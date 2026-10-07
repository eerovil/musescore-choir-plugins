"""Every problem in one list, each with its choices (#290).

What the Fix panel shows is built here, so this pins what a row is: one per bar and
part, everything wrong there said once, homr's other readings offered beside it as
whole bars — lengths and pitches together, and the second reading (#295) — and a slur the scan ran between two singers offered back in either, both or
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
    READINGS, _fixes, _score, _tokens, make_song)

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


def _bar_of(option):
    return [(m["value"], [bar_readings.pitch_name(p) for p in m["pitches"]])
            for m in option["moments"]]


def test_one_row_per_bar_and_part_with_whole_bars_to_pick(make_song):
    song = make_song(readings=NOTES)
    [row] = problems.problems(song)
    assert (row["measure"], row["part"]) == (3, "B1")
    [choice] = row["choices"]
    assert choice["kind"] == "bar" and choice["shown"] == 6
    # Three readings of the lengths times three pitches for the D: nine bars.
    assert [o["letter"] for o in choice["options"]] == list("abcdefghi")
    assert choice["options"][0]["current"]
    assert all(o["svg"].endswith(f"/{o['letter']}.svg") for o in choice["options"])


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


# ---------------------------------------------------------------- whole bars (#295)

# homr read the crop a second time and got one half note, C3.
SECOND = {**NOTES, "version": 3, "second": [
    {"part": 0, "staff": 1, "bar": 2, "voice": "1", "length": "1/2",
     "moments": READINGS["bars"][0]["moments"],
     "second": [{"kind": "note", "value": "note_2",
                 "pitches": [{"step": "C", "alter": 0, "octave": 3}]}]}]}


def test_whole_bars_are_ranked_lengths_and_pitches_together(make_song):
    [offer] = bar_readings.offers(make_song(readings=NOTES))
    assert [_bar_of(o) for o in offer["options"]][:5] == [
        [("note_4", ["C3"]), ("note_4", ["D3"])],   # a: as read
        [("note_4.", ["C3"]), ("note_8", ["D3"])],  # the next lengths, the D as read
        [("note_4", ["C3"]), ("note_4", ["Eb3"])],  # the lengths as read, the next pitch
        [("note_4.", ["C3"]), ("note_8", ["Eb3"])],
        [("note_8", ["C3"]), ("note_4.", ["D3"])],
    ]
    # The notes that differ from the bar as read are the ones drawn blue.
    assert offer["options"][2]["differs"] == [(1, 0)]


def test_the_second_reading_is_always_b(make_song):
    song = make_song(readings=SECOND)
    [offer] = bar_readings.offers(song)
    b = offer["options"][1]
    assert b["second"] and _bar_of(b) == [("note_2", ["C3"])]
    [row] = problems.problems(song)
    assert row["choices"][0]["options"][1]["label"] == "second reading"


def test_picking_the_second_reading_writes_the_bar_afresh(make_song, tmp_path):
    song = make_song(readings=SECOND)
    [offer] = bar_readings.offers(song)
    bar_readings.record_pick(song, offer["id"], "b")
    assert _tokens(song) == ["half:48"]
    [entry] = _fixes(song)
    assert entry["kind"] == "bar" and entry["letter"] == "b"
    assert "second reading" in entry["why"]
    # The word on the first note stays.
    root = etree.parse(song.cleaned_path()).getroot()
    assert score_fixes._measure(root, 1, 3).findtext(".//Lyrics/text") == "la"
    [offer] = bar_readings.offers(song)
    assert offer["decision"] == {"picked": "b"}
    # A rebuild from the scan gets it back.
    with open(song.cleaned_path(), "w") as fh:
        fh.write(_score(((1, "B1", 0),)))
    assert pipeline.apply_recorded_fixes(song.cleaned_path(), song.dir) == 1
    assert _tokens(song) == ["half:48"]


def test_a_bar_with_another_pitch_is_picked_whole(make_song):
    song = make_song(readings=NOTES)
    [offer] = bar_readings.offers(song)
    bar_readings.record_pick(song, offer["id"], "d")  # dotted quarter, eighth E flat
    assert _tokens(song) == ["quarter.:48", "eighth:51"]
    assert _pitch(song) == (51, 11)  # E flat, spelt as one


def test_a_whole_bar_follows_an_octave_shifted_tenor(make_song):
    song = make_song(staves=((1, "T1", 12),), readings=NOTES)
    [offer] = bar_readings.offers(song)
    assert [m["pitches"] for m in offer["options"][2]["to"]] == [[60], [63]]


def test_a_pick_made_before_whole_bars_counts_as_decided(make_song):
    song = make_song(readings=NOTES)
    [offer] = bar_readings.offers(song)
    with open(song.path("fixes.json"), "w") as fh:
        json.dump([{"kind": "rhythm", "source": "reading", "offer": offer["id"],
                    "system": 2, "content": song.data["scan"]["systems"]["2"]["content"],
                    "staff": 1, "part": "B1", "measure": 3, "from": _tokens(song),
                    "to": ["note_4.", "note_8"], "why": "picked earlier"}], fh)
    [offer] = bar_readings.offers(song)
    assert offer["decision"] == {"picked": "earlier"}


def test_an_option_is_engraved_with_the_other_pitch(make_song):
    pytest.importorskip("verovio")
    song = make_song(readings=NOTES)
    [offer] = bar_readings.offers(song)
    svg = bar_readings.option_svg(song, offer["id"], "c")
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
