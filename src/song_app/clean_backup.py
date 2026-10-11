"""The cleaned score a clean replaced, kept so the clean can be undone (#391).

Cleaning rebuilds the score from the scan, so a re-clean throws away whatever was
done to the cleaned score since: lyrics, edits made in MuseScore, an uploaded file.
The button already says so. This makes pressing it by mistake cost nothing: before a
clean replaces the score, the score and the song's records about it are copied to
`backups/<time>/`, and **Undo last clean** puts both back -- but only while the
score is still the one the clean made, since anything done to it after the clean
(lyrics, a fix, an upload) would be lost with nothing kept of it.

The records come back with the file because they were made against it. The health
findings, the note comparison, the MuseScore check and the lyric warnings all carry
the fingerprint of the score they describe, and that is the score being restored, so
they are true again and nothing has to be checked twice.

What does not come back is `fixes.json`. A clean only rewrites the sentences it owns
there (the MuseScore resets, the dropped voices), and anything recorded since the
clean is a person's judgement that undoing the clean must not take away.
"""
import json
import os
import shutil
import time
from typing import Dict, List, Optional, Tuple

from . import state

BACKUP_DIR = "backups"
SNAPSHOT = "song.json"
#: How many replaced scores a song keeps. Each is one score file and a few records.
KEEP = 10
#: The parts of `.song.json` that describe the cleaned score rather than the song.
KEYS = ("cleaned", "cleaned_fingerprint", "verification", "health", "lyrics", "stage")


class NothingToUndo(Exception):
    pass


class ChangedSinceClean(Exception):
    """The score is no longer what the clean made, so undoing would lose that work."""


def _root(song: state.Song) -> str:
    return song.path(BACKUP_DIR)


def _entries(song: state.Song) -> List[str]:
    """Backup folders, oldest first. Names are times, so they sort as they were made."""
    root = _root(song)
    if not os.path.isdir(root):
        return []
    return sorted(name for name in os.listdir(root)
                  if os.path.isfile(os.path.join(root, name, SNAPSHOT)))


def stash(song: state.Song) -> Optional[str]:
    """Copy the cleaned score and its records aside. None when there is no score yet."""
    cleaned = song.cleaned_path()
    if not cleaned or not os.path.exists(cleaned):
        return None
    root = _root(song)
    os.makedirs(root, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder, n = os.path.join(root, stamp), 1
    while os.path.exists(folder):
        n += 1
        folder = os.path.join(root, f"{stamp}-{n}")
    os.makedirs(folder)
    shutil.copy2(cleaned, os.path.join(folder, os.path.basename(cleaned)))
    snapshot = {key: song.data[key] for key in KEYS if key in song.data}
    snapshot["saved_at"] = time.time()
    with open(os.path.join(folder, SNAPSHOT), "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=2, ensure_ascii=False)
    for old in _entries(song)[:-KEEP]:
        shutil.rmtree(os.path.join(root, old), ignore_errors=True)
    return folder


def drop(folder: Optional[str]) -> None:
    """Take back a stash whose clean failed: the score it holds was never replaced."""
    if folder:
        shutil.rmtree(folder, ignore_errors=True)


def mark_made(folder: Optional[str], fingerprint: Optional[str]) -> None:
    """Record in the stash which score the clean put in its place.

    Undo is only safe while that score is still the one on disk: once lyrics are
    imported, a fix is applied or a hand-fixed score is uploaded, putting the old
    score back would throw that work away with nothing kept of it.
    """
    if not folder:
        return
    path = os.path.join(folder, SNAPSHOT)
    with open(path, encoding="utf-8") as fh:
        snapshot = json.load(fh)
    snapshot["made"] = fingerprint
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=2, ensure_ascii=False)


def _newest(song: state.Song) -> Optional[Tuple[str, Dict]]:
    entries = _entries(song)
    if not entries:
        return None
    folder = os.path.join(_root(song), entries[-1])
    with open(os.path.join(folder, SNAPSHOT), encoding="utf-8") as fh:
        return folder, json.load(fh)


def _changed(song: state.Song, snapshot: Dict) -> bool:
    """True when the score on disk is not the one the clean made (or nobody knows)."""
    made = snapshot.get("made")
    return not made or made != state.file_fingerprint(song.cleaned_path())


def latest(song: state.Song) -> Optional[Dict]:
    """What Undo would put back: `{saved_at, file, changed}`, or None.

    `changed` says the score has been worked on since the clean, so Undo is refused.
    """
    newest = _newest(song)
    if not newest:
        return None
    _folder, snapshot = newest
    return {"saved_at": snapshot.get("saved_at"),
            "file": os.path.basename(snapshot.get("cleaned") or ""),
            "changed": _changed(song, snapshot)}


def restore(song: state.Song) -> Dict:
    """Put the newest stashed score and its records back, and forget the stash.

    Returns the restored records. The caller holds `state.song_lock` and saves.
    """
    newest = _newest(song)
    if not newest:
        raise NothingToUndo("There is no earlier cleaned score to go back to.")
    folder, snapshot = newest
    if _changed(song, snapshot):
        raise ChangedSinceClean(
            "The score has changed since the last clean (lyrics imported, a fix applied "
            "or a score uploaded). Undo would throw that work away, so it is not offered. "
            "Download the score first if you want to keep a copy.")
    rel = snapshot.get("cleaned")
    source = os.path.join(folder, os.path.basename(rel or ""))
    if not rel or not os.path.isfile(source):
        raise NothingToUndo("The saved score is missing from its backup.")
    target = song.path(rel)
    # Copied beside the target and renamed in, so the score is never half-written.
    landing = target + ".restoring"
    shutil.copy2(source, landing)
    os.replace(landing, target)
    # Exactly as they were: a record the old score never had (no health check, no
    # lyrics yet) goes, rather than staying attached to it from the clean.
    for key in KEYS:
        if key in snapshot:
            song.data[key] = snapshot[key]
        else:
            song.data.pop(key, None)
    # The records name the file that is on disk now, whatever the snapshot said.
    song.data["cleaned_fingerprint"] = state.file_fingerprint(target)
    shutil.rmtree(folder, ignore_errors=True)
    return snapshot
