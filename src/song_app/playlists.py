"""Putting an uploaded song into the choir's playlists, or taking it out (#338).

An upload makes the song its own playlist and can add it to one more. Until this
there was no way to change that afterwards, and the app kept no record of the
extra playlist either, so "is this song in the women's choir list?" could only be
answered on YouTube. So the answer is read from YouTube every time: a list call is
one quota unit per 50 videos, and a record kept here would go stale the moment
anybody edits a playlist in YouTube's own app.

The playlists offered are the ones songs have been picked into before
(``state.load_playlists``), not the whole account — the account holds dozens of
unrelated ones. Adding or removing touches this song's videos only, and never the
song's own playlist.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from src.stemmanauha import upload_to_youtube as yt

from . import state


class PlaylistError(ValueError):
    """A request this module refuses; the message is said to the person."""


def _service():
    return yt.get_authenticated_service()


def _video_ids(song: state.Song) -> List[str]:
    uploads = song.data.get("record", {}).get("uploads", [])
    return [u["video_id"] for u in uploads if u.get("video_id")]


def _own_playlist_ids(song: state.Song) -> set:
    uploads = song.data.get("record", {}).get("uploads", [])
    return {u.get("playlist_id") for u in uploads if u.get("playlist_id")}


def _row(playlist_id: str, title: str, items: Dict[str, str],
         videos: List[str]) -> Dict:
    return {"id": playlist_id, "title": title,
            "count": sum(1 for v in videos if v in items), "total": len(videos)}


def account_playlists(youtube=None) -> List[Dict]:
    """The account's own playlists a song could be picked into: every one except
    the per-song playlists uploads make. Each says whether it is already offered."""
    youtube = youtube or _service()
    chosen = {p["id"] for p in state.load_playlists()}
    return [{**p, "chosen": p["id"] in chosen}
            for p in yt.own_playlists(youtube) if not state.is_song_playlist(p["title"])]


def song_playlists(song: state.Song, youtube=None) -> Dict:
    """Each offered playlist with how many of this song's videos it holds."""
    videos = _video_ids(song)
    if not videos:
        raise PlaylistError("Nothing uploaded yet.")
    youtube = youtube or _service()
    titles = {p["id"]: p["title"] for p in yt.own_playlists(youtube)}
    rows = []
    for p in state.load_playlists():
        title = titles.get(p["id"]) or p["title"]
        if p["id"] in titles:
            state.save_playlist(p["id"], title)  # a bare id gets its real name
        try:
            items = yt.playlist_video_items(youtube, p["id"])
        except yt.QuotaExceeded:
            raise
        except Exception as exc:  # gone, or not ours: say so on that row only
            rows.append({"id": p["id"], "title": title, "count": 0,
                         "total": len(videos), "error": f"Could not read it: {exc}"})
            continue
        rows.append(_row(p["id"], title, items, videos))
    return {"total": len(videos), "playlists": rows}


def set_membership(song: state.Song, playlist_id: str, member: bool,
                   log: Callable[[str], None], youtube=None,
                   title: Optional[str] = None) -> Dict:
    """Put every one of this song's videos into the playlist, or take them all out.
    Returns the playlist's new row."""
    videos = _video_ids(song)
    if not videos:
        raise PlaylistError("Nothing uploaded yet.")
    if not playlist_id:
        raise PlaylistError("Which playlist?")
    if playlist_id in _own_playlist_ids(song):
        raise PlaylistError("That is the song's own playlist; it is left as the upload made it.")
    youtube = youtube or _service()
    items = yt.playlist_video_items(youtube, playlist_id, log=log)
    name = title or playlist_id
    if member:
        missing = [v for v in videos if v not in items]
        log(f"Adding {len(missing)} video(s) to {name}…")
        for vid in missing:
            yt.add_video_to_playlist(youtube, playlist_id, vid)
            items[vid] = "added"
    else:
        present = [v for v in videos if v in items]
        log(f"Removing {len(present)} video(s) from {name}…")
        for vid in present:
            yt.remove_from_playlist(youtube, items.pop(vid), log=log)
    # Counted from what was done rather than listed again: YouTube's list can lag
    # a write by a moment, and a row reading "7 of 8" just after adding all eight
    # would send somebody to press it again.
    state.save_playlist(playlist_id, title)
    row = _row(playlist_id, name, items, videos)
    log(f"{name}: {row['count']} of {row['total']} of this song's videos.")
    return row
