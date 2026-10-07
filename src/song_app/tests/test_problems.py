"""Every problem in one list, each with its choices (#290).

What the Fix panel shows is built here, so this pins what a row is: one per bar and
part, everything wrong there said once, homr's other lengths and other pitches offered
beside it, and a slur the scan ran between two singers offered back in either, both or
neither. Then the answers: each goes onto the score and into fixes.json, and the slur
answer comes back on a re-clean.
"""
import json
from fractions import Fraction

import pytest
from lxml import etree

from src.clean_score.tests.test_cross_voice_slurs import _score as _slur_score
from src.clean_score.utils import score_fixes
from src.clean_score.utils.cross_voice_slurs import (
    _bar_lengths, _resolve, drop_cross_voice_slurs, removed_slurs, store_removed)
from src.clean_score.utils.problem_marks import mark_bar, marks
from src.clean_score.utils.score_fixes import FixError
from src.song_app import bar_readings, pipeline, problems, state
from src.song_app.tests.test_bar_readings import (  # noqa: F401 - make_song is a fixture
    READINGS, _fixes, _score, make_song)

# homr's second guess at bar 2's D3: an E flat, or a C.
NOTES = {**READINGS, "version": 2, "notes": [
    {"part": 0, "staff": 1, "bar": 2, "voice": "1", "moment": 1, "chord": 0,
     "moments": READINGS["bars"][0]["moments"],
     "pitches": [{"step": "D", "alter": 0, "octave": 3, "probability": None},
                 {"step": "E", "alter": -1, "octave": 3, "probability": 0.3},
                 {"step": "C", "alter": 0, "octave": 3, "probability": 0.05}]}]}


def _pitch(song, staff=1, measure=3, index=1):
    root = etree.parse(song.cleaned_path()).getroot()
    note = score_fixes._chords(root, staff, measure)[index].find("Note")
    return int(note.findtext("pitch")), int(note.findtext("tpc"))


# ---------------------------------------------------------------- the list


def test_one_row_per_bar_and_part_with_both_choices(make_song):
    song = make_song(readings=NOTES)
    [row] = problems.problems(song)
    assert (row["measure"], row["part"]) == (3, "B1")
    assert [c["kind"] for c in row["choices"]] == ["rhythm", "pitch"]
    pitch = row["choices"][1]
    assert [o["label"] for o in pitch["options"]] == ["D3", "Eb3", "C3"]
    assert pitch["options"][0]["current"]
    assert all(o["svg"].endswith(f"/{o['letter']}.svg") for o in pitch["options"])


def test_a_mark_and_its_health_row_are_said_once(make_song):
    song = make_song(readings=None)
    root = etree.parse(song.cleaned_path()).getroot()
    mark_bar(root.findall(".//Score/Staff")[0].findall("Measure")[2], "pitch?")
    etree.ElementTree(root).write(song.cleaned_path(), encoding="UTF-8")
    song.data["health"] = {"issues": [
        {"id": "marked-m3-s1", "kind": "marked-problem", "measure": 3, "staff": "B1",
         "detail": "pitch?", "status": "open"},
        {"id": "malformed-m2-s1-v0", "kind": "malformed-measure", "measure": 2, "staff": "B1",
         "detail": "voice 1 is short", "status": "open"}]}
    song.save()
    with open(song.path("fixes.json"), "w") as fh:
        json.dump([{"kind": "text", "source": pipeline.CLEAN_MARK_SOURCE, "measure": 3, "staff": 1,
                    "what": "Bar 3, B1 (red mark in the score): pitch?"},
                   {"kind": "text", "what": "The tenors share the bass words in bar 9."}], fh)
    rows = problems.problems(song)
    assert [(r["measure"], r["part"]) for r in rows] == [(2, "B1"), (3, "B1"), (None, "")]
    assert rows[1]["notes"] == [{"text": "pitch?", "kind": "mark", "dismiss": "marked-m3-s1"}]
    assert rows[0]["notes"][0]["dismiss"] == "malformed-m2-s1-v0"
    assert rows[2]["notes"][0]["text"].startswith("The tenors")


def test_a_dismissed_mark_stays_dismissed(make_song):
    song = make_song(readings=None)
    root = etree.parse(song.cleaned_path()).getroot()
    mark_bar(root.findall(".//Score/Staff")[0].findall("Measure")[2], "pitch?")
    etree.ElementTree(root).write(song.cleaned_path(), encoding="UTF-8")
    song.data["health"] = {"issues": [
        {"id": "marked-m3-s1", "kind": "marked-problem", "measure": 3, "staff": "B1",
         "detail": "pitch?", "status": "dismissed"}]}
    song.save()
    assert problems.problems(song) == []


# ---------------------------------------------------------------- a pitch


def test_a_pitch_pick_changes_that_note_and_is_replayed(make_song):
    song = make_song(readings=NOTES)
    [offer] = [o for o in bar_readings.offers(song) if o["kind"] == "pitch"]
    assert (offer["index"], offer["was"]) == (1, 50)
    bar_readings.record_pick(song, offer["id"], "b")
    assert _pitch(song) == (51, 11)  # E flat, spelt as one
    [entry] = _fixes(song)
    assert entry["kind"] == "pitch" and entry["to"] == 51 and entry["was"] == 50
    # Decided, and the bar's lengths are still on offer though the bar now reads differently.
    kinds = {o["kind"]: o for o in bar_readings.offers(song)}
    assert kinds["pitch"]["decision"] == {"picked": "b"}
    assert kinds["rhythm"]["decision"] is None
    # A rebuild from the scan gets it back.
    with open(song.cleaned_path(), "w") as fh:
        fh.write(_score(((1, "B1", 0),)))
    pipeline.apply_recorded_fixes(song.cleaned_path(), song.dir)
    assert _pitch(song) == (51, 11)


def test_a_pitch_offer_follows_an_octave_shifted_tenor(make_song):
    song = make_song(staves=((1, "T1", 12),), readings=NOTES)
    [offer] = [o for o in bar_readings.offers(song) if o["kind"] == "pitch"]
    assert offer["was"] == 62
    assert [o["to"] for o in offer["options"]] == [62, 63, 60]


def test_a_rhythm_pick_keeps_the_pitch_offer(make_song):
    song = make_song(readings=NOTES)
    rhythm = next(o for o in bar_readings.offers(song) if o["kind"] == "rhythm")
    bar_readings.record_pick(song, rhythm["id"], "b")
    pitch = next(o for o in bar_readings.offers(song) if o["kind"] == "pitch")
    assert pitch["decision"] is None and pitch["staff"] == 1
    bar_readings.record_pick(song, pitch["id"], "c")
    assert _pitch(song) == (48, 14)
    # Both replay, in the order they were made.
    with open(song.cleaned_path(), "w") as fh:
        fh.write(_score(((1, "B1", 0),)))
    pipeline.apply_recorded_fixes(song.cleaned_path(), song.dir)
    assert _pitch(song) == (48, 14)


def test_an_option_is_engraved_with_the_other_pitch(make_song):
    pytest.importorskip("verovio")
    song = make_song(readings=NOTES)
    offer = next(o for o in bar_readings.offers(song) if o["kind"] == "pitch")
    svg = bar_readings.option_svg(song, offer["id"], "b")
    assert svg.startswith("<?xml") or "<svg" in svg[:400]
    assert "#1f6fd1" in svg


# ---------------------------------------------------------------- a slur


def _slur_song(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "SONGS_DIR", str(tmp_path))
    song = state.create("Slurred", per_system=False)
    root = _slur_score()
    store_removed(root, drop_cross_voice_slurs(root))
    etree.ElementTree(root).write(song.path("song_cleaned.mscx"), encoding="UTF-8")
    song.data["cleaned"] = "song_cleaned.mscx"
    song.save()
    return song


def test_a_removed_slur_is_one_question_on_its_first_bar(tmp_path, monkeypatch):
    song = _slur_song(tmp_path, monkeypatch)
    rows = problems.problems(song)
    assert [(r["measure"], r["part"]) for r in rows] == [(1, "T1")]
    [row] = rows
    assert [n["text"] for n in row["notes"]] == [
        "slur to T2 bar 2 removed; check the page",
        "Bar 2, T2: slur from T1 bar 1 removed; check the page"]
    [choice] = row["choices"]
    assert choice["kind"] == "slur"
    assert [o["label"] for o in choice["options"]] == [
        "Slur in T1 (E4 → E4)", "Slur in T2 (C4 → C4)", "Slur in both T1 and T2",
        "No slur here on the page"]


def test_a_slur_answer_draws_it_across_the_barline_and_comes_back(tmp_path, monkeypatch):
    song = _slur_song(tmp_path, monkeypatch)
    [choice] = problems.problems(song)[0]["choices"]
    problems.record_slur_choice(song, choice["id"], "b")  # in T2

    root = etree.parse(song.cleaned_path()).getroot()
    assert marks(root) == []
    staff = root.findall(".//Score/Staff")[1]
    [head] = [sp for sp in staff.iter("Spanner") if sp.find("next") is not None]
    loc = head.find("next/location")
    assert (loc.findtext("measures"), loc.findtext("fractions")) == ("1", "-3/4")
    assert _resolve(0, Fraction(3, 4), loc, _bar_lengths(staff)) == (1, Fraction(0))
    # Answered, so the row has nothing left to ask.
    [row] = problems.problems(song)
    assert row["notes"] == [] and row["choices"][0]["decision"] == {"picked": "b"}

    # A re-clean takes the slur out and marks the bars again; the answer puts it back.
    rebuilt = _slur_score()
    store_removed(rebuilt, drop_cross_voice_slurs(rebuilt))
    etree.ElementTree(rebuilt).write(song.cleaned_path(), encoding="UTF-8")
    pipeline.apply_recorded_fixes(song.cleaned_path(), song.dir)
    again = etree.parse(song.cleaned_path()).getroot()
    assert marks(again) == []
    assert len([sp for sp in again.findall(".//Score/Staff")[1].iter("Spanner")
                if sp.find("next") is not None]) == 1


def test_no_slur_takes_the_marks_off_and_adds_nothing(tmp_path, monkeypatch):
    song = _slur_song(tmp_path, monkeypatch)
    before = len(list(etree.parse(song.cleaned_path()).getroot().iter("Spanner")))
    [choice] = problems.problems(song)[0]["choices"]
    problems.record_slur_choice(song, choice["id"], "d")
    root = etree.parse(song.cleaned_path()).getroot()
    assert marks(root) == []
    assert len(list(root.iter("Spanner"))) == before
    assert {f["kind"] for f in _fixes(song)} == {"unmark"}


def test_a_slur_answered_twice_or_unknown_is_refused(tmp_path, monkeypatch):
    song = _slur_song(tmp_path, monkeypatch)
    [choice] = problems.problems(song)[0]["choices"]
    with pytest.raises(FixError):
        problems.record_slur_choice(song, choice["id"], "z")
    with pytest.raises(FixError):
        problems.record_slur_choice(song, "slur-9-9-0-9-9-0", "a")
    assert _fixes(song) == []
    problems.record_slur_choice(song, choice["id"], "a")
    with pytest.raises(FixError):
        problems.record_slur_choice(song, choice["id"], "b")


def test_the_removed_slurs_travel_in_the_score():
    root = _slur_score()
    records = drop_cross_voice_slurs(root)
    store_removed(root, records)
    again = etree.fromstring(etree.tostring(root))
    assert removed_slurs(again) == records
    store_removed(again, [])
    assert removed_slurs(again) == [] and again.find(".//metaTag") is None


def test_unmark_is_content_when_the_mark_is_already_gone():
    root = _slur_score()
    done = score_fixes.apply_fixes(root, [
        {"kind": "unmark", "staff": 1, "measure": 1, "text": "nothing here", "why": "x"}])
    assert "no red mark left" in done[0]


def test_a_pitch_pick_leaves_the_rest_of_the_bars_marks(make_song):
    """A bar homr doubted for two things is still listed for the one not answered."""
    song = make_song(readings=NOTES)
    root = etree.parse(song.cleaned_path()).getroot()
    bar = root.findall(".//Score/Staff")[0].findall("Measure")[2]
    mark_bar(bar, "pitch? rhythm?")
    mark_bar(bar, "slur to B2 bar 4 removed; check the page")
    etree.ElementTree(root).write(song.cleaned_path(), encoding="UTF-8")
    pitch = next(o for o in bar_readings.offers(song) if o["kind"] == "pitch")
    done = bar_readings.record_pick(song, pitch["id"], "b")
    assert "took pitch? off" in done["applied"]
    left = sorted(m["text"] for m in marks(etree.parse(song.cleaned_path()).getroot()))
    assert left == ["rhythm?", "slur to B2 bar 4 removed; check the page"]
    [row] = problems.problems(song)
    assert sorted(n["text"] for n in row["notes"]) == left
    assert next(c for c in row["choices"] if c["kind"] == "rhythm")["decision"] is None


def test_a_pitch_pick_answering_the_whole_mark_takes_it_off(make_song):
    song = make_song(readings=NOTES)
    root = etree.parse(song.cleaned_path()).getroot()
    mark_bar(root.findall(".//Score/Staff")[0].findall("Measure")[2], "pitch? accidental?")
    etree.ElementTree(root).write(song.cleaned_path(), encoding="UTF-8")
    pitch = next(o for o in bar_readings.offers(song) if o["kind"] == "pitch")
    bar_readings.record_pick(song, pitch["id"], "b")
    assert marks(etree.parse(song.cleaned_path()).getroot()) == []
