"""Where a song's media (rendered videos and their audio) is written.

Videos are big — about 150 MB per voice at 4K — so `MEDIA_ROOT` in `.env` can put
them on another disk: a song in `songs/<slug>` then keeps its media in
`$MEDIA_ROOT/<slug>/`. Unset, media stays in `songs/<slug>/media/` as before.
`scripts/move_media.py` moves existing media to `MEDIA_ROOT` (#370).
"""

from __future__ import annotations

import os


def media_root() -> str:
    """The configured media root, absolute, or "" when media lives with the song."""
    root = os.getenv("MEDIA_ROOT", "").strip()
    return os.path.abspath(os.path.expanduser(root)) if root else ""


def legacy_media_dir(song_dir: str) -> str:
    """Where media lives without `MEDIA_ROOT`: inside the song folder."""
    return os.path.join(str(song_dir), "media")


def moved_media_dir(song_dir: str, root: str) -> str:
    """Where this song's media goes under `root`: a folder named after the song."""
    return os.path.join(root, os.path.basename(os.path.normpath(str(song_dir))))


def media_dir(song_dir: str) -> str:
    """This song's media folder (it may not exist yet).

    With `MEDIA_ROOT` set, a song whose media has not been moved yet keeps using
    the folder inside it until `scripts/move_media.py` moves it, so setting the
    variable never hides a video. The move puts the whole folder in place with one
    rename, so this never sees half a copy.
    """
    root = media_root()
    if not root:
        return legacy_media_dir(song_dir)
    moved = moved_media_dir(song_dir, root)
    legacy = legacy_media_dir(song_dir)
    if not os.path.exists(moved) and os.path.isdir(legacy):
        return legacy
    return moved
