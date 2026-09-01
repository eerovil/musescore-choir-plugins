"""Take one of this host's heavy slots while the video render runs.

Rendering a practice video is minutes of every core the machine has. So is an
agent's test suite, a scan, or a second song rendering at the same time, and
this host has four cores and no notion of whose turn it is — three of them at
once produce one slow render and two slow suites rather than any of them
finishing sooner.

AgentDeck already owns that queue: a small pool of ``flock``ed files under
``/run/user/<uid>``, one heavy slot by default, which its own agents take
through a PATH shim and a ``PreToolUse`` hook.  Neither of those can reach us —
the song app is a systemd service, with no shell the deck controls — so it
exposes the pool over HTTP instead (``POST /api/heavy-slots``, heartbeat,
``DELETE``).  The caller waits its turn, does its own work, and gives the slot
back; the deck never learns what ran.

Two decisions worth stating, because both are the opposite of what a queue
usually does:

**A lease has to be renewed.**  A ``flock`` dies with the process holding it, so
a killed render cannot strand a slot.  An HTTP lease can, and there is only one
to strand, so it expires unless heartbeated and a reaper takes it back.  Hence
the background thread here.

**Failing to get a slot is fail-open; losing one is not.**  A deck that is
unreachable, refusing or busy for half an hour has told us nothing about what
else is running, so the render goes ahead unqueued with a line in the song's log
saying so (a host with no deck configured at all says nothing, since a queue
that does not exist is not news on every render) — somebody is waiting for a practice track, and refusing to
render because a queue is down would be worse than being slow.  A heartbeat that
answers 404 is the opposite: the deck is saying this lease is no longer held, so
the slot may already have been handed to somebody else.  Carrying on there would
be the one thing the queue exists to prevent — two jobs on four cores, each
believing it has them.  So a lost lease stops the work (``SlotLost``), rather
than being a line in a log nobody reads.
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator, Optional, Tuple

import httpx

# The deck holds an acquire open for up to `wait_s` before answering "busy", so
# the request timeout has to outlast that rather than cut it short.
WAIT_S = 60.0
REQUEST_TIMEOUT_S = WAIT_S + 10.0
HEARTBEAT_TIMEOUT_S = 10.0
# How long a render will queue before going ahead anyway.  Long enough that an
# ordinary suite or another song's render ahead of it is simply waited out.
MAX_TOTAL_WAIT_S = 1800.0
# Only reached when the deck answered "busy" faster than it should have; the
# open request is what normally paces this loop.
RETRY_PAUSE_S = 1.0
MIN_HEARTBEAT_S = 5.0
DEFAULT_TTL_S = 120.0

Logger = Callable[[str], None]


def _noop(_message: str) -> None:
    pass


class SlotLost(RuntimeError):
    """The lease this work was running under is not held any more."""


class Slot:
    """What the work is running under: a lease, or nothing and permission anyway.

    ``guard`` is how a long call is stopped part-way.  A render is one function
    call lasting minutes, so the only places it can be interrupted are the ones
    where it already reports progress — which it does roughly every percent of
    the encode, i.e. seconds apart.  Wrapping those callbacks turns "the lease is
    gone" into an exception raised inside the render, instead of a flag nobody
    is looking at.
    """

    def __init__(self, lease: Optional[str] = None) -> None:
        self.lease = lease
        self._lost = threading.Event()

    @property
    def held(self) -> bool:
        """True while this work holds a slot.  False when it is running unqueued."""
        return self.lease is not None and not self._lost.is_set()

    def lost(self) -> bool:
        return self._lost.is_set()

    def _lose(self) -> None:
        self._lost.set()

    def check(self) -> None:
        if self._lost.is_set():
            raise SlotLost(
                "The host-wide heavy slot is no longer held, so another heavy job "
                "may already have started; stopped rather than compete with it.")

    def guard(self, callback: Logger) -> Logger:
        """The callback, plus "and stop if the slot has gone" before each call."""
        def guarded(message: str) -> None:
            self.check()
            callback(message)
        return guarded


def _api_base() -> str:
    """Where to reach AgentDeck's machine API, or "" when it is not configured.

    ``AGENTDECK_API_URL`` is the loopback URL for the common reverse-proxy setup
    and ``AGENTDECK_URL`` the browser one; either can serve the API, so the
    second is a fallback rather than a separate thing.
    """
    for name in ("AGENTDECK_API_URL", "AGENTDECK_URL"):
        base = (os.getenv(name) or "").strip().rstrip("/")
        if base:
            return base
    return ""


def _request(method: str, url: str, **kwargs) -> httpx.Response:
    """Small seam for tests; the render runs in a thread, so this is sync."""
    return httpx.request(method, url, **kwargs)


def _acquire(base: str, label: str, log: Logger, wait_s: float,
             max_total_wait_s: float) -> Tuple[Optional[str], float]:
    """Wait for a slot.  Returns (lease id, ttl) or (None, 0) to run unqueued."""
    deadline = time.monotonic() + max_total_wait_s
    said_waiting = False
    while True:
        started = time.monotonic()
        try:
            response = _request("POST", f"{base}/api/heavy-slots",
                                json={"label": label, "wait_s": wait_s},
                                timeout=REQUEST_TIMEOUT_S)
        except httpx.HTTPError as exc:
            log(f"Could not reach AgentDeck for a heavy slot ({exc}); rendering anyway.")
            return None, 0.0
        if response.status_code == 503:  # every slot busy — an answer, not a fault
            if time.monotonic() >= deadline:
                log("No heavy slot came free; rendering anyway.")
                return None, 0.0
            if not said_waiting:
                said_waiting = True
                log("Waiting for a free heavy slot on this host…")
            pause = RETRY_PAUSE_S - (time.monotonic() - started)
            if pause > 0:
                time.sleep(pause)
            continue
        if response.status_code != 200:
            log(f"AgentDeck refused a heavy slot (HTTP {response.status_code}); "
                "rendering anyway.")
            return None, 0.0
        try:
            body = response.json()
            lease = str(body.get("lease") or "")
            ttl = float(body.get("ttl_s") or DEFAULT_TTL_S)
        except (ValueError, AttributeError, TypeError):
            lease, ttl = "", DEFAULT_TTL_S
        if not lease:
            log("AgentDeck granted a heavy slot without a lease; rendering anyway.")
            return None, 0.0
        if said_waiting:
            log("The heavy slot is free; starting.")
        return lease, ttl


class _Heartbeat(threading.Thread):
    """Renew the lease until the work is done, and give up the slot if it is lost.

    A heartbeat for a lease that is gone answers 404 rather than quietly
    reissuing one, precisely so a caller can tell "still mine" from "someone else
    may be running now".  Acting on that means marking the slot lost, which stops
    the work at its next progress report.

    A heartbeat that could not be *sent* is a weaker fact than a 404 — the deck
    may simply be restarting — so it is retried.  But only until the lease it was
    renewing would have expired anyway: past that the deck has given the slot to
    whoever asked next, and not hearing about it is not the same as it not having
    happened.
    """

    def __init__(self, base: str, slot: Slot, ttl_s: float, log: Logger) -> None:
        super().__init__(daemon=True, name=f"heavy-slot-{slot.lease}")
        self._base, self._slot, self._log = base, slot, log
        self._ttl_s = ttl_s
        self._interval = max(MIN_HEARTBEAT_S, ttl_s / 3.0)
        self._done = threading.Event()

    def _lose(self, why: str) -> None:
        self._log(f"{why} Stopping the render rather than competing for the host.")
        self._slot._lose()

    def run(self) -> None:
        renewed_at = time.monotonic()
        while not self._done.wait(self._interval):
            try:
                response = _request(
                    "POST", f"{self._base}/api/heavy-slots/{self._slot.lease}/heartbeat",
                    timeout=HEARTBEAT_TIMEOUT_S)
            except httpx.HTTPError as exc:
                if time.monotonic() - renewed_at >= self._ttl_s:
                    self._lose("The heavy slot could not be renewed for longer than "
                               f"its lease lasts ({exc}).")
                    return
                self._log(f"Could not reach AgentDeck to renew the heavy slot "
                          f"({exc}); trying again.")
                continue
            if response.status_code != 200:
                self._lose("The heavy slot is no longer held — another heavy job "
                           "may already have it.")
                return
            renewed_at = time.monotonic()

    def stop(self) -> None:
        self._done.set()
        self.join(timeout=5.0)


def _release(base: str, lease: str) -> None:
    try:
        _request("DELETE", f"{base}/api/heavy-slots/{lease}",
                 timeout=HEARTBEAT_TIMEOUT_S)
    except httpx.HTTPError:
        pass  # the lease expires on its own; a failed release is not worth a log line


@contextmanager
def heavy_slot(label: str, *, log: Logger = _noop, wait_s: float = WAIT_S,
               max_total_wait_s: float = MAX_TOTAL_WAIT_S) -> Iterator[Slot]:
    """Hold a host-wide heavy slot for the body, or run without one and say so.

    Yields a `Slot`: pass its `guard` around the work's progress callbacks and
    losing the lease stops the work.  A slot that was never granted is never
    lost, so an unqueued run is never interrupted.
    """
    base = _api_base()
    if not base:
        # A host with no deck configured is the ordinary case elsewhere, so this
        # is silent: a line about a queue that does not exist, on every render,
        # would be noise in the one log a person actually reads.
        yield Slot()
        return
    lease, ttl = _acquire(base, label, log, wait_s, max_total_wait_s)
    if lease is None:
        yield Slot()
        return
    slot = Slot(lease)
    beat = _Heartbeat(base, slot, ttl, log)
    beat.start()
    try:
        yield slot
    finally:
        beat.stop()
        _release(base, lease)
