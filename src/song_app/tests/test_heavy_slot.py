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
    with heavy_slot.heavy_slot("render mysong") as lease:
        assert lease == "abc123"
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
    with heavy_slot.heavy_slot("render mysong", log=logged.append) as lease:
        assert lease == "abc123"
    assert len(deck.of("acquire")) == 3
    assert any("Waiting" in line for line in logged)
    # Said once, however long the wait: this goes to the song's live log.
    assert sum("Waiting" in line for line in logged) == 1


def test_a_host_that_never_frees_up_renders_anyway(monkeypatch):
    logged = []
    deck = _deck(monkeypatch, [(503, {})])
    ran = False
    with heavy_slot.heavy_slot("render mysong", log=logged.append,
                               max_total_wait_s=0.0) as lease:
        assert lease is None
        ran = True
    assert ran
    assert deck.of("release") == []
    assert any("rendering anyway" in line for line in logged)


def test_an_unreachable_deck_renders_anyway(monkeypatch):
    logged = []
    _deck(monkeypatch, [httpx.ConnectError("no route")])
    with heavy_slot.heavy_slot("render mysong", log=logged.append) as lease:
        assert lease is None
    assert any("Could not reach AgentDeck" in line for line in logged)


def test_a_refusal_renders_anyway(monkeypatch):
    logged = []
    _deck(monkeypatch, [(500, {})])
    with heavy_slot.heavy_slot("render mysong", log=logged.append) as lease:
        assert lease is None
    assert any("HTTP 500" in line for line in logged)


def test_an_unconfigured_deck_renders_anyway(monkeypatch):
    logged = []
    monkeypatch.delenv("AGENTDECK_API_URL", raising=False)
    called = []
    monkeypatch.setattr(heavy_slot, "_request",
                        lambda *a, **k: called.append(a) or httpx.Response(200))
    with heavy_slot.heavy_slot("render mysong", log=logged.append) as lease:
        assert lease is None
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


def test_a_lost_lease_does_not_stop_the_work(monkeypatch):
    """404 means somebody else may be running; a half-rendered video is still
    worth finishing, so it is said and not acted on."""
    monkeypatch.setattr(heavy_slot, "MIN_HEARTBEAT_S", 0.01)
    logged = []
    deck = _deck(monkeypatch, [(200, {"json": {"lease": "abc123", "ttl_s": 0.03}})],
                 heartbeat_status=404)
    finished = False
    with heavy_slot.heavy_slot("render mysong", log=logged.append):
        for _ in range(200):
            if deck.of("heartbeat"):
                break
            time.sleep(0.01)
        finished = True
    assert finished
    assert any("lease is gone" in line for line in logged)
