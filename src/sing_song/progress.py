"""Where a sing_song.py run says how it is going, and which singer version is current (#453).

A run is an agent's `agentdeck job`, not the app's, so the app learns about it from a file:
songs/<slug>/.sing-run.json, written whole (by rename) at every step. The song list reads it.

    {"state": "waiting" | "bundle" | "singing" | "publishing" | "done" | "failed",
     "started": t, "updated": t, "finished": t, "pid": n, "version": SINGER_VERSION,
     "choirs": [...], "remote": true when only the remote runner sings (--remote-only),
     "sing": false for a piano-only run (--no-sing), which is no re-sing,
     "part": i, "parts": n, "part_name": "T2", "choir": "jm", "error": "..."}

Stdlib only: the app imports it too.
"""
import json
import os
import re
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SINGER = os.path.join(HERE, "sing_ufr.py")
RUN_FILE = ".sing-run.json"
RUNNING = ("bundle", "singing", "publishing")   # states a live process holds


def singer_version(path=SINGER):
    """SINGER_VERSION in sing_ufr.py, read as text: the singer runs in its own venv and reads its
    arguments as it loads, so it cannot be imported here."""
    with open(path, encoding="utf-8") as f:
        m = re.search(r"^SINGER_VERSION\s*=\s*(\d+)\s*$", f.read(), re.M)
    if not m:
        raise ValueError(f"no SINGER_VERSION line in {path}")
    return int(m.group(1))


def write(song_dir, record):
    """Replace the run's record with `record` (plus the time), in one rename."""
    record = dict(record, updated=time.time())
    path = os.path.join(song_dir, RUN_FILE)
    fd, tmp = tempfile.mkstemp(prefix=RUN_FILE + ".", suffix=".tmp", dir=song_dir)   # one per write
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return record


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, TypeError, ValueError, OverflowError):
        return False
    return True


def read(song_dir):
    """The last run's record, or None. A run whose process is gone without saying how it ended
    (killed, the host restarted) reads as failed."""
    try:
        with open(os.path.join(song_dir, RUN_FILE), encoding="utf-8") as f:
            record = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    if record.get("state") in RUNNING and not alive(record.get("pid")):
        record = dict(record, state="failed", finished=record.get("updated"),
                      error="the run stopped without finishing (killed, or the machine restarted)")
    return record


class Run:
    """One sing_song.py run's record, kept up to date as it goes."""

    def __init__(self, song_dir, choirs=(), version=None, remote=False, sing=True):
        self.song_dir = song_dir
        self.lock = threading.Lock()   # the singers here and on the runner report from two threads
        self.record = {"state": "bundle", "started": time.time(), "pid": os.getpid(),
                       "version": version, "choirs": list(choirs), "remote": bool(remote),
                       "sing": bool(sing)}

    def step(self, state, **fields):
        with self.lock:
            self.record = dict(self.record, state=state, **fields)
            if os.path.isdir(self.song_dir):
                write(self.song_dir, self.record)

    def finish(self, error=None):
        if error is None:
            self.step("done", finished=time.time())
        else:
            self.step("failed", finished=time.time(), error=error)


def mark_waiting(song_dir, choirs=()):
    """Before a run is queued: the song shows as waiting until the run starts."""
    return write(song_dir, {"state": "waiting", "queued": time.time(), "choirs": list(choirs)})
