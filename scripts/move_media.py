#!/usr/bin/env python3
"""Move every song's media from `songs/<slug>/media/` to `$MEDIA_ROOT/<slug>/` (#370).

    .venv/bin/python scripts/move_media.py            # MEDIA_ROOT from .env
    .venv/bin/python scripts/move_media.py --dry-run  # say what would move

Safe to run while the app is up, and again after a partial run:

- a song that is recording or uploading (its `.recording.lock` names a live
  process) is skipped, and so is one whose media changed during the copy;
- each song is copied into `<slug>.moving` beside its target, every file is
  compared by size and SHA-256, and only then is the copy renamed into place
  and the old folder deleted;
- until that rename the app keeps reading the old folder (`media_root.media_dir`),
  so nothing ever sees half a copy.

A song whose target already exists is skipped: two folders for one song is for a
person to sort out, not this script.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
from typing import Dict, Optional, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from src.media_root import legacy_media_dir, media_root, moved_media_dir  # noqa: E402

LOCK_FILE = ".recording.lock"  # server._lock_path: held across record and upload
Snapshot = Dict[str, Tuple[int, int]]


def snapshot(folder: str) -> Snapshot:
    """Every file under `folder` with its size and mtime, by relative path."""
    out: Snapshot = {}
    for base, _dirs, files in os.walk(folder):
        for name in files:
            path = os.path.join(base, name)
            st = os.lstat(path)
            out[os.path.relpath(path, folder)] = (st.st_size, st.st_mtime_ns)
    return out


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def busy(song_dir: str) -> Optional[str]:
    """Why this song must not be moved now, or None."""
    lock = os.path.join(song_dir, LOCK_FILE)
    if not os.path.exists(lock):
        return None
    try:
        with open(lock) as f:
            pid = int(f.read().strip() or "0")
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None  # a lock left by a dead server; the app clears it too
    except PermissionError:
        pass
    return f"a recording or upload is running (pid {pid})"


def move_song(song_dir: str, root: str, dry_run: bool, log=print) -> str:
    """Move one song's media. Returns "moved", "skipped" or "nothing"."""
    slug = os.path.basename(os.path.normpath(song_dir))
    source = legacy_media_dir(song_dir)
    if os.path.islink(source) or not os.path.isdir(source):
        return "nothing"
    target = moved_media_dir(song_dir, root)
    if os.path.exists(target):
        log(f"{slug}: skipped — {target} already exists")
        return "skipped"
    reason = busy(song_dir)
    if reason:
        log(f"{slug}: skipped — {reason}")
        return "skipped"

    before = snapshot(source)
    size = sum(s for s, _ in before.values())
    if dry_run:
        log(f"{slug}: would move {len(before)} files, {size / 1e9:.2f} GB, to {target}")
        return "moved"

    staging = target + ".moving"
    if os.path.exists(staging):
        shutil.rmtree(staging)  # a copy an earlier run did not finish; never in use
    os.makedirs(root, exist_ok=True)
    log(f"{slug}: copying {len(before)} files, {size / 1e9:.2f} GB…")
    shutil.copytree(source, staging, symlinks=True)

    for rel in sorted(before):
        a, b = os.path.join(source, rel), os.path.join(staging, rel)
        if os.path.islink(a):
            ok = os.path.islink(b) and os.readlink(a) == os.readlink(b)
        else:
            ok = (os.path.isfile(b) and os.path.getsize(a) == os.path.getsize(b)
                  and sha256(a) == sha256(b))
        if not ok:
            shutil.rmtree(staging)
            log(f"{slug}: skipped — the copy of {rel} does not match; nothing deleted")
            return "skipped"

    reason = busy(song_dir)
    if reason or snapshot(source) != before:
        shutil.rmtree(staging)
        log(f"{slug}: skipped — {reason or 'its media changed during the copy'}; "
            "nothing deleted, run again later")
        return "skipped"

    os.rename(staging, target)  # from here the app reads the new folder
    shutil.rmtree(source)
    log(f"{slug}: moved to {target}")
    return "moved"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="say what would move")
    parser.add_argument("--songs", default=os.path.join(REPO, "songs"),
                        help="the songs folder (default: the repo's songs/)")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv(os.path.join(REPO, ".env"))
    root = media_root()
    if not root:
        print("MEDIA_ROOT is not set in .env; there is nowhere to move media to.",
              file=sys.stderr)
        return 2

    counts = {"moved": 0, "skipped": 0, "nothing": 0}
    for entry in sorted(os.listdir(args.songs)):
        song_dir = os.path.join(args.songs, entry)
        if os.path.isdir(song_dir):
            counts[move_song(song_dir, root, args.dry_run)] += 1
    verb = "would move" if args.dry_run else "moved"
    print(f"{verb} {counts['moved']}, skipped {counts['skipped']}")
    return 1 if counts["skipped"] else 0


if __name__ == "__main__":
    sys.exit(main())
