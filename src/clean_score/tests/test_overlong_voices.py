"""Voices that run past their barline: the rule MuseScore 4 refuses a score by (#481).

MuseScore 4 refuses a whole file over one voice longer than its bar and writes the
reason only to its own log file. `overlong_voices` is that rule kept in Python, so the
app can name the bar when the input is converted and check a cleaned score when the
command-line MuseScore is 4, which writes no `.mlog`.
"""

import os
import shutil
import subprocess

import pytest
from lxml import etree

from src import musescore_cli
from src.clean_score.utils.overlong_voices import overlong_voices

TEST_FILES = os.path.join(os.path.dirname(__file__), "test_files")
FIXTURE = os.path.join(TEST_FILES, "overlong_voice.musicxml")


def _chord(kind, dots=0):
    dot = f"<dots>{dots}</dots>" if dots else ""
    return f"<Chord>{dot}<durationType>{kind}</durationType><Note><pitch>60</pitch></Note></Chord>"


def _back(fraction):
    return f"<location><fractions>{fraction}</fractions></location>"


def _score(*bars, sig=("6", "2"), lens=None):
    """One staff; each bar is a list of voices, each voice its inner XML."""
    lens = lens or {}
    measures = []
    for no, voices in enumerate(bars, start=1):
        head = (f"<TimeSig><sigN>{sig[0]}</sigN><sigD>{sig[1]}</sigD></TimeSig>"
                if no == 1 else "")
        body = "".join(f"<voice>{head if i == 0 else ''}{v}</voice>" for i, v in enumerate(voices))
        attr = f' len="{lens[no]}"' if no in lens else ""
        measures.append(f"<Measure{attr}>{body}</Measure>")
    return etree.fromstring(
        ('<museScore><Score><Part><trackName>S1</trackName><Staff id="1"/></Part>'
         f'<Staff id="1">{"".join(measures)}</Staff></Score></museScore>').encode())


FULL = _chord("whole") * 3                       # 6/2 exactly
# The real scan's bar: it reaches exactly the barline, but its notes overlap.
OVERLAPPING = (_chord("half") + _chord("whole", 1) + _back("-1/2") + _chord("whole")
               + _back("-1/2") + _chord("whole"))


def test_a_voice_that_overlaps_itself_is_named_as_musescore_4_names_it():
    found = overlong_voices(_score([FULL, FULL], [FULL, OVERLAPPING]))
    assert found == [{
        "measure": 2, "staff": 1, "voice": 2,
        "message": "Voice too long: measure 2, staff 1, voice 2. Found: 4/1. Expected: 3/1.",
    }]


def test_a_voice_running_past_the_barline_is_named():
    assert [f["measure"] for f in overlong_voices(_score([FULL + _chord("half")]))] == [1]


def test_a_short_voice_passes_because_musescore_fills_it():
    assert overlong_voices(_score([_chord("whole")], [FULL, _chord("half")])) == []


def test_a_triplet_that_fills_the_bar_passes():
    triplet = ("<Tuplet><normalNotes>2</normalNotes><actualNotes>3</actualNotes></Tuplet>"
               + _chord("quarter") * 3 + "<endTuplet/>")
    assert overlong_voices(_score([_chord("half") * 1 + triplet], sig=("4", "4"))) == []


def test_a_tuplet_inside_a_tuplet_is_measured_by_both():
    def tup(a, n):
        return f"<Tuplet><normalNotes>{n}</normalNotes><actualNotes>{a}</actualNotes></Tuplet>"
    # 2/4: three quarters in the time of two, the middle one three eighths in the time of two.
    bar = (tup(3, 2) + _chord("quarter") + tup(3, 2) + _chord("eighth") * 3 + "<endTuplet/>"
           + _chord("quarter") + "<endTuplet/>")
    assert overlong_voices(_score([bar], sig=("2", "4"))) == []
    assert overlong_voices(_score([bar + _chord("eighth")], sig=("2", "4"))) != []


def test_a_breve_counts_its_length():
    assert [f["measure"] for f in overlong_voices(_score([_chord("breve") + _chord("quarter")],
                                                        sig=("4", "4")))] == [1]
    assert overlong_voices(_score([_chord("breve")], sig=("4", "2"))) == []


def test_a_bar_with_its_own_length_is_measured_by_it():
    long_bar = FULL + _chord("whole")
    assert overlong_voices(_score([long_bar], lens={1: "8/2"})) == []
    assert overlong_voices(_score([long_bar])) != []


def test_a_bar_rest_and_grace_notes_take_no_more_than_the_bar():
    grace = "<Chord><acciaccatura/><durationType>eighth</durationType><Note><pitch>62</pitch></Note></Chord>"
    rest = "<Rest><durationType>measure</durationType><duration>6/2</duration></Rest>"
    assert overlong_voices(_score([grace + FULL], [rest])) == []


def _musescore_4():
    cli = os.getenv("MUSESCORE_CLI_PATH", "")
    return cli if cli and os.path.exists(cli) and musescore_cli.major(cli) == 4 else None


needs_musescore_4 = pytest.mark.skipif(not _musescore_4(), reason="needs MuseScore 4 as MUSESCORE_CLI_PATH")


@needs_musescore_4
def test_musescore_4_refuses_the_fixture_without_force_and_the_rule_names_its_bar(tmp_path):
    cli = _musescore_4()
    plain = subprocess.run([cli, FIXTURE, "-o", str(tmp_path / "plain.mscx")],
                           capture_output=True, timeout=300)
    assert plain.returncode != 0 and not (tmp_path / "plain.mscx").exists()
    forced = str(tmp_path / "forced.mscx")
    subprocess.run([cli, "-f", FIXTURE, "-o", forced], capture_output=True, timeout=300)
    found = overlong_voices(etree.parse(forced).getroot())
    assert [(f["measure"], f["staff"], f["voice"]) for f in found] == [(2, 1, 2)]


@needs_musescore_4
def test_after_the_reset_musescore_4_opens_it_without_force(tmp_path):
    from src.song_app import pipeline

    cli = _musescore_4()
    forced = str(tmp_path / "forced.mscx")
    subprocess.run([cli, "-f", FIXTURE, "-o", forced], capture_output=True, timeout=300)
    found = pipeline.musescore_check(forced)
    assert found and found[0]["measure"] == 2
    pipeline.reset_rejected_bars(forced, found)
    assert pipeline.musescore_check(forced) == []
    again = str(tmp_path / "again.mscx")
    shutil.copyfile(forced, str(tmp_path / "in.mscx"))
    subprocess.run([cli, str(tmp_path / "in.mscx"), "-o", again], capture_output=True, timeout=300)
    assert os.path.exists(again)


def test_a_reused_conversion_still_names_the_bars(tmp_path):
    # The staff list converts first with no log; the clean that follows reuses the file.
    from src.song_app import pipeline

    source = tmp_path / "in.musicxml"
    source.write_text("<score-partwise/>")
    target = tmp_path / "in.mscx"
    target.write_bytes(etree.tostring(_score([FULL, FULL], [FULL, OVERLAPPING])))
    os.utime(source, (1, 1))
    said = []
    assert pipeline.convert_to_mscx(str(source), str(tmp_path), log=said.append) == str(target)
    assert any("measure 2, staff 1, voice 2" in line for line in said)
