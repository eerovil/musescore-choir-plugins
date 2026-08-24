"""Per-voice mixes are a volume edit on the score, not a GUI session."""

import os
import wave

from lxml import etree

from src.scrollvideo import audio
from src.scrollvideo.audio import (BACKGROUND_VOLUME, FOCUS_VOLUME, VOLUME_CTRL,
                                   part_names, prune_mix_cache, render_mix_cached,
                                   set_mix)

SCORE = """<museScore><Score>
  <Part><trackName>S1</trackName><Instrument><Channel>
    <program value="0"/><controller ctrl="10" value="63"/></Channel></Instrument></Part>
  <Part><trackName>B1</trackName><Instrument><Channel>
    <program value="0"/><controller ctrl="10" value="63"/></Channel></Instrument></Part>
</Score></museScore>"""


def _volumes(root):
    return {(p.findtext("trackName") or "").strip():
            next(c.get("value") for c in p.iter("controller") if c.get("ctrl") == VOLUME_CTRL)
            for p in root.iter("Part")}


def test_part_names_follow_score_order():
    assert part_names(etree.fromstring(SCORE)) == ["S1", "B1"]


def test_focus_part_is_loud_and_the_rest_are_background():
    root = set_mix(etree.fromstring(SCORE), "B1")
    assert _volumes(root) == {"S1": str(BACKGROUND_VOLUME), "B1": str(FOCUS_VOLUME)}


def test_no_focus_means_an_even_mix():
    root = set_mix(etree.fromstring(SCORE), None)
    assert set(_volumes(root).values()) == {str(FOCUS_VOLUME)}


def test_existing_volume_is_replaced_not_duplicated():
    root = set_mix(set_mix(etree.fromstring(SCORE), "S1"), "B1")
    for part in root.iter("Part"):
        volumes = [c for c in part.iter("controller") if c.get("ctrl") == VOLUME_CTRL]
        assert len(volumes) == 1
    assert _volumes(root)["B1"] == str(FOCUS_VOLUME)


def test_pan_and_program_are_left_alone():
    root = set_mix(etree.fromstring(SCORE), "S1")
    channel = next(root.iter("Channel"))
    assert channel.find("program") is not None
    assert [c.get("value") for c in channel.findall("controller") if c.get("ctrl") == "10"] == ["63"]


def _write_wav(path):
    with wave.open(os.fspath(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\0\0" * 10)


def test_unchanged_audio_mix_is_reused(tmp_path, monkeypatch):
    score = tmp_path / "score.mscx"
    score.write_text(SCORE)
    rendered = []

    def fake_render(_score, focus, out, **_volumes):
        rendered.append(focus)
        _write_wav(out)
        return out

    monkeypatch.setattr(audio, "render_mix", fake_render)
    first, first_reused = render_mix_cached(str(score), "S1", str(tmp_path / "cache"))
    second, second_reused = render_mix_cached(str(score), "S1", str(tmp_path / "cache"))

    assert first == second
    assert (first_reused, second_reused) == (False, True)
    assert rendered == ["S1"]


def test_score_or_mix_change_invalidates_cached_audio(tmp_path, monkeypatch):
    score = tmp_path / "score.mscx"
    score.write_text(SCORE)
    rendered = []
    monkeypatch.setattr(audio, "render_mix", lambda _score, focus, out, **_volumes:
                        rendered.append(focus) or _write_wav(out) or out)

    render_mix_cached(str(score), "S1", str(tmp_path / "cache"))
    score.write_text(SCORE.replace("B1", "A1"))
    render_mix_cached(str(score), "S1", str(tmp_path / "cache"))
    render_mix_cached(str(score), "S1", str(tmp_path / "cache"), background_volume=20)

    assert rendered == ["S1", "S1", "S1"]


def test_corrupt_cached_audio_is_rendered_again(tmp_path, monkeypatch):
    score = tmp_path / "score.mscx"
    score.write_text(SCORE)
    rendered = []

    def fake_render(_score, focus, out, **_volumes):
        rendered.append(focus)
        _write_wav(out)
        return out

    monkeypatch.setattr(audio, "render_mix", fake_render)
    cached, _ = render_mix_cached(str(score), "S1", str(tmp_path / "cache"))
    with open(cached, "wb") as broken:
        broken.write(b"not a wav")

    _, reused = render_mix_cached(str(score), "S1", str(tmp_path / "cache"))

    assert reused is False
    assert rendered == ["S1", "S1"]


def test_only_the_current_audio_generation_is_kept(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    current = cache / "current.wav"
    obsolete = cache / "obsolete.wav"
    note = cache / "README"
    for path in (current, obsolete, note):
        path.write_bytes(b"cache")

    prune_mix_cache(str(cache), {str(current)})

    assert current.exists()
    assert not obsolete.exists()
    assert note.exists()
