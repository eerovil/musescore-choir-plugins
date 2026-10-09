"""MEDIA_ROOT: where a song's videos live, and moving them there safely (#370)."""

import importlib.util
import os
import shutil

from fastapi.testclient import TestClient

from src import media_root
from src.song_app import server, state

_spec = importlib.util.spec_from_file_location(
    "move_media", os.path.join(os.path.dirname(__file__), "..", "..", "..",
                               "scripts", "move_media.py"))
move_media = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(move_media)


def _song(tmp_path, monkeypatch, root=None):
    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    if root is not None:
        monkeypatch.setenv("MEDIA_ROOT", str(root))
    return state.create("Media Song", per_system=False)


def _video(folder, name, data=b"video bytes"):
    os.makedirs(os.path.join(folder, "video"), exist_ok=True)
    with open(os.path.join(folder, "video", name), "wb") as f:
        f.write(data)


def test_unset_keeps_media_inside_the_song(tmp_path, monkeypatch):
    song = _song(tmp_path, monkeypatch)
    assert song.media_path("video") == os.path.join(song.dir, "media", "video")


def test_set_puts_media_under_the_root_by_slug(tmp_path, monkeypatch):
    root = tmp_path / "ssd"
    song = _song(tmp_path, monkeypatch, root)
    assert song.media_path("video") == str(root / song.slug / "video")


def test_media_not_moved_yet_is_still_read_where_it_is(tmp_path, monkeypatch):
    root = tmp_path / "ssd"
    song = _song(tmp_path, monkeypatch, root)
    _video(song.path("media"), f"{song.slug} T1.mp4")
    assert song.media_path("video") == os.path.join(song.dir, "media", "video")


def test_app_lists_and_serves_videos_from_the_root(tmp_path, monkeypatch):
    root = tmp_path / "ssd"
    song = _song(tmp_path, monkeypatch, root)
    _video(str(root / song.slug), f"{song.slug} T1.mp4", b"from the ssd")
    listed = server._media_list(song)
    assert [m["label"] for m in listed] == ["T1"]
    client = TestClient(server.app)
    got = client.get(listed[0]["url"])
    assert got.status_code == 200 and got.content == b"from the ssd"


def test_scroll_render_writes_into_the_root(tmp_path, monkeypatch):
    from src.song_app import pipeline
    import src.scrollvideo as scrollvideo
    root = tmp_path / "ssd"
    song = _song(tmp_path, monkeypatch, root)
    seen = {}

    def fake_build(_cleaned, out_dir, **kwargs):
        seen["out"], seen["audio"] = out_dir, kwargs["audio_cache_dir"]
        return []
    monkeypatch.setattr(scrollvideo, "build_videos", fake_build)
    pipeline.run_scroll_video(song.dir, "x.mscx", song.slug)
    assert seen["out"] == str(root / song.slug / "video")
    assert seen["audio"] == str(root / song.slug / ".scrollvideo-audio")


def test_move_copies_verifies_then_deletes(tmp_path, monkeypatch):
    root = tmp_path / "ssd"
    song = _song(tmp_path, monkeypatch, root)
    _video(song.path("media"), f"{song.slug} T1.mp4", b"a" * 5000)
    with open(song.path("media", "T1.mp3"), "wb") as f:
        f.write(b"mp3")

    assert move_media.move_song(song.dir, str(root), dry_run=False, log=lambda m: None) == "moved"
    assert not os.path.exists(song.path("media"))
    assert (root / song.slug / "video" / f"{song.slug} T1.mp4").read_bytes() == b"a" * 5000
    assert (root / song.slug / "T1.mp3").read_bytes() == b"mp3"
    assert not os.path.exists(str(root / song.slug) + ".moving")
    assert song.media_path("video") == str(root / song.slug / "video")
    # A second run finds nothing to do.
    assert move_media.move_song(song.dir, str(root), dry_run=False, log=lambda m: None) == "nothing"


def test_dry_run_moves_nothing(tmp_path, monkeypatch):
    root = tmp_path / "ssd"
    song = _song(tmp_path, monkeypatch, root)
    _video(song.path("media"), "x.mp4")
    assert move_media.move_song(song.dir, str(root), dry_run=True, log=lambda m: None) == "moved"
    assert os.path.exists(song.path("media", "video", "x.mp4"))
    assert not root.exists()


def test_a_running_recording_or_upload_is_not_moved(tmp_path, monkeypatch):
    root = tmp_path / "ssd"
    song = _song(tmp_path, monkeypatch, root)
    _video(song.path("media"), "x.mp4")
    with open(song.path(".recording.lock"), "w") as f:
        f.write(str(os.getpid()))  # a live process holds it
    said = []
    assert move_media.move_song(song.dir, str(root), dry_run=False, log=said.append) == "skipped"
    assert "running" in said[0]
    assert os.path.exists(song.path("media", "video", "x.mp4"))
    assert not root.exists()


def test_a_dead_servers_lock_does_not_block(tmp_path, monkeypatch):
    root = tmp_path / "ssd"
    song = _song(tmp_path, monkeypatch, root)
    _video(song.path("media"), "x.mp4")
    with open(song.path(".recording.lock"), "w") as f:
        f.write("999999999")
    assert move_media.move_song(song.dir, str(root), dry_run=False, log=lambda m: None) == "moved"


def test_a_bad_copy_deletes_nothing(tmp_path, monkeypatch):
    root = tmp_path / "ssd"
    song = _song(tmp_path, monkeypatch, root)
    _video(song.path("media"), "x.mp4", b"good")
    real = shutil.copytree

    def corrupting(src, dst, *a, **kw):
        out = real(src, dst, *a, **kw)
        if not dst.endswith(".moving"):
            return out
        with open(os.path.join(dst, "video", "x.mp4"), "wb") as f:
            f.write(b"bad!")
    monkeypatch.setattr(move_media.shutil, "copytree", corrupting)
    assert move_media.move_song(song.dir, str(root), dry_run=False, log=lambda m: None) == "skipped"
    assert open(song.path("media", "video", "x.mp4"), "rb").read() == b"good"
    assert not (root / song.slug).exists()
    assert not os.path.exists(str(root / song.slug) + ".moving")


def test_media_written_during_the_copy_is_not_lost(tmp_path, monkeypatch):
    root = tmp_path / "ssd"
    song = _song(tmp_path, monkeypatch, root)
    _video(song.path("media"), "x.mp4")
    real = shutil.copytree

    def racing(src, dst, *a, **kw):
        out = real(src, dst, *a, **kw)
        if not dst.endswith(".moving"):
            return out
        with open(os.path.join(src, "video", "new.mp4"), "wb") as f:
            f.write(b"written meanwhile")
    monkeypatch.setattr(move_media.shutil, "copytree", racing)
    assert move_media.move_song(song.dir, str(root), dry_run=False, log=lambda m: None) == "skipped"
    assert os.path.exists(song.path("media", "video", "new.mp4"))
    assert not (root / song.slug).exists()


def test_an_existing_target_is_left_for_a_person(tmp_path, monkeypatch):
    root = tmp_path / "ssd"
    song = _song(tmp_path, monkeypatch, root)
    _video(song.path("media"), "x.mp4")
    _video(str(root / song.slug), "y.mp4")
    assert move_media.move_song(song.dir, str(root), dry_run=False, log=lambda m: None) == "skipped"
    assert os.path.exists(song.path("media", "video", "x.mp4"))


def test_create_video_finds_merged_outputs_under_the_root(tmp_path, monkeypatch):
    from src.stemmanauha import create_video
    root = tmp_path / "ssd"
    song = _song(tmp_path, monkeypatch, root)
    folder = str(root / song.slug)
    _video(folder, f"{song.slug} T1.mp4")
    with open(os.path.join(folder, f"{song.slug} T1.mp3"), "wb") as f:
        f.write(b"x")
    found = create_video.find_merged_outputs(song.dir)
    assert [os.path.basename(str(p)) for p in found] == [f"{song.slug} T1.mp4"]
    assert media_root.media_dir(song.dir) == folder
