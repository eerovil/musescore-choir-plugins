"""The Record stage offers two renderers and defaults to the scrolling one.

Both write "<slug> <part>" files into media/video, so review and upload do not
care which one ran. Neither renderer actually runs here — what is under test is
the routing, the state it records, and the stage it leaves the song in.
"""
import os
import time

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from src.song_app import job_state, pipeline, server, state


@pytest.fixture
def song(tmp_path, monkeypatch):
    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    s = state.create("My Song", per_system=False)
    cleaned = s.path("mysong_cleaned.mscx")
    with open(cleaned, "w") as fh:
        fh.write("<museScore><Score/></museScore>")
    s.data["cleaned"] = os.path.basename(cleaned)
    s.data["stage"] = "record"
    s.data["review"] = {"approved_against": state.file_fingerprint(cleaned)}
    s.save()
    return s


@pytest.fixture
def client(song):
    return TestClient(server.app)


def _finished(client, slug):
    """The record endpoint hands off to a worker thread; wait for it."""
    for _ in range(250):
        data = client.get(f"/api/songs/{slug}").json()
        if not data.get("recording"):
            return data
        time.sleep(0.02)
    raise AssertionError("the record run never finished")


def _fake_scroll(monkeypatch, seen, parts=("S1", "A1")):
    def fake(song_dir, cleaned, name, *, quality="4k", hardware_encoding=True,
             initial_bpm=None,
             log=lambda m: None,
             progress=lambda m: None):
        seen.update(song_dir=song_dir, cleaned=cleaned, name=name, quality=quality,
                    hardware_encoding=hardware_encoding, initial_bpm=initial_bpm)
        out = os.path.join(song_dir, "media", "video")
        os.makedirs(out, exist_ok=True)
        made = []
        for part in parts:
            path = os.path.join(out, f"{name} {part}.mp4")
            open(path, "wb").close()
            made.append(path)
        return made
    monkeypatch.setattr(pipeline, "run_scroll_video", fake)


def test_the_scrolling_renderer_is_what_runs_by_default(client, song, monkeypatch):
    seen = {}
    _fake_scroll(monkeypatch, seen)

    assert client.post(f"/api/songs/{song.slug}/record", json={}).json()["started"]
    data = _finished(client, song.slug)

    assert seen["name"] == song.slug, "outputs must be named for the slug"
    assert seen["quality"] == "4k"
    assert seen["hardware_encoding"] is True
    assert seen["initial_bpm"] == 80
    assert data["record"]["renderer"] == "scroll"
    assert data["record"]["bpm"] == 80
    assert data["record"]["outputs"] == [f"{song.slug} S1.mp4", f"{song.slug} A1.mp4"]
    assert data["stage"] == "upload"


def test_the_song_api_reports_when_the_bpm_choice_is_needed(client, song):
    assert client.get(f"/api/songs/{song.slug}").json()["needs_initial_bpm"] is True


def test_review_approval_is_tied_to_the_cleaned_score(client, song):
    song.set_stage("review")
    song.save()

    shown = state.file_fingerprint(song.cleaned_path())
    approved = client.post(f"/api/songs/{song.slug}/approve-review", json={
        "cleaned_fingerprint": shown,
    })

    assert approved.status_code == 200
    data = approved.json()
    assert data["stage"] == "record"
    assert data["review"]["approved_against"] == \
           data["verification_summary"]["cleaned_fingerprint"]

    with open(song.cleaned_path(), "a") as fh:
        fh.write("\n")
    stale = client.get(f"/api/songs/{song.slug}").json()
    assert stale["review"]["approved_against"] != \
           stale["verification_summary"]["cleaned_fingerprint"]


def test_review_cannot_approve_a_score_that_changed_after_it_was_shown(client, song):
    shown = state.file_fingerprint(song.cleaned_path())
    with open(song.cleaned_path(), "a") as fh:
        fh.write("\n")

    response = client.post(f"/api/songs/{song.slug}/approve-review", json={
        "cleaned_fingerprint": shown,
    })

    assert response.status_code == 409
    assert "changed" in response.json()["detail"]


def test_rendering_rejects_a_stale_review_approval(client, song, monkeypatch):
    with open(song.cleaned_path(), "a") as fh:
        fh.write("\n")
    monkeypatch.setattr(
        pipeline, "run_scroll_video",
        lambda *_args, **_kwargs: pytest.fail("a stale score must not render"),
    )

    response = client.post(f"/api/songs/{song.slug}/record", json={})

    assert response.status_code == 409
    assert "approve" in response.json()["detail"]


def test_remerge_does_not_require_approval_of_the_current_score(client, song, monkeypatch):
    with open(song.cleaned_path(), "a") as fh:
        fh.write("\n")
    seen = {}
    import src.stemmanauha.create_video as create_video
    monkeypatch.setattr(create_video, "run", lambda **kwargs: seen.update(kwargs) or [])

    response = client.post(f"/api/songs/{song.slug}/record", json={
        "renderer": "screen", "merge_only": True,
    })

    assert response.status_code == 200
    _finished(client, song.slug)
    assert seen["merge_only"] is True


def test_an_existing_opening_tempo_is_never_overridden(client, song, monkeypatch):
    with open(song.cleaned_path(), "w") as fh:
        fh.write("<museScore><Score><Staff><Measure><voice>"
                 "<Tempo><tempo>1.5</tempo></Tempo><Chord/>"
                 "</voice></Measure></Staff></Score></museScore>")
    song.data["review"] = {"approved_against": state.file_fingerprint(song.cleaned_path())}
    song.save()
    seen = {}
    _fake_scroll(monkeypatch, seen)

    assert client.get(f"/api/songs/{song.slug}").json()["needs_initial_bpm"] is False
    client.post(f"/api/songs/{song.slug}/record", json={"bpm": 92})
    data = _finished(client, song.slug)

    assert seen["initial_bpm"] is None
    assert "bpm" not in data["record"]


@pytest.mark.parametrize("bpm", [19, 301, "quick"])
def test_an_invalid_missing_tempo_bpm_is_rejected(client, song, bpm):
    response = client.post(f"/api/songs/{song.slug}/record", json={"bpm": bpm})
    assert response.status_code == 400


def test_the_rendered_files_are_what_review_and_upload_look_for(client, song, monkeypatch):
    """The whole point of the naming: neither stage knows which renderer ran."""
    from src.stemmanauha.create_video import find_merged_outputs
    _fake_scroll(monkeypatch, {})
    client.post(f"/api/songs/{song.slug}/record", json={})
    data = _finished(client, song.slug)

    assert [m["label"] for m in data["media"]] == ["A1", "S1"]
    assert all(m["merged"] for m in data["media"])
    assert [os.path.basename(p) for p in find_merged_outputs(song.dir)] == [
        f"{song.slug} A1.mp4", f"{song.slug} S1.mp4"]


def test_switching_renderer_does_not_upload_the_previous_take_as_well(song):
    """Both renderers write "<slug> <part>" here and neither clears the other's
    files, so upload has to pick one video per voice or a re-render doubles every
    upload."""
    import time

    from src.stemmanauha.create_video import find_merged_outputs

    media = os.path.join(song.dir, "media")
    video = os.path.join(media, "video")
    os.makedirs(video)
    open(os.path.join(media, f"{song.slug} S1.mp3"), "wb").close()
    open(os.path.join(video, f"{song.slug} S1.mov"), "wb").close()   # screen recording
    time.sleep(0.01)
    open(os.path.join(video, f"{song.slug} S1.mp4"), "wb").close()   # newer scroll render

    found = [os.path.basename(p) for p in find_merged_outputs(song.dir)]
    assert found == [f"{song.slug} S1.mp4"], "one video per voice, newest wins"


def test_the_size_choice_is_passed_through(client, song, monkeypatch):
    seen = {}
    _fake_scroll(monkeypatch, seen)
    client.post(f"/api/songs/{song.slug}/record", json={"quality": "1080p"})
    _finished(client, song.slug)
    assert seen["quality"] == "1080p"


def test_720p_and_software_encoding_are_passed_through_and_remembered(
        client, song, monkeypatch):
    seen = {}
    _fake_scroll(monkeypatch, seen)
    client.post(f"/api/songs/{song.slug}/record", json={
        "quality": "720p", "hardware_encoding": False, "bpm": 92})
    data = _finished(client, song.slug)

    assert seen["quality"] == "720p"
    assert seen["hardware_encoding"] is False
    assert seen["initial_bpm"] == 92
    assert data["record"]["quality"] == "720p"
    assert data["record"]["hardware_encoding"] is False
    assert data["record"]["bpm"] == 92


def test_720p_maps_to_1280_by_720_and_uses_the_song_audio_cache(
        song, monkeypatch):
    seen = {}
    import src.scrollvideo as scrollvideo
    monkeypatch.setattr(scrollvideo, "build_videos",
                        lambda *args, **kwargs: seen.update(kwargs) or [])

    pipeline.run_scroll_video(song.dir, song.cleaned_path(), song.slug,
                              quality="720p", hardware_encoding=False,
                              initial_bpm=80)

    assert (seen["width"], seen["height"], seen["fps"]) == (1280, 720, 30)
    assert seen["hardware_encoding"] is False
    assert seen["initial_bpm"] == 80
    assert seen["audio_cache_dir"] == song.path("media", ".scrollvideo-audio")


def test_render_status_and_logs_are_available_after_a_fresh_song_read(
        client, song, monkeypatch):
    def fake(song_dir, cleaned, name, *, quality="4k", hardware_encoding=True,
             initial_bpm=None,
             log=lambda m: None,
             progress=lambda m: None):
        log("Engraving")
        progress("Rendering video: 50% (30/60 frames)")
        out = os.path.join(song_dir, "media", "video")
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, f"{name} ALL.mp4")
        open(path, "wb").close()
        return [path]

    monkeypatch.setattr(pipeline, "run_scroll_video", fake)
    client.post(f"/api/songs/{song.slug}/record", json={})
    data = _finished(client, song.slug)

    job = data["jobs"]["render"]
    assert job["status"] == "succeeded"
    assert [(line["type"], line["line"]) for line in job["logs"]] == [
        ("log", "Rendering the scrolling video (4k)…"),
        ("log", "Engraving"),
        ("progress", "Rendering video: 50% (30/60 frames)"),
        ("log", "Done. 1 video(s) ready."),
    ]


def test_a_score_edit_during_render_marks_the_outputs_stale(client, song, monkeypatch):
    def fake(song_dir, cleaned, name, *, quality="4k", hardware_encoding=True,
             initial_bpm=None,
             log=lambda m: None,
             progress=lambda m: None):
        with open(cleaned, "a") as f:
            f.write("\n")
        out = os.path.join(song_dir, "media", "video")
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, f"{name} ALL.mp4")
        open(path, "wb").close()
        return [path]

    monkeypatch.setattr(pipeline, "run_scroll_video", fake)
    client.post(f"/api/songs/{song.slug}/record", json={})
    data = _finished(client, song.slug)

    assert data["record"]["rendered_against"] != data["verification_summary"]["cleaned_fingerprint"]
    assert data["verification_summary"]["media"]["status"] == "stale"


def test_clean_and_render_cannot_overlap(client, song):
    job_state.start(song.dir, "clean")
    response = client.post(f"/api/songs/{song.slug}/record", json={})
    assert response.status_code == 409
    job_state.finish(song.dir, "clean", error="test cleanup")


def test_upload_uses_the_exact_recorded_output_manifest(client, song, monkeypatch):
    video = song.path("media", "video")
    os.makedirs(video)
    chosen = song.path("media", "video", f"{song.slug} T1.mp4")
    stale = song.path("media", "video", f"{song.slug} old.mov")
    open(chosen, "wb").close()
    open(stale, "wb").close()
    song.data["record"] = {"outputs": [os.path.basename(chosen)]}
    song.save()
    seen = {}

    import src.stemmanauha.create_video as create_video

    def fake_run(**kwargs):
        seen.update(kwargs)
        return [chosen]

    monkeypatch.setattr(create_video, "run", fake_run)
    client.post(f"/api/songs/{song.slug}/record", json={"upload_only": True})
    _finished(client, song.slug)
    assert seen["existing_outputs"] == [chosen]
    assert stale not in seen["existing_outputs"]


def test_persisting_a_log_cannot_abort_the_reported_work(song, monkeypatch):
    monkeypatch.setattr(job_state, "append", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    server._job_emit(song.slug, "render", "still rendering")


def test_progress_does_not_load_song_state_that_another_thread_may_be_saving(
        song, monkeypatch):
    seen = {}
    monkeypatch.setattr(server, "_require",
                        lambda slug: pytest.fail("progress must not parse .song.json"))
    monkeypatch.setattr(job_state, "append",
                        lambda directory, kind, line, entry_type: seen.update(
                            directory=directory, kind=kind, line=line, entry_type=entry_type))

    server._job_emit(song.slug, "render", "Rendering video: 50%", "progress")

    assert seen == {
        "directory": song.dir,
        "kind": "render",
        "line": "Rendering video: 50%",
        "entry_type": "progress",
    }


def test_asking_for_the_screen_recorder_still_gets_it(client, song, monkeypatch):
    called = {}

    def fake_run(**kwargs):
        called.update(kwargs)
        return []

    import src.stemmanauha.create_video as create_video
    monkeypatch.setattr(create_video, "run", fake_run)
    monkeypatch.setattr(pipeline, "run_scroll_video",
                        lambda *a, **k: pytest.fail("scrolling renderer ran instead"))

    client.post(f"/api/songs/{song.slug}/record", json={"renderer": "screen"})
    data = _finished(client, song.slug)
    assert called, "the screen recorder should have been invoked"
    assert called["redo_mp3"] is True, "a score with no prior provenance needs fresh audio"
    assert called["redo_video"] is True
    assert data["record"]["renderer"] == "screen"


def test_screen_recorder_reuses_mp3_when_the_score_is_unchanged(
        client, song, monkeypatch):
    fingerprint = state.file_fingerprint(song.cleaned_path())
    song.data["record"] = {"rendered_against": fingerprint, "outputs": ["old.mov"]}
    song.save()
    called = {}

    import src.stemmanauha.create_video as create_video
    monkeypatch.setattr(create_video, "run",
                        lambda **kwargs: called.update(kwargs) or [])

    client.post(f"/api/songs/{song.slug}/record", json={"renderer": "screen"})
    _finished(client, song.slug)

    assert called["redo_mp3"] is False
    assert called["redo_video"] is False


def test_screen_recorder_refreshes_audio_and_video_once_after_a_score_change(
        client, song, monkeypatch):
    old = state.file_fingerprint(song.cleaned_path())
    song.data["record"] = {"rendered_against": old, "outputs": ["old.mov"]}
    song.save()
    with open(song.cleaned_path(), "a") as changed:
        changed.write("\n")
    song.data["review"] = {"approved_against": state.file_fingerprint(song.cleaned_path())}
    song.save()
    calls = []

    import src.stemmanauha.create_video as create_video
    monkeypatch.setattr(create_video, "run",
                        lambda **kwargs: calls.append(kwargs) or [])

    client.post(f"/api/songs/{song.slug}/record", json={"renderer": "screen"})
    first = _finished(client, song.slug)
    client.post(f"/api/songs/{song.slug}/record", json={"renderer": "screen"})
    _finished(client, song.slug)

    assert (calls[0]["redo_mp3"], calls[0]["redo_video"]) == (True, True)
    assert (calls[1]["redo_mp3"], calls[1]["redo_video"]) == (False, False)
    assert first["record"]["audio_rendered_against"] == state.file_fingerprint(song.cleaned_path())
    assert first["record"]["video_rendered_against"] == state.file_fingerprint(song.cleaned_path())


def test_switching_from_scroll_does_not_reuse_untracked_screen_ingredients(
        client, song, monkeypatch):
    current = state.file_fingerprint(song.cleaned_path())
    song.data["record"] = {
        "renderer": "scroll", "rendered_against": current, "outputs": ["scroll.mp4"]}
    song.save()
    called = {}

    import src.stemmanauha.create_video as create_video
    monkeypatch.setattr(create_video, "run",
                        lambda **kwargs: called.update(kwargs) or [])

    client.post(f"/api/songs/{song.slug}/record", json={"renderer": "screen"})
    _finished(client, song.slug)

    assert (called["redo_mp3"], called["redo_video"]) == (True, True)


def test_progress_reporting_cannot_kill_the_render():
    """A render runs for minutes in a worker thread while the browser may come and
    go. Emitting progress to a loop that has closed must not surface there and
    abort work that is going perfectly well."""
    import asyncio

    hub = server.Hub()
    loop = asyncio.new_event_loop()
    loop.close()
    hub.loop = loop

    hub.emit("mysong", {"type": "log", "line": "still going"})   # must not raise
    assert hub.loop is None or hub.loop.is_closed()


def test_rendering_without_a_cleaned_score_is_refused(client, song, monkeypatch):
    os.remove(song.path("mysong_cleaned.mscx"))
    song.data.pop("cleaned", None)
    song.save()
    monkeypatch.setattr(pipeline, "run_scroll_video",
                        lambda *a, **k: pytest.fail("should not render without a score"))

    response = client.post(f"/api/songs/{song.slug}/record", json={})
    assert response.status_code == 400
    assert "clean" in response.json()["detail"].lower()
    assert client.get(f"/api/songs/{song.slug}").json()["stage"] != "upload"
