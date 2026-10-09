"""Which of a song's videos YouTube has, and freeing the local copies (#371).

A song's practice videos are 4K and about 150 MB a voice, so a song can hold
2 GB, and once YouTube has them they are only a cache: recording again draws
them from the score. This module says, per video, whether it is on YouTube, and
deletes the local copies when every one of them is.

"On YouTube" has two halves, and freeing needs both:

- **the record**: an entry in `record.uploads` for the video's part, with a
  `video_id`. An upload made since this module records which file it sent and
  that file's size and mtime, so a video rendered again after the upload no
  longer counts as uploaded. Older entries say neither, and some do not even say
  their part, which is then read off the end of the title.
- **YouTube itself**, asked at the moment of freeing: the video still exists,
  has finished processing, and was published after the local file was written.
  The last check is what covers an old entry with no file stamp.

Nothing is freed unless every local video passes both. A raw screen recording,
and an older take of a part another file has replaced, are never touched.
"""

from __future__ import annotations

import datetime
import os
import time
from typing import Callable, Dict, List, Optional

from . import state

VIDEO_EXTS = (".mov", ".mp4")


def _part_of(upload: Dict) -> Optional[str]:
    """The part an upload entry is for. Old entries carry only the title,
    "<song name> <part>", and a part never has a space in it."""
    if upload.get("part"):
        return upload["part"]
    title = (upload.get("title") or "").strip()
    return title.rsplit(" ", 1)[-1] if title else None


def local_videos(song: state.Song) -> Dict[str, str]:
    """{part: path} of the merged videos on disk, newest file per part — the
    same choice the upload makes (`create_video.find_merged_outputs`)."""
    vdir = song.path("media", "video")
    if not os.path.isdir(vdir):
        return {}
    prefix = song.slug + " "
    newest: Dict[str, str] = {}
    for name in os.listdir(vdir):
        stem, ext = os.path.splitext(name)
        if ext.lower() not in VIDEO_EXTS or not stem.startswith(prefix):
            continue
        part = stem[len(prefix):]
        path = os.path.join(vdir, name)
        if part not in newest or os.path.getmtime(path) > os.path.getmtime(newest[part]):
            newest[part] = path
    return newest


def _matches(upload: Dict, path: str) -> Optional[bool]:
    """Whether the entry was made from this very file: True/False when the entry
    recorded its file, None when it is an older entry that cannot say."""
    if not upload.get("file"):
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (upload["file"] == os.path.basename(path)
            and upload.get("size") == st.st_size
            and upload.get("mtime_ns") == st.st_mtime_ns)


def status(song: state.Song) -> Dict:
    """Per video: is it on disk, and is it recorded as uploaded.

    `complete` is the library's "uploaded": every video the song has, on disk or
    recorded as rendered, has a YouTube id. One entry is not enough, since an
    upload that stopped half-way leaves some.
    """
    rec = song.data.get("record") or {}
    by_part = {}
    for upload in rec.get("uploads") or []:
        part = _part_of(upload)
        if part and upload.get("video_id"):
            by_part[part] = upload
    local = local_videos(song)
    prefix = song.slug + " "
    parts = [os.path.splitext(os.path.basename(n))[0] for n in rec.get("outputs") or []]
    parts = [p[len(prefix):] if p.startswith(prefix) else p for p in parts]
    for part in list(local) + list(by_part):
        if part not in parts:
            parts.append(part)

    rows = []
    for part in parts:
        upload = by_part.get(part)
        path = local.get(part)
        row = {"part": part, "local": bool(path),
               "size": os.path.getsize(path) if path else 0,
               "video_id": upload.get("video_id") if upload else None,
               "url": upload.get("url") if upload else None}
        if not upload:
            row["state"] = "not_uploaded"
        elif path and _matches(upload, path) is False:
            # Rendered again since: YouTube has an older take than this file.
            row["state"] = "changed"
        else:
            row["state"] = "uploaded"
        rows.append(row)
    complete = bool(rows) and all(r["state"] == "uploaded" for r in rows)
    local_bytes = sum(r["size"] for r in rows)
    return {"videos": rows, "complete": complete,
            "local_count": sum(1 for r in rows if r["local"]),
            "local_bytes": local_bytes,
            "can_free": complete and local_bytes > 0,
            "freed": rec.get("freed")}


def _published(stamp: str) -> float:
    return datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def free(song: state.Song, confirm: Callable[[List[str]], Dict[str, Dict]],
         log: Callable[[str], None] = lambda m: None) -> Dict:
    """Delete the local videos once YouTube is confirmed to hold every one.

    `confirm(video_ids)` asks YouTube and answers {video_id: {"processed": bool,
    "published_at": "<ISO time>"}}, leaving out ids YouTube does not know.
    Raises ValueError naming what is not confirmed, and deletes nothing then.
    The caller holds the song's job gate, so no recording or upload runs meanwhile.
    """
    now = status(song)
    local = local_videos(song)
    if not local:
        raise ValueError("There are no local videos to free.")
    missing = [r["part"] for r in now["videos"] if r["state"] != "uploaded"]
    if missing:
        raise ValueError("Not every video is on YouTube: " + ", ".join(missing)
                         + ". Upload them first.")
    rows = {r["part"]: r for r in now["videos"]}
    ids = [rows[part]["video_id"] for part in local]
    log(f"Asking YouTube about {len(ids)} video(s)…")
    answers = confirm(ids)
    refused = []
    for part, path in sorted(local.items()):
        vid = rows[part]["video_id"]
        seen = answers.get(vid)
        if not seen:
            refused.append(f"{part} (not found on YouTube)")
        elif not seen.get("processed"):
            refused.append(f"{part} (YouTube is still processing it)")
        elif not seen.get("published_at") or _published(seen["published_at"]) < os.path.getmtime(path):
            refused.append(f"{part} (the local file is newer than the upload)")
    if refused:
        raise ValueError("Kept every video; not confirmed: " + "; ".join(refused) + ".")

    freed, total = [], 0
    for part, path in sorted(local.items()):
        size = os.path.getsize(path)
        os.remove(path)
        freed.append(os.path.basename(path))
        total += size
        log(f"Deleted {os.path.basename(path)} ({size / 2**20:.0f} MB).")
    with state.song_lock(song.slug):
        song = state.load(song.slug) or song
        song.data.setdefault("record", {})["freed"] = {
            "at": time.time(), "files": freed, "bytes": total}
        song.save()
    log(f"Freed {total / 2**30:.2f} GB. The YouTube links stay; recording again "
        "renders the videos afresh.")
    return song.data["record"]["freed"]
