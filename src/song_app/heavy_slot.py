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

**Nothing about this may stop a render.**  A deck that is unconfigured,
unreachable, busy for half an hour, or that loses the lease mid-render leaves
the render running unqueued, with a line in the song's log saying so.  Somebody
is waiting for a practice track; being slow because the host is busy is the
problem this solves, and refusing to render because a queue is down would be a
worse one.
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
    """Renew the lease until the work is done, and say so if it is lost.

    A heartbeat for a lease that is gone answers 404 rather than quietly
    reissuing one, which is the deck telling us somebody else may be running
    now.  We stop renewing and say so; we do not abandon a half-rendered video,
    because throwing away minutes of finished work helps nobody.
    """

    def __init__(self, base: str, lease: str, ttl_s: float, log: Logger) -> None:
        super().__init__(daemon=True, name=f"heavy-slot-{lease}")
        self._base, self._lease, self._log = base, lease, log
        self._interval = max(MIN_HEARTBEAT_S, ttl_s / 3.0)
        self._done = threading.Event()

    def run(self) -> None:
        while not self._done.wait(self._interval):
            try:
                response = _request(
                    "POST", f"{self._base}/api/heavy-slots/{self._lease}/heartbeat",
                    timeout=HEARTBEAT_TIMEOUT_S)
            except httpx.HTTPError as exc:
                self._log(f"Lost touch with AgentDeck while holding the heavy slot "
                          f"({exc}); carrying on.")
                return
            if response.status_code != 200:
                self._log("The heavy slot lease is gone; carrying on with the render.")
                return

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
               max_total_wait_s: float = MAX_TOTAL_WAIT_S) -> Iterator[Optional[str]]:
    """Hold a host-wide heavy slot for the body, or run without one and say so.

    Yields the lease id, or ``None`` when the work is going ahead unqueued.
    """
    base = _api_base()
    if not base:
        log("AgentDeck is not configured, so this work is not queued behind "
            "other heavy jobs on this host.")
        yield None
        return
    lease, ttl = _acquire(base, label, log, wait_s, max_total_wait_s)
    if lease is None:
        yield None
        return
    beat = _Heartbeat(base, lease, ttl, log)
    beat.start()
    try:
        yield lease
    finally:
        beat.stop()
        _release(base, lease)
