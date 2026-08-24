import json
import os
from types import SimpleNamespace

from src.song_app import state, verification


SCORE = """<museScore><Score>
<Part><trackName>T1</trackName><Staff id="1"/></Part>
<Part><trackName>B1</trackName><Staff id="2"/></Part>
<Staff id="1"><Measure><voice><Chord><Note><pitch>60</pitch></Note></Chord></voice></Measure></Staff>
<Staff id="2"><Measure><voice><Chord><Note><pitch>48</pitch></Note></Chord></voice></Measure></Staff>
</Score></museScore>"""


def _song(tmp_path, monkeypatch):
    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    song = state.create("Verify Me", per_system=False)
    cleaned = song.path("verify_cleaned.mscx")
    with open(cleaned, "w") as f:
        f.write(SCORE)
    song.data["cleaned"] = os.path.basename(cleaned)
    song.data["cleaned_fingerprint"] = state.file_fingerprint(cleaned)
    song.save()
    return song, cleaned


def test_note_comparison_reports_pass_and_difference(tmp_path):
    source = tmp_path / "source.mscx"
    cleaned = tmp_path / "cleaned.mscx"
    source.write_text(SCORE)
    cleaned.write_text(SCORE)
    assert verification.compare_notes(str(source), str(cleaned))["status"] == "passed"

    cleaned.write_text(SCORE.replace("<pitch>48</pitch>", "<pitch>49</pitch>"))
    result = verification.compare_notes(str(source), str(cleaned))
    assert result["status"] == "warning"
    assert result["source_notes"] == result["cleaned_notes"] == 2

    cleaned.write_text(SCORE.replace("<Chord><Note><pitch>48</pitch>",
                                     "<Chord><durationType>half</durationType><Note><pitch>48</pitch>"))
    assert verification.compare_notes(str(source), str(cleaned))["status"] == "warning"


def test_probe_requires_picture_sound_duration_and_reports_metadata(tmp_path, monkeypatch):
    media = tmp_path / "voice.mp4"
    media.write_bytes(b"video")
    payload = {"streams": [
        {"codec_type": "video", "width": 1920, "height": 1080, "avg_frame_rate": "30/1"},
        {"codec_type": "audio"},
    ], "format": {"duration": "12.5"}}
    monkeypatch.setattr(verification.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps(payload), stderr=""))

    result = verification.probe_file(str(media))
    assert result["status"] == "passed"
    assert (result["width"], result["height"], result["fps"], result["duration"]) == \
           (1920, 1080, 30.0, 12.5)
    assert result["video"] and result["audio"]


def test_summary_distinguishes_current_stale_and_not_checked(tmp_path, monkeypatch):
    song, cleaned = _song(tmp_path, monkeypatch)
    current = state.file_fingerprint(cleaned)
    song.data["health"] = {"checked_against": current, "issues": []}
    song.data["verification"] = {"notes": {
        "status": "passed", "detail": "same", "checked_against": current}}
    song.data["lyrics"] = {"imported_against": current, "warnings": []}
    song.save()

    result = verification.summary(song, systems=7)
    assert [result[key]["status"] for key in ("health", "notes", "lyrics")] == \
           ["passed", "passed", "passed"]
    assert result["expected_parts"] == ["T1", "B1"] and result["systems"] == 7
    assert result["media"]["status"] == "not_checked"

    with open(cleaned, "a") as f:
        f.write("\n")
    stale = verification.summary(song, systems=7)
    assert [stale[key]["status"] for key in ("health", "notes", "lyrics")] == \
           ["stale", "stale", "stale"]
