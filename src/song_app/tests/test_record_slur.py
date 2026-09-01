"""Recording a missing slur from the app instead of hand-editing fixes.json.

A slur is the one repair the cleaning pipeline will never make for itself: it joins
different pitches, so it cannot be pitch-checked, and mirroring one voice's onto
another produces false positives. The judgement is a person's, and the only place
it survives a re-clean is `fixes.json`.

What these pin is the pair that has to happen together — the entry is written *and*
the score is changed — and that neither happens when the entry is refused.
"""
import json
import os

import pytest
from lxml import etree

from src.clean_score import lyric_txt
from src.clean_score.utils.score_fixes import FixError
from src.song_app import pipeline

# Herää Suomi!, bar 8, Tenor 1 as the scan left it, plus a plain second bar.
SCORE = """<museScore><Score>
<Part><trackName>T1</trackName><Staff id="1"/></Part>
<Part><trackName>T2</trackName><Staff id="2"/></Part>
<Part><trackName>Click</trackName><Staff id="3"/></Part>
<Staff id="1">
  <Measure><voice>
    <Chord><durationType>whole</durationType><Note><pitch>62</pitch><tpc>16</tpc></Note></Chord>
  </voice></Measure>
  <Measure><voice>
    <Rest><durationType>half</durationType></Rest>
    <Chord><durationType>quarter</durationType><Note><pitch>62</pitch><tpc>16</tpc></Note></Chord>
    <Chord><durationType>eighth</durationType><dots>1</dots>
           <Note><pitch>63</pitch><tpc>11</tpc></Note></Chord>
    <Chord><durationType>16th</durationType><Note><pitch>62</pitch><tpc>16</tpc></Note></Chord>
  </voice></Measure>
</Staff>
<Staff id="2">
  <Measure><voice><Rest><durationType>whole</durationType></Rest></voice></Measure>
  <Measure><voice><Rest><durationType>whole</durationType></Rest></voice></Measure>
</Staff>
<Staff id="3">
  <Measure><voice><Rest><durationType>whole</durationType></Rest></voice></Measure>
  <Measure><voice><Rest><durationType>whole</durationType></Rest></voice></Measure>
</Staff>
</Score></museScore>"""

WHY = "Page 1 system 2, bar 8: the tenor slurs E flat to D over one syllable."


@pytest.fixture
def song(tmp_path):
    cleaned = tmp_path / "song_cleaned.mscx"
    cleaned.write_text(SCORE, encoding="utf-8")
    return str(tmp_path), str(cleaned)


def _fixes(song_dir):
    path = os.path.join(song_dir, "fixes.json")
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []


def test_the_bar_comes_back_as_notes_a_person_can_point_at(song):
    _, cleaned = song
    bar = pipeline.bar_for_fix(cleaned, 1, 2)
    assert [n["name"] for n in bar["notes"]] == ["D4", "Eb4", "D4"]
    assert [n["carries_syllable"] for n in bar["notes"]] == [True, True, True]
    assert bar["syllables"] == 3


def test_the_parts_offered_leave_out_the_click_staff(song):
    """The spacer a recording adds has nothing to sing, so nothing to slur either."""
    _, cleaned = song
    parts, measures = pipeline.score_parts_and_measures(cleaned)
    assert [p["name"] for p in parts] == ["T1", "T2"]
    assert measures == 2


def test_recording_a_slur_writes_the_entry_and_changes_the_score(song):
    song_dir, cleaned = song
    done = pipeline.record_slur_fix(song_dir, cleaned, 1, 2, 1, 1, WHY)

    assert _fixes(song_dir) == [
        {"kind": "slur", "staff": 1, "measure": 2, "index": 1, "span": 1, "why": WHY}]
    assert "slurred" in done["applied"]
    root = etree.parse(cleaned).getroot()
    assert root.findall(".//Spanner[@type='Slur']")


def test_the_bar_then_carries_one_syllable_fewer(song):
    """The whole point: `slot_counts` stops asking for a word on the second note."""
    song_dir, cleaned = song
    before = lyric_txt.slot_counts(etree.parse(cleaned).getroot())[1][2]
    pipeline.record_slur_fix(song_dir, cleaned, 1, 2, 1, 1, WHY)
    after = lyric_txt.slot_counts(etree.parse(cleaned).getroot())[1][2]

    assert (before, after) == (3, 2)
    assert pipeline.bar_for_fix(cleaned, 1, 2)["syllables"] == 2


def test_the_recorded_slur_survives_a_rebuild(song):
    """A clean rebuilds from the scan, so the entry — not the edit — is what lasts."""
    song_dir, cleaned = song
    pipeline.record_slur_fix(song_dir, cleaned, 1, 2, 1, 1, WHY)
    rebuilt = os.path.join(song_dir, "rebuilt_cleaned.mscx")
    with open(rebuilt, "w", encoding="utf-8") as fh:
        fh.write(SCORE)                       # what cleaning would produce again

    assert pipeline.apply_recorded_fixes(rebuilt, song_dir, lambda _m: None) == 1
    assert lyric_txt.slot_counts(etree.parse(rebuilt).getroot())[1][2] == 2


def test_only_the_new_entry_is_applied(song):
    """`slur` is not idempotent — replaying the file here would double every earlier one."""
    song_dir, cleaned = song
    pipeline.record_slur_fix(song_dir, cleaned, 1, 2, 1, 1, WHY)
    pipeline.record_slur_fix(song_dir, cleaned, 1, 2, 0, 1, "and the page slurs D to E flat too")

    assert len(_fixes(song_dir)) == 2
    # Two slurs, each written as a head on one chord and a tail on the next. Replaying
    # the whole file on the second call would have made it eight.
    assert len(etree.parse(cleaned).getroot().findall(".//Chord/Spanner[@type='Slur']")) == 4


def test_a_slur_already_in_the_score_is_refused(song):
    song_dir, cleaned = song
    pipeline.record_slur_fix(song_dir, cleaned, 1, 2, 1, 1, WHY)
    with pytest.raises(FixError, match="already starts a slur"):
        pipeline.record_slur_fix(song_dir, cleaned, 1, 2, 1, 1, WHY)
    assert len(_fixes(song_dir)) == 1


def test_a_fix_with_no_reason_is_refused(song):
    """The entry is the only thing that will still say what was read on the page."""
    song_dir, cleaned = song
    with pytest.raises(FixError, match="say why"):
        pipeline.record_slur_fix(song_dir, cleaned, 1, 2, 1, 1, "   ")
    assert _fixes(song_dir) == []
    assert not etree.parse(cleaned).getroot().findall(".//Spanner[@type='Slur']")


def test_a_slur_that_runs_past_the_bar_leaves_both_files_alone(song):
    song_dir, cleaned = song
    before = open(cleaned, encoding="utf-8").read()
    with pytest.raises(FixError):
        pipeline.record_slur_fix(song_dir, cleaned, 1, 2, 2, 1, WHY)
    assert _fixes(song_dir) == []
    assert open(cleaned, encoding="utf-8").read() == before


def test_a_bar_that_is_not_there_is_refused(song):
    song_dir, cleaned = song
    with pytest.raises(FixError):
        pipeline.record_slur_fix(song_dir, cleaned, 1, 99, 0, 1, WHY)
    with pytest.raises(FixError):
        pipeline.record_slur_fix(song_dir, cleaned, 1, 2, 0, 0, WHY)
    assert _fixes(song_dir) == []


def test_an_existing_fix_is_kept(song):
    """Appending, not replacing: someone else's page-verified edit stays in the file."""
    song_dir, cleaned = song
    with open(os.path.join(song_dir, "fixes.json"), "w", encoding="utf-8") as fh:
        json.dump([{"kind": "text", "what": "B1 bar 40: the basses cross here."}], fh)
    pipeline.record_slur_fix(song_dir, cleaned, 1, 2, 1, 1, WHY)

    kinds = [f["kind"] for f in _fixes(song_dir)]
    assert kinds == ["text", "slur"]
    assert pipeline.free_text_fixes(song_dir) == ["B1 bar 40: the basses cross here."]


def test_the_fix_stage_can_read_the_slurs_back(song):
    song_dir, cleaned = song
    assert pipeline.recorded_slurs(song_dir) == []
    pipeline.record_slur_fix(song_dir, cleaned, 1, 2, 1, 1, WHY)
    recorded = pipeline.recorded_slurs(song_dir)

    assert [(f["staff"], f["measure"], f["index"], f["span"]) for f in recorded] == [(1, 2, 1, 1)]
    assert recorded[0]["why"] == WHY


def test_syllable_slots_agree_with_the_counts_they_come_from(song):
    """A note in the middle of a slur carries no marker, so a per-note answer cannot
    be read off a chord on its own — it has to come from the same pass as the counts."""
    _, cleaned = song
    root = etree.parse(cleaned).getroot()
    for staff, by_measure in lyric_txt.slot_counts(root).items():
        for measure, count in by_measure.items():
            assert sum(lyric_txt.syllable_slots(root, staff, measure)) == count


def test_a_two_note_slur_silences_both_notes_it_reaches(song):
    """The middle note of a slur carries nothing of its own; the count still drops by two."""
    song_dir, cleaned = song
    pipeline.record_slur_fix(song_dir, cleaned, 1, 2, 0, 2, "the page slurs all three")
    root = etree.parse(cleaned).getroot()

    assert lyric_txt.syllable_slots(root, 1, 2) == [True, False, False]
    assert pipeline.bar_for_fix(cleaned, 1, 2)["syllables"] == 1


def test_slots_for_a_staff_or_bar_that_is_not_there_are_empty(song):
    _, cleaned = song
    root = etree.parse(cleaned).getroot()
    assert lyric_txt.syllable_slots(root, 9, 1) == []
    assert lyric_txt.syllable_slots(root, 1, 99) == []


# --------------------------------------------------------------------------
# The same thing over HTTP, which is how the Fix panel reaches it.
# --------------------------------------------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from src.song_app import server, state

    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    monkeypatch.setenv("MUSESCORE_CLI_PATH", str(tmp_path / "no-such-musescore"))
    created = state.create("Slur Song", per_system=False)
    with open(created.path("slur-song_cleaned.mscx"), "w", encoding="utf-8") as fh:
        fh.write(SCORE)
    created.data["cleaned"] = "slur-song_cleaned.mscx"
    created.data["stage"] = "fix"
    created.save()
    return TestClient(server.app), created


def test_the_route_offers_the_parts_and_the_bar_to_pick_from(client):
    api, song = client
    body = api.get(f"/api/songs/{song.slug}/bar?staff=1&measure=2").json()

    assert [p["name"] for p in body["parts"]] == ["T1", "T2"]
    assert body["measures"] == 2
    assert [n["name"] for n in body["notes"]] == ["D4", "Eb4", "D4"]
    assert body["lyrics_imported"] is False


def test_asking_without_a_bar_still_answers_with_the_choices(client):
    """The panel needs the parts before it can name a bar; one parse answers both."""
    api, song = client
    body = api.get(f"/api/songs/{song.slug}/bar").json()
    assert [p["name"] for p in body["parts"]] == ["T1", "T2"]
    assert "notes" not in body


def test_posting_a_slur_records_it_and_says_so_in_the_state(client):
    api, song = client
    r = api.post(f"/api/songs/{song.slug}/fixes/slur",
                 json={"staff": 1, "measure": 2, "index": 1, "span": 1, "why": WHY})

    assert r.status_code == 200
    assert [f["why"] for f in r.json()["recorded_slurs"]] == [WHY]
    assert _fixes(song.dir)[0]["kind"] == "slur"
    assert api.get(f"/api/songs/{song.slug}/bar?staff=1&measure=2").json()["syllables"] == 2


def test_a_refused_slur_comes_back_as_a_message_not_a_500(client):
    api, song = client
    for body in ({"staff": 1, "measure": 2, "index": 1, "span": 1, "why": ""},
                 {"staff": 1, "measure": 2, "index": 2, "span": 1, "why": WHY},
                 {"staff": 1, "measure": 99, "index": 0, "span": 1, "why": WHY}):
        r = api.post(f"/api/songs/{song.slug}/fixes/slur", json=body)
        assert r.status_code == 400, r.text
        assert r.json()["detail"]
    assert _fixes(song.dir) == []


def test_a_negative_index_leaves_the_fix_file_and_score_unchanged(client):
    api, song = client
    fixes = song.path("fixes.json")
    cleaned = song.cleaned_path()
    before = open(cleaned, encoding="utf-8").read()

    r = api.post(f"/api/songs/{song.slug}/fixes/slur",
                 json={"staff": 1, "measure": 2, "index": -1, "span": 1, "why": WHY})

    assert r.status_code == 400
    assert "no index -1" in r.json()["detail"]
    assert not os.path.exists(fixes)
    assert open(cleaned, encoding="utf-8").read() == before


def test_a_bar_out_of_range_is_a_404(client):
    api, song = client
    assert api.get(f"/api/songs/{song.slug}/bar?staff=1&measure=99").status_code == 404


def test_the_lyrics_warning_is_available_before_anything_is_written(client):
    """Re-slurring shortens the bar, so a line already imported comes back too long."""
    api, song = client
    song.data["lyrics"] = {"json": "lyrics.json"}
    song.save()
    assert api.get(f"/api/songs/{song.slug}/bar").json()["lyrics_imported"] is True


def test_recording_claims_its_own_write(client):
    """Otherwise the file watcher reads the score as edited in MuseScore and rescans."""
    api, song = client
    from src.song_app import state

    api.post(f"/api/songs/{song.slug}/fixes/slur",
             json={"staff": 1, "measure": 2, "index": 1, "span": 1, "why": WHY})
    fresh = state.load(song.slug)
    assert fresh.data["cleaned_fingerprint"] == state.file_fingerprint(fresh.cleaned_path())
