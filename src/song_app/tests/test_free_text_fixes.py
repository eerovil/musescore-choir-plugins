"""A recorded fix that is just a sentence, seen from the app.

Cleaning cannot carry one out. What it must not do is either of the two easy
things: refuse to clean at all, or pass over it in silence — the second is the
exact failure fixes.json exists to prevent.
"""
import json
import os

import pytest
from lxml import etree

from src.song_app import pipeline

SAID = "B1 bar 40, last eighth: drop the D, keep the C — the basses cross here."


@pytest.fixture
def song(tmp_path):
    """A song folder with a one-bar cleaned score in it."""
    cleaned = tmp_path / "song_cleaned.mscx"
    cleaned.write_text(
        "<museScore><Score><Part><trackName>B1</trackName><Staff id='3'/></Part>"
        "<Staff id='3'><Measure><voice><Chord><durationType>quarter</durationType>"
        "<Note><pitch>60</pitch></Note></Chord></voice></Measure></Staff>"
        "</Score></museScore>", encoding="utf-8")
    return tmp_path, str(cleaned)


def _write(song_dir, entries):
    with open(os.path.join(str(song_dir), "fixes.json"), "w", encoding="utf-8") as f:
        json.dump(entries, f)


def test_cleaning_says_the_sentence_is_still_outstanding(song):
    song_dir, cleaned = song
    _write(song_dir, [{"kind": "text", "what": SAID}])
    logged = []
    applied = pipeline.apply_recorded_fixes(cleaned, str(song_dir), logged.append)

    assert applied == 0                        # nothing claims to have run
    assert SAID in "\n".join(logged)           # but the log says so out loud
    assert "NOT applied" in "\n".join(logged)


def test_the_score_is_left_exactly_as_it_was(song):
    song_dir, cleaned = song
    before = open(cleaned, encoding="utf-8").read()
    _write(song_dir, [{"kind": "text", "what": SAID}])
    pipeline.apply_recorded_fixes(cleaned, str(song_dir), lambda _m: None)
    assert open(cleaned, encoding="utf-8").read() == before


def test_a_sentence_does_not_stop_a_fix_that_does_apply(song):
    song_dir, cleaned = song
    _write(song_dir, [
        {"kind": "text", "what": SAID},
        {"kind": "append", "staff": 3, "measure": 1,
         "from": ["quarter:60"], "add": ["quarter:57"], "why": "the scan lost it"},
    ])
    assert pipeline.apply_recorded_fixes(cleaned, str(song_dir), lambda _m: None) == 1
    pitches = [n.findtext("pitch") for n in etree.parse(cleaned).getroot().findall(".//Note")]
    assert pitches == ["60", "57"]


def test_the_fix_stage_reads_the_sentences_live(song):
    """Live off the file, so writing one shows at once and applying it stops showing."""
    song_dir, _ = song
    assert pipeline.free_text_fixes(str(song_dir)) == []
    _write(song_dir, [{"kind": "text", "what": SAID},
                      {"kind": "undot", "staff": 3, "measure": 1, "index": 0}])
    assert pipeline.free_text_fixes(str(song_dir)) == [SAID]
    _write(song_dir, [])
    assert pipeline.free_text_fixes(str(song_dir)) == []


def test_a_broken_file_is_the_cleans_problem_not_the_panels(song):
    """Showing the Fix stage must not blow up; cleaning is where this gets said."""
    song_dir, cleaned = song
    with open(os.path.join(str(song_dir), "fixes.json"), "w", encoding="utf-8") as f:
        f.write("{not json")
    assert pipeline.free_text_fixes(str(song_dir)) == []
    with pytest.raises(RuntimeError):
        pipeline.apply_recorded_fixes(cleaned, str(song_dir), lambda _m: None)


def test_a_sentence_that_says_nothing_fails_the_clean(song):
    song_dir, cleaned = song
    _write(song_dir, [{"kind": "text", "what": ""}])
    with pytest.raises(RuntimeError):
        pipeline.apply_recorded_fixes(cleaned, str(song_dir), lambda _m: None)
