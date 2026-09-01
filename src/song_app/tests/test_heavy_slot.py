"""Holding a host-wide heavy slot around work that would otherwise fight for cores.

The property under test is mostly the refusals: every way AgentDeck can fail to
hand out a slot has to leave the work running anyway, because the render is what
somebody is waiting for and the queue is only an optimisation of the host.
"""
import threading
import time

import pytest

pytest.importorskip("httpx")

import httpx

from src.song_app import heavy_slot


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setenv("AGENTDECK_API_URL", "http://deck.test")
    monkeypatch.delenv("AGENTDECK_URL", raising=False)
    monkeypatch.setattr(heavy_slot, "RETRY_PAUSE_S", 0.0)


class Deck:
    """A fake AgentDeck: a scripted answer per acquire, and a call log."""

    def __init__(self, acquires, heartbeat_status=200):
        self._acquires = list(acquires)
        self._heartbeat_status = heartbeat_status
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, method, url, **kwargs):
        with self.lock:
            self.calls.append((method, url))
        if method == "POST" and url.endswith("/api/heavy-slots"):
            answer = self._acquires.pop(0) if len(self._acquires) > 1 \
                else self._acquires[0]
            if isinstance(answer, Exception):
                raise answer
            status, body = answer
            return httpx.Response(status, request=httpx.Request(method, url), **body)
        if url.endswith("/heartbeat"):
            return httpx.Response(self._heartbeat_status,
                                  request=httpx.Request(method, url))
        return httpx.Response(200, json={"released": True},
                              request=httpx.Request(method, url))

    def of(self, kind):
        with self.lock:
            calls = list(self.calls)
        if kind == "acquire":
            return [c for c in calls if c[0] == "POST" and c[1].endswith("/heavy-slots")]
        if kind == "heartbeat":
            return [c for c in calls if c[1].endswith("/heartbeat")]
        return [c for c in calls if c[0] == "DELETE"]


def _deck(monkeypatch, acquires, **kwargs):
    deck = Deck(acquires, **kwargs)
    monkeypatch.setattr(heavy_slot, "_request", deck)
    return deck


GRANTED = (200, {"json": {"lease": "abc123", "ttl_s": 120.0}})


def test_holds_a_lease_for_the_work_and_gives_it_back(monkeypatch):
    deck = _deck(monkeypatch, [GRANTED])
    with heavy_slot.heavy_slot("render mysong") as slot:
        assert slot.lease == "abc123" and slot.held
        assert deck.of("release") == []  # still held while the work runs
    assert deck.of("release") == [("DELETE", "http://deck.test/api/heavy-slots/abc123")]


def test_the_slot_is_asked_for_by_name(monkeypatch):
    sent = {}

    def deck(method, url, **kwargs):
        sent.update(kwargs.get("json") or {})
        return httpx.Response(200, json={"lease": "abc123", "ttl_s": 120.0},
                              request=httpx.Request(method, url))

    monkeypatch.setattr(heavy_slot, "_request", deck)
    with heavy_slot.heavy_slot("render mysong"):
        pass
    assert sent["label"] == "render mysong"


def test_the_lease_is_released_when_the_work_raises(monkeypatch):
    deck = _deck(monkeypatch, [GRANTED])
    with pytest.raises(RuntimeError):
        with heavy_slot.heavy_slot("render mysong"):
            raise RuntimeError("the render failed")
    assert len(deck.of("release")) == 1


def test_a_busy_host_is_waited_out(monkeypatch):
    logged = []
    deck = _deck(monkeypatch, [(503, {}), (503, {}), GRANTED])
    with heavy_slot.heavy_slot("render mysong", log=logged.append) as slot:
        assert slot.lease == "abc123"
    assert len(deck.of("acquire")) == 3
    assert any("Waiting" in line for line in logged)
    # Said once, however long the wait: this goes to the song's live log.
    assert sum("Waiting" in line for line in logged) == 1


def test_a_host_that_never_frees_up_renders_anyway(monkeypatch):
    logged = []
    deck = _deck(monkeypatch, [(503, {})])
    ran = False
    with heavy_slot.heavy_slot("render mysong", log=logged.append,
                               max_total_wait_s=0.0) as slot:
        assert not slot.held and slot.lease is None
        slot.check()      # nothing to lose, so nothing that can stop the work
        ran = True
    assert ran
    assert deck.of("release") == []
    assert any("rendering anyway" in line for line in logged)


def test_an_unreachable_deck_renders_anyway(monkeypatch):
    logged = []
    _deck(monkeypatch, [httpx.ConnectError("no route")])
    with heavy_slot.heavy_slot("render mysong", log=logged.append) as slot:
        assert not slot.held
    assert any("Could not reach AgentDeck" in line for line in logged)


def test_a_refusal_renders_anyway(monkeypatch):
    logged = []
    _deck(monkeypatch, [(500, {})])
    with heavy_slot.heavy_slot("render mysong", log=logged.append) as slot:
        assert not slot.held
    assert any("HTTP 500" in line for line in logged)


def test_an_unconfigured_deck_renders_anyway(monkeypatch):
    logged = []
    monkeypatch.delenv("AGENTDECK_API_URL", raising=False)
    called = []
    monkeypatch.setattr(heavy_slot, "_request",
                        lambda *a, **k: called.append(a) or httpx.Response(200))
    with heavy_slot.heavy_slot("render mysong", log=logged.append) as slot:
        assert not slot.held
    assert called == []
    assert any("not configured" in line for line in logged)


def test_the_lease_is_renewed_while_the_work_runs(monkeypatch):
    monkeypatch.setattr(heavy_slot, "MIN_HEARTBEAT_S", 0.01)
    deck = _deck(monkeypatch, [(200, {"json": {"lease": "abc123", "ttl_s": 0.03}})])
    with heavy_slot.heavy_slot("render mysong"):
        for _ in range(200):
            if deck.of("heartbeat"):
                break
            time.sleep(0.01)
    assert deck.of("heartbeat"), "the lease was never renewed"
    assert deck.of("heartbeat")[0][1].endswith("/api/heavy-slots/abc123/heartbeat")
    # Renewing stops when the work does, rather than outliving it on a thread.
    before = len(deck.of("heartbeat"))
    time.sleep(0.1)
    assert len(deck.of("heartbeat")) == before


def test_a_lost_lease_stops_the_work(monkeypatch):
    """404 means the deck may already have handed this slot to somebody else, so
    carrying on would be exactly the two-jobs-on-four-cores case the queue exists
    to prevent. The work is stopped where it next reports progress."""
    monkeypatch.setattr(heavy_slot, "MIN_HEARTBEAT_S", 0.01)
    logged = []
    deck = _deck(monkeypatch, [(200, {"json": {"lease": "abc123", "ttl_s": 0.03}})],
                 heartbeat_status=404)
    steps = []

    with pytest.raises(heavy_slot.SlotLost):
        with heavy_slot.heavy_slot("render mysong", log=logged.append) as slot:
            report = slot.guard(lambda m: steps.append(m))
            for n in range(200):          # the work, reporting as it goes
                report(f"step {n}")
                time.sleep(0.01)

    assert steps, "the work should have run until the lease went"
    assert len(steps) < 200, "the work carried on after the slot was lost"
    assert not slot.held and slot.lost()
    assert deck.of("release"), "the lease is still given back"
    assert any("no longer held" in line for line in logged)


def test_a_heartbeat_that_cannot_be_sent_is_retried_before_giving_up(monkeypatch):
    """A deck that is restarting has told us nothing about who owns the slot;
    only going a whole lease-length without renewing has."""
    monkeypatch.setattr(heavy_slot, "MIN_HEARTBEAT_S", 0.01)
    logged = []

    def deck(method, url, **kwargs):
        request = httpx.Request(method, url)
        if url.endswith("/heartbeat"):
            raise httpx.ConnectError("deck restarting")
        if method == "POST":
            return httpx.Response(200, request=request,
                                  json={"lease": "abc123", "ttl_s": 0.6})
        return httpx.Response(200, request=request, json={})

    monkeypatch.setattr(heavy_slot, "_request", deck)
    with heavy_slot.heavy_slot("render mysong", log=logged.append) as slot:
        for _ in range(100):      # a lease that has not expired is still ours
            slot.check()
            if any("trying again" in line for line in logged):
                break
            time.sleep(0.02)
        assert slot.held
    assert any("trying again" in line for line in logged), \
        "a heartbeat that could not be sent should be retried, not taken as loss"


def test_a_lease_that_cannot_be_renewed_for_its_whole_life_is_lost(monkeypatch):
    monkeypatch.setattr(heavy_slot, "MIN_HEARTBEAT_S", 0.01)
    logged = []

    def deck(method, url, **kwargs):
        request = httpx.Request(method, url)
        if url.endswith("/heartbeat"):
            raise httpx.ConnectError("deck gone")
        if method == "POST":
            return httpx.Response(200, request=request,
                                  json={"lease": "abc123", "ttl_s": 0.05})
        return httpx.Response(200, request=request, json={})

    monkeypatch.setattr(heavy_slot, "_request", deck)
    with pytest.raises(heavy_slot.SlotLost):
        with heavy_slot.heavy_slot("render mysong", log=logged.append) as slot:
            for _ in range(200):
                slot.check()
                time.sleep(0.01)
    assert any("lease lasts" in line for line in logged)
