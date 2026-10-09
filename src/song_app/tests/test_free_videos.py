"""Freeing a song's local videos once YouTube has every one of them (#371).

YouTube is a function handed to `free_videos.free`, answered here from a dict,
so nothing signs in anywhere. What is pinned: which videos count as uploaded
(old entries with only a title included), that nothing is deleted unless every
video is confirmed, that the YouTube links stay, and the route's refusals.
"""

import datetime
import os
import time

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from src.song_app import free_videos, job_state, server, state  # noqa: E402

PARTS = ["T1", "T2", "ALL"]
LATER = (datetime.datetime.now(datetime.timezone.utc)
         + datetime.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
EARLIER = "2020-01-01T00:00:00Z"


@pytest.fixture
def songs(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "SONGS_DIR", str(tmp_path))
    return tmp_path


def _song(parts=PARTS, uploaded=PARTS, stamped=True, name="Laulu"):
    song = state.create(name, per_system=False)
    vdir = song.path("media", "video")
    os.makedirs(vdir)
    uploads = []
    for part in parts:
        path = os.path.join(vdir, f"{song.slug} {part}.mp4")
        with open(path, "wb") as f:
            f.write(b"x" * 1000)
        if part in uploaded:
            entry = {"title": f"{name} {part}", "video_id": f"vid{part}",
                     "url": f"https://youtu.be/vid{part}"}
            if stamped:
                st = os.stat(path)
                entry.update(part=part, file=os.path.basename(path),
                             size=st.st_size, mtime_ns=st.st_mtime_ns)
            uploads.append(entry)
    song.data["stage"] = "upload"
    song.data["record"] = {"outputs": [f"{song.slug} {p}.mp4" for p in parts],
                           "uploads": uploads}
    song.save()
    return song


def _youtube(parts=PARTS, published=LATER, processed=True):
    answers = {f"vid{p}": {"processed": processed, "published_at": published} for p in parts}
    asked = []

    def confirm(ids):
        asked.append(list(ids))
        return {i: answers[i] for i in ids if i in answers}
    confirm.asked = asked
    return confirm


def _on_disk(song):
    return sorted(os.listdir(song.path("media", "video")))


def test_every_video_uploaded_frees_them_all_and_keeps_the_links(songs):
    song = _song()
    assert free_videos.status(song)["can_free"]
    freed = free_videos.free(song, _youtube())
    assert _on_disk(song) == []
    assert freed["bytes"] == 3000 and len(freed["files"]) == 3
    after = state.load(song.slug)
    assert len(after.data["record"]["uploads"]) == 3  # the links stay
    now = free_videos.status(after)
    assert now["complete"] and not now["can_free"] and now["local_count"] == 0
    assert [v["url"] for v in now["videos"]] == [f"https://youtu.be/vid{p}" for p in PARTS]


def test_an_old_entry_with_only_a_title_still_counts(songs):
    song = _song(stamped=False, name="Kaksi laulua")
    assert all(v["state"] == "uploaded" for v in free_videos.status(song)["videos"])
    free_videos.free(song, _youtube())
    assert _on_disk(song) == []


def test_one_video_not_uploaded_keeps_every_video(songs):
    song = _song(uploaded=["T1", "ALL"])
    st = free_videos.status(song)
    assert not st["complete"] and not st["can_free"]
    with pytest.raises(ValueError, match="T2"):
        free_videos.free(song, _youtube())
    assert len(_on_disk(song)) == 3


def test_a_video_rendered_again_after_the_upload_is_not_uploaded(songs):
    song = _song()
    path = song.path("media", "video", f"{song.slug} T2.mp4")
    with open(path, "wb") as f:
        f.write(b"y" * 2000)
    states = {v["part"]: v["state"] for v in free_videos.status(song)["videos"]}
    assert states == {"T1": "uploaded", "T2": "changed", "ALL": "uploaded"}
    with pytest.raises(ValueError):
        free_videos.free(song, _youtube())
    assert len(_on_disk(song)) == 3


def test_youtube_not_having_a_video_keeps_every_video(songs):
    song = _song()
    with pytest.raises(ValueError, match="T2 \\(not found"):
        free_videos.free(song, _youtube(parts=["T1", "ALL"]))
    assert len(_on_disk(song)) == 3


def test_still_processing_keeps_every_video(songs):
    song = _song()
    with pytest.raises(ValueError, match="processing"):
        free_videos.free(song, _youtube(processed=False))
    assert len(_on_disk(song)) == 3


def test_an_old_entry_older_than_the_file_keeps_every_video(songs):
    # No stamp to compare, so YouTube's publish time has to be after the file.
    song = _song(stamped=False)
    with pytest.raises(ValueError, match="newer than the upload"):
        free_videos.free(song, _youtube(published=EARLIER))
    assert len(_on_disk(song)) == 3


def test_a_raw_recording_is_never_touched(songs):
    song = _song()
    raw = song.path("media", "video", "Screen Recording.mov")
    with open(raw, "wb") as f:
        f.write(b"r")
    free_videos.free(song, _youtube())
    assert _on_disk(song) == ["Screen Recording.mov"]


def test_the_library_says_uploaded_only_when_every_video_is(songs):
    assert not _song(uploaded=["T1"], name="Puoliksi").to_summary()["uploaded"]
    assert _song(name="Kokonaan").to_summary()["uploaded"]


# --- the route --------------------------------------------------------------
@pytest.fixture
def client(songs, monkeypatch):
    from src.stemmanauha import upload_to_youtube
    confirm = _youtube()
    monkeypatch.setattr(upload_to_youtube, "confirm_uploads",
                        lambda ids, log=None: confirm(ids))
    return TestClient(server.app)


def test_the_route_frees_and_reports(client):
    song = _song()
    r = client.post(f"/api/songs/{song.slug}/free-videos")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["upload_status"]["local_count"] == 0
    assert body["record"]["freed"]["bytes"] == 3000
    assert body["media"] == []


def test_the_route_refuses_while_a_render_or_upload_runs(client):
    song = _song()
    for kind in ("render", "upload"):
        job_state.start(song.dir, kind)
        r = client.post(f"/api/songs/{song.slug}/free-videos")
        assert r.status_code == 409
        job_state.finish(song.dir, kind)
    assert len(_on_disk(song)) == 3


def test_the_route_says_what_is_not_uploaded(client):
    song = _song(uploaded=["T1"])
    r = client.post(f"/api/songs/{song.slug}/free-videos")
    assert r.status_code == 409 and "T2" in r.json()["detail"]
    assert len(_on_disk(song)) == 3


def test_upload_only_is_refused_once_the_videos_are_freed(client):
    song = _song()
    assert client.post(f"/api/songs/{song.slug}/free-videos").status_code == 200
    r = client.post(f"/api/songs/{song.slug}/record", json={"upload_only": True})
    assert r.status_code == 409 and "record again" in r.json()["detail"]


def test_an_upload_records_the_file_it_sent(songs, monkeypatch):
    """A new upload names its file, so a later render is told apart from it."""
    from src.stemmanauha import upload_to_youtube as yt
    song = _song(uploaded=[])
    monkeypatch.setattr(yt, "get_authenticated_service", lambda: object())
    monkeypatch.setattr(yt, "create_playlist", lambda youtube, title: None)
    monkeypatch.setattr(yt, "upload_video", lambda youtube, path, title, **kw: "vid" + title.split()[-1])
    infos = []
    videos = [song.path("media", "video", f"{song.slug} {p}.mp4") for p in PARTS]
    yt.upload_to_youtube(song.dir, videos, log=lambda m: None, display_name="Laulu",
                         on_uploaded=infos.append)
    assert [(i["part"], i["file"], i["size"]) for i in infos] == [
        (p, f"{song.slug} {p}.mp4", 1000) for p in PARTS]
    song.data["record"]["uploads"] = infos
    song.save()
    assert free_videos.status(song)["can_free"]


def test_an_older_take_does_not_stand_in_once_the_videos_are_freed(songs):
    """A screen-recorder .mov beside the scroll renderer's .mp4 for the same part
    is not the song's video: freeing leaves it, and the song stays uploaded."""
    song = _song()
    old = song.path("media", "video", f"{song.slug} T1.mov")
    with open(old, "wb") as f:
        f.write(b"o" * 500)
    past = time.time() - 3600
    os.utime(old, (past, past))
    assert free_videos.status(song)["can_free"]
    free_videos.free(song, _youtube())
    assert _on_disk(song) == [f"{song.slug} T1.mov"]
    after = state.load(song.slug)
    now = free_videos.status(after)
    assert now["complete"] and now["local_count"] == 0
    assert all(v["state"] == "uploaded" and not v["local"] for v in now["videos"])
    assert after.to_summary()["uploaded"]
