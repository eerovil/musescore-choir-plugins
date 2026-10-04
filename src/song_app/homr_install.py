"""Install or update homr from the app, by running ``scripts/install-homr.sh``.

The script stays the one install path; this only runs it where a person on a
phone can reach it (#249). So nothing here knows how homr is installed — it knows
when it is safe to run the script, how to show what it says, and whether the
fork's ``main`` has moved past the commit this host has.

Three rules, each for a reason:

- **It is a press, never automatic.** The day a parse changes has to be a day
  somebody chose, which is why the deploy never touches homr's venv. A button
  keeps that; a timer would not.
- **Not under a running job.** The script replaces files inside the venv a scan
  is running out of, so it is refused while any song is scanning, cleaning,
  rendering or uploading, and scans are refused while it runs (:func:`busy`).
- **One at a time, under one heavy slot.** An install is minutes of download and
  unpacking; a lock file holding the server pid makes a page refresh unable to
  start a second, the same way the recording and scan locks do.

Progress is a log tail the browser fetches while the install runs. The song
WebSocket is per song and this belongs to the host.
"""

from __future__ import annotations

import collections
import os
import signal
import subprocess
import threading
import time
from typing import Callable, Dict, List, Optional

from . import heavy_slot, job_state, omr, state

Logger = Callable[[str], None]

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: The installer this runs. Module-level so a test can hand it a stub.
SCRIPT = os.path.join(REPO_ROOT, "scripts", "install-homr.sh")

#: Where the fork lives; the same default as the script's own ``HOMR_REPO``.
REPO = os.getenv("HOMR_REPO", "https://github.com/eerovil/homr.git")

#: How long the fork's ``main`` commit is trusted before asking GitHub again.
LATEST_TTL_S = 600

#: A wedged-install guard, not a budget: a cold install is ~10 minutes.
TIMEOUT_S = 45 * 60

LOG_LINES = 200

#: Song jobs that run homr or would be read half-written by one being replaced.
SONG_JOBS = ("scan", "clean", "render", "upload")

_guard = threading.Lock()
_log: "collections.deque[str]" = collections.deque(maxlen=LOG_LINES)
_result: Dict[str, object] = {}
_latest: Dict[str, object] = {"at": 0.0, "commit": None}


class Refused(RuntimeError):
    """The install cannot start now; the message says why."""


def _lock_path() -> str:
    # Beside the songs, not in the venv: the venv may not exist yet.
    return os.path.join(state.SONGS_DIR, ".homr-install.lock")


def busy() -> bool:
    """True while an install started by *this* server is running.

    A lock left by a server that has since died is stale and is cleared, so a
    crash mid-install cannot leave scanning refused for good.
    """
    path = _lock_path()
    try:
        with open(path, encoding="utf-8") as f:
            pid = int(f.read().strip() or "0")
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        pid = 0
    if pid == os.getpid():
        return True
    try:
        os.remove(path)
    except OSError:
        pass
    return False


def _venv() -> str:
    """The venv the app reads homr from, which is the one to install into."""
    binary = omr.homr_binary()
    if os.path.sep in binary:
        return os.path.dirname(os.path.dirname(binary))
    return omr.DEFAULT_VENV


def _ls_remote() -> Optional[str]:
    """The commit the fork's ``main`` points at, or None when GitHub is not there."""
    try:
        out = subprocess.run(["git", "ls-remote", REPO, "refs/heads/main"],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return out.stdout.split()[0]


def latest(refresh: bool = False) -> Optional[str]:
    now = time.time()
    # A failed lookup is cached as None like any other answer: keeping the old
    # commit would show GitHub's state from before it stopped answering, and
    # asking again on every request would cost each one the lookup's timeout.
    if refresh or not _latest["at"] or now - float(_latest["at"]) > LATEST_TTL_S:
        _latest.update(at=now, commit=_ls_remote())
    return _latest["commit"]  # type: ignore[return-value]


def _running_song_jobs() -> List[str]:
    names = []
    for song in state.list_songs():
        if any(job_state.is_running(song.dir, kind) for kind in SONG_JOBS):
            names.append(song.name)
    return names


def status(refresh: bool = False) -> Dict[str, object]:
    engine = omr.default_engine()
    installed = engine.commit if engine else None
    newest = latest(refresh=refresh)
    return {
        "installed": installed,
        "label": engine.label.removeprefix("installed: ") if engine else None,
        "latest": newest,
        # Unknown, not False, when either side cannot be read.
        "up_to_date": (installed == newest) if installed and newest else None,
        "running": busy(),
        "log": list(_log),
        "result": dict(_result),
    }


def start(run_in_background: Callable[[Callable[[], None]], object]) -> None:
    """Take the lock and hand the install to ``run_in_background``.

    Raises :class:`Refused` with a sentence for the person who pressed.
    """
    with _guard:
        if busy():
            raise Refused("homr is already being installed.")
        jobs = _running_song_jobs()
        if jobs:
            raise Refused("Wait for " + ", ".join(jobs) + " to finish: a scan, clean, "
                          "render or upload is running, and installing would replace "
                          "homr underneath it.")
        os.makedirs(state.SONGS_DIR, exist_ok=True)
        with open(_lock_path(), "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        _log.clear()
        _result.clear()
    try:
        run_in_background(run)
    except Exception:
        os.remove(_lock_path())
        raise


def run() -> None:
    """The install itself. Always clears the lock and records how it ended."""
    def log(line: str) -> None:
        _log.append(line.rstrip("\n"))

    error: Optional[str] = None
    try:
        with heavy_slot.heavy_slot("homr install", log=log) as slot:
            error = _run_script(slot.guard(log))
    except heavy_slot.SlotLost as exc:
        error = f"Stopped: {exc}"
    except Exception as exc:  # recorded for the panel, never raised into a thread
        error = str(exc)
    finally:
        if error:
            log(error)
        engine = omr.default_engine()
        _result.update(ok=error is None, error=error, finished_at=time.time(),
                       installed=engine.commit if engine else None)
        latest(refresh=True)
        try:
            os.remove(_lock_path())
        except OSError:
            pass


def _run_script(log: Logger) -> Optional[str]:
    env = dict(os.environ, HOMR_VENV=_venv(), PYTHONUNBUFFERED="1")
    # An explicit source belongs to a shell; the button always installs main.
    env.pop("HOMR_SOURCE", None)
    log(f"Running {os.path.basename(SCRIPT)} into {env['HOMR_VENV']}")
    process = subprocess.Popen([SCRIPT], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, env=env, cwd=REPO_ROOT, start_new_session=True)
    timed_out = threading.Event()

    def give_up() -> None:
        timed_out.set()
        _kill(process)

    timer = threading.Timer(TIMEOUT_S, give_up)
    timer.start()
    try:
        assert process.stdout is not None
        for line in process.stdout:
            log(line)  # a lost slot raises out of here
        code = process.wait()
    except BaseException:
        _kill(process)
        raise
    finally:
        timer.cancel()
    if timed_out.is_set():
        return f"The install was still running after {TIMEOUT_S // 60} minutes and was stopped."
    if code != 0:
        return f"The install failed (exit {code}); the lines above say why."
    return None


def _kill(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
