"""Song state machine — the .song.json file is the UX.

A Song is a folder `songs/<slug>/` plus a `.song.json` state file. The folder is a
slug; the human display name lives in the state file. See DESIGN.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import unicodedata
from typing import Dict, List, Optional

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SONGS_DIR = os.path.join(SCRIPT_DIR, "songs")
STATE_FILE = ".song.json"

# Linear stages; `fix` and `lyrics` form a loop (lyric overflow can re-open fix).
STAGES = ["register", "clean", "fix", "lyrics", "review", "record"]


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

    def set_stage(self, stage: str) -> None:
        self.data["stage"] = stage

    # ---- persistence -----------------------------------------------------
    def save(self) -> None:
        os.makedirs(self.dir, exist_ok=True)
        self.data["updated_at"] = time.time()
        with open(self.state_path(), "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def to_summary(self) -> Dict:
        """Lightweight view for the library list."""
        return {
            "slug": self.slug,
            "name": self.name,
            "stage": self.stage,
            "mode": self.mode,
            "stage_index": STAGES.index(self.stage) if self.stage in STAGES else 0,
            "stages": STAGES,
            "open_issues": sum(
                1 for i in self.data.get("health", {}).get("issues", [])
                if i.get("status") == "open"
            ),
            "lyric_warnings": len(self.data.get("lyrics", {}).get("warnings", [])),
        }


def load(slug: str) -> Optional[Song]:
    path = os.path.join(song_dir(slug), STATE_FILE)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return Song(slug, json.load(f))


def list_songs() -> List[Song]:
    _ensure_songs_dir()
    songs: List[Song] = []
    for entry in sorted(os.listdir(SONGS_DIR)):
        if os.path.isfile(os.path.join(SONGS_DIR, entry, STATE_FILE)):
            s = load(entry)
            if s:
                songs.append(s)
    return songs


def create(name: str, per_system: bool) -> Song:
    """Create a new song folder + state file. Caller then attaches source files."""
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
        "sources": {},
        "created_at": time.time(),
    })
    os.makedirs(s.dir, exist_ok=True)
    s.save()
    return s
