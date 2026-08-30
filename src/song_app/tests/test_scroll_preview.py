"""The preview endpoint: what it prepares, what it reuses, and what it never does.

Preparing a preview engraves and rasterises the score, so it is cached — and a
cache is exactly where a preview stops being a preview. These tests hold it to the
one rule that matters: what comes back is this score, under these settings, or it
is prepared again. Since the preview is now pictures, the cache is a folder and
"prepared again" has to mean the old pictures are gone as well.

The drawing itself is pinned in `src/scrollvideo/tests/test_preview.py`; here it is
stubbed, so these run without MuseScore.
"""

import json
import os
import wave

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from src.song_app import pipeline, server, state  # noqa: E402

PAYLOAD = {"duration": 12.0, "frame": {"width": 853, "height": 480},
           "strip": {"width": 1000, "tiles": [{"name": "strip-0.png", "x": 0,
                                               "width": 1000}]},
           "lit": {"tiles": [{"name": "lit-0.png", "x": 0, "width": 1000}]},
           "background": {"tiles": [{"name": "background-0.png", "x": 0,
                                        "width": 1000}]},
           "parts": ["S1", "B1"], "dropped": ["Piano"],
           "scroll": {"times": [0.0], "xs": [0.0], "jump": 1.0}}


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
    s.save()
    return s


@pytest.fixture
def client(song):
    return TestClient(server.app)


@pytest.fixture
def prepared(monkeypatch):
    """Stand in for the drawing: record what it was asked for, leave tiles behind."""
    calls = []

    def fake(mscx_path, out_dir, **kwargs):
        calls.append({"path": mscx_path, "out_dir": out_dir, **kwargs})
        os.makedirs(out_dir, exist_ok=True)
        for tile in ("strip-0.png", "lit-0.png", "background-0.png"):
            with open(os.path.join(out_dir, tile), "wb") as fh:
                fh.write(b"\x89PNG" + str(len(calls)).encode())
        with open(os.path.join(out_dir, "audio-source.mscx"), "wb") as fh:
            fh.write(open(mscx_path, "rb").read())
        return dict(PAYLOAD, call=len(calls))

    monkeypatch.setattr("src.scrollvideo.preview.preview", fake)
    return calls


def test_the_preview_is_the_current_score_at_the_chosen_size(client, song, prepared):
    response = client.get(f"/api/songs/{song.slug}/scroll-preview",
                          params={"quality": "720p", "top_margin": 12,
                                  "bottom_margin": -5})
    assert response.status_code == 200
    assert response.json()["duration"] == 12.0
    assert response.headers["cache-control"] == "no-cache"

    asked = prepared[0]
    assert asked["path"] == song.cleaned_path()
    assert (asked["width"], asked["height"], asked["fps"]) == (1280, 720, 30)
    assert asked["top_margin_percent"] == 12
    assert asked["bottom_margin_percent"] == -5


def test_looking_again_reuses_the_prepared_preview(client, song, prepared):
    first = client.get(f"/api/songs/{song.slug}/scroll-preview").json()
    second = client.get(f"/api/songs/{song.slug}/scroll-preview").json()
    assert first == second
    assert len(prepared) == 1


def test_editing_the_score_throws_the_old_preview_away(client, song, prepared):
    client.get(f"/api/songs/{song.slug}/scroll-preview")
    with open(song.cleaned_path(), "w") as fh:
        fh.write("<museScore><Score><Staff/></Score></museScore>")
    client.get(f"/api/songs/{song.slug}/scroll-preview")
    assert len(prepared) == 2


@pytest.mark.parametrize("params", [
    {"top_margin": 8},
    {"bottom_margin": 8},
    {"quality": "1080p"},
])
def test_a_setting_that_moves_the_picture_throws_it_away_too(client, song, prepared,
                                                             params):
    client.get(f"/api/songs/{song.slug}/scroll-preview")
    client.get(f"/api/songs/{song.slug}/scroll-preview", params=params)
    assert len(prepared) == 2


def test_the_tempo_the_app_supplies_is_part_of_what_the_preview_is_of(client, song,
                                                                     prepared):
    """A score with no opening tempo is previewed at the BPM the panel offers."""
    client.get(f"/api/songs/{song.slug}/scroll-preview", params={"bpm": 96})
    assert prepared[0]["initial_bpm"] == 96
    client.get(f"/api/songs/{song.slug}/scroll-preview", params={"bpm": 120})
    assert prepared[1]["initial_bpm"] == 120


def test_changing_the_musescore_renderer_invalidates_the_preview(song, prepared,
                                                                 monkeypatch):
    monkeypatch.setenv("MUSESCORE_CLI_PATH", "/missing/musescore-one")
    pipeline.scroll_preview(song.dir, song.cleaned_path())
    monkeypatch.setenv("MUSESCORE_CLI_PATH", "/missing/musescore-two")
    pipeline.scroll_preview(song.dir, song.cleaned_path())
    assert len(prepared) == 2


def test_changing_the_spacing_cap_invalidates_the_preview(song, prepared, monkeypatch):
    """A cached picture must not survive a renderer-default spacing change."""
    from src.scrollvideo import spacing
    shipped = spacing.DEFAULT_MAX_RATIO

    pipeline.scroll_preview(song.dir, song.cleaned_path())
    monkeypatch.setattr(spacing, "DEFAULT_MAX_RATIO", 1.6)
    pipeline.scroll_preview(song.dir, song.cleaned_path())

    assert [call["spacing_ratio"] for call in prepared] == [shipped, 1.6]


def test_a_score_with_its_own_tempo_ignores_the_offered_one(client, song, prepared,
                                                            monkeypatch):
    monkeypatch.setattr(pipeline, "has_opening_tempo", lambda _path: True)
    client.get(f"/api/songs/{song.slug}/scroll-preview", params={"bpm": 96})
    assert prepared[0]["initial_bpm"] is None


def test_previewing_does_not_advance_the_song(client, song, prepared):
    """It is a look at the score, not a step in the workflow."""
    client.get(f"/api/songs/{song.slug}/scroll-preview")

    data = client.get(f"/api/songs/{song.slug}").json()
    assert data["stage"] == "record"
    assert not data["recording"]
    assert not data.get("record", {}).get("outputs")


def test_the_framing_a_preview_was_asked_for_is_remembered(client, song, prepared):
    """Nudging a margin and looking is how the choice gets made.

    Everything else about the preview leaves the song alone, but a framing that
    only stuck once you had rendered a video was lost every time.
    """
    client.get(f"/api/songs/{song.slug}/scroll-preview",
               params={"top_margin": 3, "bottom_margin": 11})

    rec = client.get(f"/api/songs/{song.slug}").json()["record"]
    assert (rec["top_margin"], rec["bottom_margin"]) == (3, 11)


def test_a_framing_the_renderer_refuses_is_not_remembered(client, song, monkeypatch):
    """Coming back to a margin that cannot be drawn would be a trap."""
    def refuse(*_args, **_kwargs):
        raise ValueError("top and bottom video margins leave no visible picture")

    monkeypatch.setattr("src.scrollvideo.preview.preview", refuse)
    response = client.get(f"/api/songs/{song.slug}/scroll-preview",
                          params={"bottom_margin": 99})

    assert response.status_code == 400
    assert "bottom_margin" not in client.get(f"/api/songs/{song.slug}").json().get(
        "record", {})


def test_visual_preview_does_not_prepare_any_audio(client, song, prepared, monkeypatch):
    monkeypatch.setattr(
        "src.scrollvideo.audio.render_mix_cached",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("opening the picture must not render a mix")))
    assert client.get(f"/api/songs/{song.slug}/scroll-preview").status_code == 200


@pytest.mark.parametrize(("mix", "focus"), [("ALL", None), ("B1", "B1")])
def test_audio_uses_the_prepared_score_and_requested_mix(song, prepared, monkeypatch,
                                                         mix, focus):
    payload = pipeline.scroll_preview(song.dir, song.cleaned_path())
    seen = {}

    def fake(source, selected, cache):
        seen.update(source=source, focus=selected, cache=cache)
        return os.path.join(cache, "mix.wav"), False

    monkeypatch.setattr("src.scrollvideo.audio.render_mix_cached", fake)
    path, reused = pipeline.scroll_preview_audio(
        song.dir, song.cleaned_path(), mix, payload["revision"])

    assert reused is False
    assert path.endswith("mix.wav")
    assert seen["focus"] == focus
    assert seen["cache"] == song.path("media", ".scrollvideo-audio")
    assert open(seen["source"], "rb").read() == open(song.cleaned_path(), "rb").read()


@pytest.mark.parametrize("mix", ["Unknown", "Piano"])
def test_unknown_or_dropped_audio_mix_is_rejected(song, prepared, monkeypatch, mix):
    payload = pipeline.scroll_preview(song.dir, song.cleaned_path())
    monkeypatch.setattr(
        "src.scrollvideo.audio.render_mix_cached",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an invalid mix must not be rendered")))

    with pytest.raises(ValueError, match="No such preview mix"):
        pipeline.scroll_preview_audio(
            song.dir, song.cleaned_path(), mix, payload["revision"])


def test_audio_refuses_an_open_preview_after_the_score_changes(song, prepared):
    payload = pipeline.scroll_preview(song.dir, song.cleaned_path())
    with open(song.cleaned_path(), "w") as fh:
        fh.write("<museScore><Score><Staff/></Score></museScore>")

    with pytest.raises(ValueError, match="reopen"):
        pipeline.scroll_preview_audio(
            song.dir, song.cleaned_path(), "ALL", payload["revision"])


def test_preview_audio_reuses_the_final_renderer_cache(song, prepared, monkeypatch):
    payload = pipeline.scroll_preview(song.dir, song.cleaned_path())
    rendered = []

    def fake_render(_source, focus, out, **_volumes):
        rendered.append(focus)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with wave.open(out, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8000)
            wav.writeframes(b"\0\0" * 10)
        return out

    monkeypatch.setattr("src.scrollvideo.audio.render_mix", fake_render)
    first = pipeline.scroll_preview_audio(
        song.dir, song.cleaned_path(), "ALL", payload["revision"])
    second = pipeline.scroll_preview_audio(
        song.dir, song.cleaned_path(), "ALL", payload["revision"])

    assert first[0] == second[0]
    assert (first[1], second[1]) == (False, True)
    assert rendered == [None]


def test_audio_completed_after_a_score_edit_is_not_served(song, prepared, monkeypatch):
    payload = pipeline.scroll_preview(song.dir, song.cleaned_path())

    def edit_during_render(_source, _focus, out, **_volumes):
        with open(song.cleaned_path(), "w") as fh:
            fh.write("<museScore><Score><Staff/></Score></museScore>")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with wave.open(out, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8000)
            wav.writeframes(b"\0\0" * 10)
        return out

    monkeypatch.setattr("src.scrollvideo.audio.render_mix", edit_during_render)
    with pytest.raises(ValueError, match="reopen"):
        pipeline.scroll_preview_audio(
            song.dir, song.cleaned_path(), "ALL", payload["revision"])


def test_audio_endpoint_serves_the_lazy_wav(client, song, monkeypatch, tmp_path):
    wav = tmp_path / "mix.wav"
    wav.write_bytes(b"RIFF preview audio")
    seen = {}

    def fake(song_dir, cleaned, mix, revision, **settings):
        seen.update(song_dir=song_dir, cleaned=cleaned, mix=mix,
                    revision=revision, settings=settings)
        return str(wav), True

    monkeypatch.setattr(pipeline, "scroll_preview_audio", fake)
    response = client.get(
        f"/api/songs/{song.slug}/scroll-preview-audio",
        params={"mix": "S1", "revision": "rev", "quality": "720p", "bpm": 96})

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["x-scroll-audio-cache"] == "hit"
    assert response.content == b"RIFF preview audio"
    assert (seen["mix"], seen["revision"]) == ("S1", "rev")
    assert seen["settings"]["initial_bpm"] == 96


def test_what_a_render_would_refuse_comes_back_as_a_message(client, song, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise NotImplementedError("This score uses Jump (a D.C./D.S. jump)")

    monkeypatch.setattr("src.scrollvideo.preview.preview", refuse)
    response = client.get(f"/api/songs/{song.slug}/scroll-preview")
    assert response.status_code == 400
    assert "D.C./D.S." in response.json()["detail"]


def test_there_is_nothing_to_preview_before_the_score_is_cleaned(client, song,
                                                                 prepared):
    song.data.pop("cleaned")
    song.save()
    response = client.get(f"/api/songs/{song.slug}/scroll-preview")
    assert response.status_code == 400
    assert "clean" in response.json()["detail"].lower()
    assert not prepared


@pytest.mark.parametrize("params", [{"top_margin": -41}, {"bottom_margin": 101}])
def test_a_margin_the_renderer_could_not_use_is_refused(client, song, prepared, params):
    response = client.get(f"/api/songs/{song.slug}/scroll-preview", params=params)
    assert response.status_code == 400
    assert not prepared


def _payload_path(song):
    return os.path.join(song.dir, pipeline.PREVIEW_CACHE, pipeline.PREVIEW_PAYLOAD)


def test_a_damaged_cache_file_is_prepared_again_rather_than_served(song, prepared):
    """A half-written file is not a preview; it is just a file."""
    os.makedirs(os.path.join(song.dir, pipeline.PREVIEW_CACHE), exist_ok=True)
    with open(_payload_path(song), "w") as fh:
        fh.write('{"key": "whatever", "prev')
    payload = pipeline.scroll_preview(song.dir, song.cleaned_path())
    assert payload["duration"] == 12.0
    assert json.load(open(_payload_path(song)))["preview"]


def test_the_tiles_the_payload_names_are_the_ones_served(client, song, prepared):
    """The picture is files, so the payload is only half of it."""
    client.get(f"/api/songs/{song.slug}/scroll-preview")
    response = client.get(f"/api/songs/{song.slug}/scroll-preview/strip-0.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-cache"
    assert response.content == b"\x89PNG1"


def test_preparing_again_replaces_the_old_pictures(client, song, prepared):
    """Tile names are positional, so a stale one would be played as this score's."""
    client.get(f"/api/songs/{song.slug}/scroll-preview")
    with open(os.path.join(song.dir, pipeline.PREVIEW_CACHE, "strip-9.png"), "wb") as fh:
        fh.write(b"stale")
    client.get(f"/api/songs/{song.slug}/scroll-preview", params={"top_margin": 8})

    assert len(prepared) == 2
    assert client.get(f"/api/songs/{song.slug}/scroll-preview/strip-0.png").content \
        == b"\x89PNG2"
    assert client.get(f"/api/songs/{song.slug}/scroll-preview/strip-9.png").status_code \
        == 404


def test_a_tile_name_cannot_reach_out_of_the_preview_folder(client, song, prepared):
    """A name off the wire is matched against the folder, never joined onto a path."""
    client.get(f"/api/songs/{song.slug}/scroll-preview")
    for name in ("../.song.json", "..%2F.song.json", "nothing.png"):
        assert client.get(
            f"/api/songs/{song.slug}/scroll-preview/{name}").status_code == 404


def _printed_systems(song, breaks):
    """An input score with printed line breaks; cleaning strips them from the score
    the preview draws, so the app has to read them off this one."""
    from lxml import etree

    root = etree.Element("museScore")
    staff = etree.SubElement(etree.SubElement(root, "Score"), "Staff")
    staff.set("id", "1")
    for i in range(12):
        measure = etree.SubElement(staff, "Measure")
        if i in breaks:
            etree.SubElement(etree.SubElement(measure, "LayoutBreak"),
                             "subtype").text = "line"
    with open(song.path("input.mscx"), "wb") as fh:
        fh.write(etree.tostring(root))
    song.data.setdefault("sources", {})["xml"] = "input.mscx"
    song.save()


def test_the_preview_numbers_the_bars_the_page_started_a_system_on(client, song,
                                                                   prepared):
    """The preview has to be given the same grouping the render is given (#78)."""
    _printed_systems(song, (3, 7))
    client.get(f"/api/songs/{song.slug}/scroll-preview")
    assert prepared[0]["system_starts"] == [0, 4, 8]


def test_relaying_out_the_source_throws_the_preview_away(client, song, prepared):
    """Different systems mean different bar numbers on the page, so it is a new picture."""
    _printed_systems(song, (3, 7))
    client.get(f"/api/songs/{song.slug}/scroll-preview")
    _printed_systems(song, (5,))
    client.get(f"/api/songs/{song.slug}/scroll-preview")

    assert [call["system_starts"] for call in prepared] == [[0, 4, 8], [0, 6]]
