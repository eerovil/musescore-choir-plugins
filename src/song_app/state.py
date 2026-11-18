"""Song state machine — the .song.json file is the UX.

A Song is a folder `songs/<slug>/` plus a `.song.json` state file. The folder is a
slug; the human display name lives in the state file. See DESIGN.md.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import logging
import os
import pwd
import re
import threading
import time
import unicodedata
from typing import Dict, List, Optional

from . import health
from . import job_state
from .job_state import SongGone

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SONGS_DIR = os.path.join(SCRIPT_DIR, "songs")
STATE_FILE = ".song.json"
# Deleted songs are moved here rather than removed (#428), so a mistake is undone by
# moving the folder back. Inside songs/ so the move is one rename on one disk, and
# the nightly backup keeps a copy. A dot folder, so nothing lists it as a song.
TRASH_DIR_NAME = ".trash"

# Linear stages; `fix` and `lyrics` form a loop (lyric overflow can re-open fix).
#
# `scan` reads a score off its PDF one printed system at a time, and a song only
# passes through it when it was registered with a PDF and no score. Registering a
# score is still the manual route (#86) and starts at `clean` -- which is also
# where every song that predates this stage sits, so the rail reads `scan` as
# behind them and satisfied. Having an input score *is* what being past scanning
# means; nothing has to be recorded to say so.
#
# A song ends at `publish`: the score, its MIDI and its timing go to the
# stemmanauhat site, which draws and plays them itself (#400). The `record` and
# `upload` stages that made and uploaded videos are gone, and a song left on
# either is read as being on `publish` (`_upgrade`).
STAGES = ["register", "scan", "clean", "fix", "lyrics", "review", "publish"]
RETIRED_STAGES = ("record", "upload")


def slugify(name: str) -> str:
    """Turn a human song name into a filesystem-safe slug ('Laulun aika' -> 'laulun-aika')."""
    norm = unicodedata.normalize("NFKD", name)
    norm = norm.encode("ascii", "ignore").decode("ascii")
    norm = norm.lower()
    norm = re.sub(r"[^a-z0-9]+", "-", norm).strip("-")
    return norm or "song"


def _ensure_songs_dir() -> None:
    os.makedirs(SONGS_DIR, exist_ok=True)


def song_dir(slug: str) -> str:
    return os.path.join(SONGS_DIR, slug)


def file_fingerprint(path: str) -> Optional[str]:
    """sha1 of a file's contents, or None if it doesn't exist."""
    if not path or not os.path.exists(path):
        return None
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha1:" + h.hexdigest()


log = logging.getLogger(__name__)

# The dicts a save merges key by key rather than writing whole (#498). Everything
# else is merged a top-level key at a time: a record such as `health`
# (`{checked_against, issues}`) is written by one writer as one unit, and stitching
# it from two would make a record nobody wrote. `publish` holds the Publish panel's
# settings beside the publish job's `sites`, and `sites` one record per choir, each
# written by a different writer (the app, `sing_song.py --publish`).
MERGED = {"publish": {"sites": {}}}

_MISSING = object()


def _merge(mine: Dict, base: Dict, disk: Dict, deeper: Dict) -> None:
    """Merge into `mine`, in place: what this copy changed since `base` stays,
    everything else becomes the disk's.

    In place, so a reference taken into `mine` before a save (`pub =
    song.data.setdefault("publish", {})`) still points into the song afterwards.
    """
    for key in set(mine) | set(base) | set(disk):
        have, was = mine.get(key, _MISSING), base.get(key, _MISSING)
        theirs = disk.get(key, _MISSING)
        if key in deeper and isinstance(have, dict) and isinstance(theirs, dict):
            _merge(have, was if isinstance(was, dict) else {}, theirs, deeper[key])
            continue
        if have != was:
            continue                      # this copy's change, written whole
        if theirs is _MISSING:
            mine.pop(key, None)
        elif isinstance(have, dict) and isinstance(theirs, dict):
            have.clear()
            have.update(copy.deepcopy(theirs))
        else:
            mine[key] = copy.deepcopy(theirs)


class Song:
    """In-memory view of a song's .song.json, with helpers to resolve its files.

    A loaded song remembers what it was loaded with (`_base`), so a save writes
    only what this copy changed and keeps what another writer saved meanwhile
    (#498). A long job (a clean, a scan) holds its copy for minutes.
    """

    def __init__(self, slug: str, data: Dict):
        self.slug = slug
        self._data = data
        self._base: Optional[Dict] = None
        self._rebound = False
        self._loaded_raw: Optional[str] = None

    @property
    def data(self) -> Dict:
        return self._data

    @data.setter
    def data(self, value: Dict) -> None:
        # Someone else's dict: the base no longer says what this copy changed.
        # `adopt` is the way to take another copy's data with its base.
        self._data = value
        if self._base is not None:
            self._rebound = True
        self._base = None

    def adopt(self, other: "Song") -> None:
        """Take `other`'s data and what it was loaded with."""
        if other is self:
            return
        self._data = other._data
        self._base = copy.deepcopy(other._base) if other._base is not None else None
        self._rebound = other._rebound
        self._loaded_raw = other._loaded_raw

    def _loaded(self, raw: str) -> None:
        self._base = copy.deepcopy(self._data)
        self._rebound = False
        self._loaded_raw = raw

    # ---- paths -----------------------------------------------------------
    @property
    def dir(self) -> str:
        return song_dir(self.slug)

    def path(self, *parts: str) -> str:
        return os.path.join(self.dir, *parts)

    def state_path(self) -> str:
        return self.path(STATE_FILE)

    def source_path(self, kind: str) -> Optional[str]:
        rel = self.data.get("sources", {}).get(kind)
        return self.path(rel) if rel else None

    def cleaned_path(self) -> Optional[str]:
        rel = self.data.get("cleaned")
        return self.path(rel) if rel else None

    def lyrics_json_path(self) -> Optional[str]:
        rel = self.data.get("lyrics", {}).get("json")
        return self.path(rel) if rel else None

    # ---- accessors -------------------------------------------------------
    @property
    def name(self) -> str:
        return self.data.get("name", self.slug)

    @property
    def stage(self) -> str:
        return self.data.get("stage", "register")

    @property
    def mode(self) -> str:
        return self.data.get("mode", "normal")

    def published(self) -> List[str]:
        """The choirs whose lists the site shows this song in, sorted.

        Read through `publish.records`, so a song published before #384 kept its
        one record under `site` and still counts as published.
        """
        from . import publish  # noqa: PLC0415 - publish is a leaf; keep state's imports light
        return sorted(publish.records(self.data.get("publish")))

    def set_stage(self, stage: str) -> None:
        self.data["stage"] = stage

    # ---- persistence -----------------------------------------------------
    def save(self) -> None:
        """Write what this copy changed, under the song's lock and in one rename.

        The rename means a reader never sees half a file. The lock is what lets a
        writer that only owns a few fields (`server._rescan`) re-read and write in
        one step without another save landing in between (#252), and it is held
        across processes, so `sing_song.py` is kept apart from the app (#498).

        The file is read again and merged (`_merge`): a top-level record this copy
        changed since it was loaded is written whole, every other one is kept as
        it is on disk now, and `MERGED` names the two dicts merged a key deeper.
        A song never loaded writes whole.
        """
        with song_lock(self.slug):
            self.check_still_here()
            raw = _read_raw(self.state_path())
            disk = None
            if raw is not None:
                try:
                    disk = _upgrade(json.loads(raw))
                except ValueError:
                    disk = None
            if disk is not None and self._base is not None:
                _merge(self._data, self._base, disk, MERGED)
            elif disk is not None and self._rebound:
                if raw == self._loaded_raw:
                    pass    # nothing saved since this copy was loaded: whole is right
                else:
                    log.warning("%s: saving a rebound copy whole over a state saved "
                                "since it was loaded (use Song.adopt)", self.slug)
            self._data["updated_at"] = time.time()
            text = json.dumps(self._data, indent=2, ensure_ascii=False)
            tmp = self.state_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, self.state_path())
            self._loaded(text)

    def check_still_here(self) -> None:
        """Raise `SongGone` unless this copy is still the song on disk (#428).

        Call it under `song_lock`. Deleting moves the folder and `create` claims a
        slug under that lock, so a copy loaded before a delete can neither put the
        deleted song back, nor save over or start a job on a new song that has
        since taken the same slug (told apart by `created_at`). Folders are made by
        `create` and `import_legacy`, never by a save.
        """
        if not os.path.isdir(self.dir):
            raise SongGone(self.dir)
        mine = self.data.get("created_at")
        if mine is None or not os.path.exists(self.state_path()):
            return
        try:
            with open(self.state_path(), encoding="utf-8") as f:
                theirs = json.load(f).get("created_at")
        except (OSError, ValueError):
            return
        if theirs is not None and theirs != mine:
            raise SongGone(self.dir)

    def to_summary(self) -> Dict:
        """Lightweight view for the library list."""
        return {
            "slug": self.slug,
            "name": self.name,
            "stage": self.stage,
            "mode": self.mode,
            "stage_index": STAGES.index(self.stage) if self.stage in STAGES else 0,
            "stages": STAGES,
            # Findings, not rows — the library badge is a number two songs get
            # compared by, so a collapsed meter row has to count for what it holds.
            "open_issues": health.finding_count(
                i for i in self.data.get("health", {}).get("issues", [])
                if i.get("status") == "open"
            ),
            "lyric_warnings": len(self.data.get("lyrics", {}).get("warnings", [])),
            # Which choirs' lists the site shows it in.
            "published": self.published(),
            "created_at": self.data.get("created_at") or self.data.get("updated_at") or 0,
            "updated_at": self.data.get("updated_at") or 0,
        }


def _read_raw(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def lock_dir() -> str:
    """Where the song locks' files are, the same for every process of this account.

    Off the account's home in the password database, not `$HOME`, `$TMPDIR` or
    `$XDG_*`: the systemd service and a shell running `sing_song.py` may have
    different ones, and two lock files would keep nothing apart (#498). Not in
    `songs/` (its own git repo) nor beside it (an untracked folder in the checkout
    makes the deploy refuse).
    """
    home = pwd.getpwuid(os.getuid()).pw_dir
    return os.path.join(home, ".local", "state", "musescore-choir-plugins", "song-locks")


class _SongLock:
    """A re-entrant lock held across threads *and* processes.

    The thread lock keeps this process's threads apart and makes the lock
    re-entrant; the outermost hold also takes `flock` on a file, which keeps other
    processes apart. The kernel drops a `flock` when its holder dies, so a killed
    job never strands it.
    """

    def __init__(self, path: str):
        self.path = path
        self._thread = threading.RLock()
        self._depth = 0
        self._fd: Optional[int] = None

    def acquire(self) -> None:
        self._thread.acquire()
        try:
            if self._depth == 0:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                except BaseException:
                    os.close(fd)
                    raise
                self._fd = fd
            self._depth += 1
        except BaseException:
            self._thread.release()
            raise

    def release(self) -> None:
        self._depth -= 1
        if self._depth == 0 and self._fd is not None:
            fd, self._fd = self._fd, None
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        self._thread.release()

    def __enter__(self) -> "_SongLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


_LOCKS: Dict[str, _SongLock] = {}
_LOCKS_GUARD = threading.Lock()


def lock_path(slug: str, songs_dir: Optional[str] = None) -> str:
    key = hashlib.sha1(os.path.realpath(songs_dir or SONGS_DIR).encode()).hexdigest()
    return os.path.join(lock_dir(), key, f"{slug}.lock")


def song_lock(slug: str, songs_dir: Optional[str] = None) -> _SongLock:
    """The lock every save of this song's state takes, in every process.

    Routes load, work, then save, so a save from a stale copy used to undo
    whatever was saved since it was loaded. Hold this across a re-load and the
    save that follows it to make the pair one step. `songs_dir` is for a writer
    handed another songs folder; the lock is keyed by that folder's real path,
    so a worktree's `songs` symlink and the main checkout's folder share it.
    """
    path = lock_path(slug, songs_dir)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(path)
        if lock is None:
            lock = _LOCKS[path] = _SongLock(path)
        return lock


def load(slug: str) -> Optional[Song]:
    path = os.path.join(song_dir(slug), STATE_FILE)
    raw = _read_raw(path)
    if raw is None:
        return None
    song = Song(slug, _upgrade(json.loads(raw)))
    song._loaded(raw)
    return song


# The settings the site plays a song with. They used to be the Record stage's
# (`record`), and are read from there until the song is saved again (#400).
PUBLISH_SETTINGS = ("bpm", "tempo_changes")


def _upgrade(data: Dict) -> Dict:
    """A song saved before the video stages were removed, read as it is now (#400).

    Only what the app still uses is carried over: the stage moves to `publish`,
    and the BPM and tempo changes move from `record` to `publish`. The rest of
    `record` (videos, uploads) is left in the file untouched.
    """
    if data.get("stage") in RETIRED_STAGES:
        data["stage"] = "publish"
    record = data.get("record") or {}
    publish = data.setdefault("publish", {}) if any(k in record for k in PUBLISH_SETTINGS) \
        else data.get("publish")
    for key in PUBLISH_SETTINGS:
        if key in record and key not in publish:
            publish[key] = record[key]
    return data


def list_songs() -> List[Song]:
    _ensure_songs_dir()
    songs: List[Song] = []
    for entry in sorted(os.listdir(SONGS_DIR)):
        if os.path.isfile(os.path.join(SONGS_DIR, entry, STATE_FILE)):
            s = load(entry)
            if s:
                songs.append(s)
    return songs


def start_job(song: Song, kind: str, conflicts, fingerprint=None) -> Optional[Dict]:
    """`job_state.start_if_idle` for this very song, or `SongGone` (#428).

    A route loads the song, may wait (publish asks the site first), then starts
    its job; in between the song may have been deleted and another created under
    its slug. Checked under the song lock, then the job lock, the same order the
    delete takes them.
    """
    with song_lock(song.slug):
        song.check_still_here()
        return job_state.start_if_idle(song.dir, kind, conflicts, fingerprint)


def trash(slug: str) -> str:
    """Move a song's folder into `songs/.trash/<slug>-<time>/`; return where it went.

    Moving it back to `songs/<slug>/` restores it as it was.
    """
    trash_dir = os.path.join(SONGS_DIR, TRASH_DIR_NAME)
    os.makedirs(trash_dir, exist_ok=True)
    base = f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}"
    dest, n = os.path.join(trash_dir, base), 2
    while os.path.exists(dest):
        dest = os.path.join(trash_dir, f"{base}-{n}")
        n += 1
    os.rename(song_dir(slug), dest)
    return dest


def create(name: str, per_system: bool, voicing: str = "",
           sources: Optional[Dict[str, tuple]] = None, stage: str = "register") -> Song:
    """Create a new song folder + state file.

    `sources` maps a kind ("xml", "pdf") to `(file name, bytes)`; they are written
    with the state file under the slug's lock, so nothing lands in a song deleted
    or re-created meanwhile (#428). Read uploads before calling this.

    `voicing` ("men"/"women"/"mixed") settles what the parts are called. Clef and
    pitch cannot: a male-choir score is written in treble sounding an octave down
    and editions often leave the 8 off, so a tenor line reads as a soprano one.
    """
    _ensure_songs_dir()
    base = slugify(name)
    # Avoid collisions with an existing song. A slug is claimed under its lock, the
    # one a delete holds while it moves a folder away (#428).
    slug, n = base, 2
    while True:
        with song_lock(slug):
            if not os.path.exists(os.path.join(song_dir(slug), STATE_FILE)):
                return _create_at(slug, name, per_system, voicing, sources or {}, stage)
        slug = f"{base}-{n}"
        n += 1


def _create_at(slug: str, name: str, per_system: bool, voicing: str,
               sources: Dict[str, tuple], stage: str) -> Song:
    s = Song(slug, {
        "name": name,
        "slug": slug,
        "stage": stage,
        "mode": "per-system" if per_system else "normal",
        "voicing": voicing or "",
        "sources": {},
        "created_at": time.time(),
    })
    os.makedirs(s.dir, exist_ok=True)
    for kind, (file_name, data) in sources.items():
        file_name = os.path.basename(file_name)
        with open(s.path(file_name), "wb") as f:
            f.write(data)
        s.data["sources"][kind] = file_name
    s.save()
    return s
