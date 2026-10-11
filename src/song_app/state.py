"""Song state machine — the .song.json file is the UX.

A Song is a folder `songs/<slug>/` plus a `.song.json` state file. The folder is a
slug; the human display name lives in the state file. See DESIGN.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import unicodedata
from typing import Dict, List, Optional

from . import health

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SONGS_DIR = os.path.join(SCRIPT_DIR, "songs")
STATE_FILE = ".song.json"

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


class Song:
    """In-memory view of a song's .song.json, with helpers to resolve its files."""

    def __init__(self, slug: str, data: Dict):
        self.slug = slug
        self.data = data

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
        """Write the whole state, under the song's lock and in one rename.

        The rename means a reader never sees half a file. The lock is what lets a
        writer that only owns a few fields (`server._rescan`) re-read and write in
        one step without another save landing in between (#252).
        """
        os.makedirs(self.dir, exist_ok=True)
        with song_lock(self.slug):
            self.data["updated_at"] = time.time()
            tmp = self.state_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.state_path())

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


_LOCKS: Dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def song_lock(slug: str) -> threading.RLock:
    """The lock every save of this song's state takes.

    Routes load, work, then save the whole state, so a save from a stale copy
    silently undoes whatever was saved since it was loaded. Hold this across a
    re-load and the save that follows it to make the pair one step.
    """
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(slug, threading.RLock())


def load(slug: str) -> Optional[Song]:
    path = os.path.join(song_dir(slug), STATE_FILE)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return Song(slug, _upgrade(json.load(f)))


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


def create(name: str, per_system: bool, voicing: str = "") -> Song:
    """Create a new song folder + state file. Caller then attaches source files.

    `voicing` ("men"/"women"/"mixed") settles what the parts are called. Clef and
    pitch cannot: a male-choir score is written in treble sounding an octave down
    and editions often leave the 8 off, so a tenor line reads as a soprano one.
    """
    _ensure_songs_dir()
    slug = slugify(name)
    # Avoid collisions with an existing song.
    base, n = slug, 2
    while os.path.exists(os.path.join(song_dir(slug), STATE_FILE)):
        slug = f"{base}-{n}"
        n += 1
    s = Song(slug, {
        "name": name,
        "slug": slug,
        "stage": "register",
        "mode": "per-system" if per_system else "normal",
        "voicing": voicing or "",
        "sources": {},
        "created_at": time.time(),
    })
    os.makedirs(s.dir, exist_ok=True)
    s.save()
    return s
