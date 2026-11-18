"""Where a sing_song.py run says how it is going, and which singer version is current (#453).

A run is an agent's `agentdeck job`, not the app's, so the app learns about it from a file:
songs/<slug>/.sing-run.json, written whole (by rename) at every step. The song list reads it.

    {"state": "waiting" | "bundle" | "singing" | "publishing" | "done" | "failed",
     "started": t, "updated": t, "finished": t, "pid": n, "version": SINGER_VERSION,
     "choirs": [...], "remote": true when only the remote runner sings (--remote-only),
     "sing": false for a piano-only run (--no-sing), which is no re-sing,
     "part": i, "parts": n, "part_name": "T2", "choir": "jm", "error": "...",
     "choir_outcomes": {"jm": "published" | "refused-sung" | "failed" | "not-tried"},
     "run_id": the app's id for a run it started (SING_RUN_ID, #510),
     "for": "graphics card" while a started run waits for the card}

A full run holds a lock on the song (run_lock()) from before its first write to the end, so
two runs never sing or publish one song at once (#510); the lock and the app's own files for a
run live outside songs/, in state_dir(slug).

A piano-only run does not replace a sung version on the site without --piano-over-sung
(#483), so one choir of several may be refused while the others are published;
"choir_outcomes" says which.

Stdlib only: the app imports it too.
"""
import ast
import fcntl
import json
import os
import pwd
import re
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SINGER = os.path.join(HERE, "sing_ufr.py")
RUN_FILE = ".sing-run.json"
RUNNING = ("bundle", "singing", "publishing")   # states a live process holds


def state_dir(slug):
    """~/.local/state/musescore-choir-plugins/sing/<slug>/: the song lock and the app's files for
    the runs it starts (#510). The home is read from the password file, not $HOME, like the song
    locks, so the app's service and a shell agree."""
    base = os.environ.get("SING_STATE_DIR")
    if not base:
        home = pwd.getpwuid(os.getuid()).pw_dir
        base = os.path.join(home, ".local", "state", "musescore-choir-plugins", "sing")
    return os.path.join(base, slug)


def run_lock(slug):
    """Take the song's run lock without waiting: an open file descriptor that holds it until it
    is closed (or the process ends), or None when another run holds it."""
    folder = state_dir(slug)
    os.makedirs(folder, exist_ok=True)
    fd = os.open(os.path.join(folder, "run.lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def run_locked(slug):
    """True when a run holds the song's lock now."""
    fd = run_lock(slug)
    if fd is None:
        return True
    os.close(fd)
    return False


def singer_version(path=SINGER):
    """SINGER_VERSION in sing_ufr.py, read as text: the singer runs in its own venv and reads its
    arguments as it loads, so it cannot be imported here."""
    with open(path, encoding="utf-8") as f:
        m = re.search(r"^SINGER_VERSION\s*=\s*(\d+)\s*$", f.read(), re.M)
    if not m:
        raise ValueError(f"no SINGER_VERSION line in {path}")
    return int(m.group(1))


def singer_changes(path=SINGER):
    """SINGER_CHANGES in sing_ufr.py, read as text: {version: "*" or a language code or a list of
    them}, every version up to SINGER_VERSION. A version the line does not name, or a line that
    cannot be read, counts as "*" (it changed every song), so a mistake re-sings too much rather
    than too little (#484). A language code covers its variants ("la" covers "la-de",
    languages.chain), as resing.up_to_date reads it."""
    version = singer_version(path)
    changes = {}
    try:
        with open(path, encoding="utf-8") as f:
            m = re.search(r"^SINGER_CHANGES\s*=\s*(\{.*\})\s*$", f.read(), re.M)
        read = ast.literal_eval(m.group(1)) if m else {}
        if isinstance(read, dict):
            changes = read
    except (OSError, ValueError, SyntaxError, TypeError):
        changes = {}
    out = {}
    for v in range(1, version + 1):
        said = changes.get(v, "*")
        if isinstance(said, str):
            said = [said]
        if not isinstance(said, (list, tuple)) or not said or not all(isinstance(c, str) for c in said):
            said = ["*"]
        out[v] = "*" if "*" in said else sorted(said)
    return out


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
    # a "waiting" record with a pid is a started run waiting for the card (#510); one without
    # is --waiting's, before any run started, and keeps its meaning
    gone = record.get("state") in RUNNING or (record.get("state") == "waiting" and "pid" in record)
    if gone and not alive(record.get("pid")):
        record = dict(record, state="failed", finished=record.get("updated"),
                      error="the run stopped without finishing (killed, or the machine restarted)")
    return record


class Run:
    """One sing_song.py run's record, kept up to date as it goes."""

    def __init__(self, song_dir, choirs=(), version=None, remote=False, sing=True, kept=True):
        self.song_dir = song_dir
        self.kept = kept   # False: nothing is written (a --try phrase, #483)
        self.lock = threading.Lock()   # the singers here and on the runner report from two threads
        self.record = {"state": "bundle", "started": time.time(), "pid": os.getpid(),
                       "version": version, "choirs": list(choirs), "remote": bool(remote),
                       "sing": bool(sing)}
        if os.environ.get("SING_RUN_ID"):
            self.record["run_id"] = os.environ["SING_RUN_ID"]   # the app's run (#510)
        self.finished = False

    def choir_outcome(self, choir, outcome):
        """How publishing to `choir` went: published, refused-sung, failed or not-tried."""
        with self.lock:
            outcomes = dict(self.record.get("choir_outcomes") or {}, **{choir: outcome})
        self.step(self.record["state"], choir_outcomes=outcomes)

    def step(self, state, **fields):
        with self.lock:
            self.record = dict(self.record, state=state, **fields)
            if self.kept and os.path.isdir(self.song_dir):
                write(self.song_dir, self.record)

    def finish(self, error=None):
        """End the record; only the first call counts, so a run that said why it stopped keeps it."""
        if self.finished:
            return
        self.finished = True
        if error is None:
            self.step("done", finished=time.time())
        else:
            self.step("failed", finished=time.time(), error=error)


def mark_waiting(song_dir, choirs=()):
    """Before a run is queued: the song shows as waiting until the run starts."""
    return write(song_dir, {"state": "waiting", "queued": time.time(), "choirs": list(choirs)})
