"""A recorded `bar` fix answers cleaning's "cut to" mark on every clean (#482).

Cleaning rebuilds the score and `long_bars` writes the mark again each time; the
recorded fixes then replay (`pipeline.apply_recorded_fixes`). The mark has to come off
again on the rebuild when the fix answers it, and stay when the cut was in a voice the
fix does not write.
"""
import json

from lxml import etree

from src.clean_score.utils.long_bars import trim_long_bars
from src.clean_score.utils.problem_marks import marks
from src.clean_score.utils.score_fixes import _bar_tokens
from src.song_app.pipeline import apply_recorded_fixes

TS = "<TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>"


def _chord(kind, pitch, dots=0):
    dot = f"<dots>{dots}</dots>" if dots else ""
    return f"<Chord>{dot}<durationType>{kind}</durationType><Note><pitch>{pitch}</pitch><tpc>14</tpc></Note></Chord>"


def _source(voice2=""):
    """Bar 2 is 9/8 under 4/4: voice 1 an eighth too long, or voice 2 if given."""
    if voice2:
        one = _chord("half", 60) + _chord("half", 62)
        two = f"<voice>{voice2}</voice>"
    else:
        one = (_chord("quarter", 60, 1) + _chord("eighth", 62) + _chord("quarter", 64)
               + _chord("eighth", 65) + _chord("eighth", 67) + _chord("eighth", 69))
        two = ""
    whole = _chord("whole", 60)
    return (f'<museScore version="3.02"><Score>'
            f'<Part><trackName>A1</trackName><Staff id="1"/></Part><Staff id="1">'
            f"<Measure><voice>{TS}{whole}</voice></Measure>"
            f'<Measure len="9/8"><voice>{one}</voice>{two}</Measure>'
            f"<Measure><voice>{whole}</voice></Measure>"
            f"</Staff></Score></museScore>")


def _clean(source, path):
    """What cleaning does to bar 2: cut it and mark it, written out."""
    root = etree.fromstring(source)
    trim_long_bars(root)
    etree.ElementTree(root).write(str(path), encoding="UTF-8", xml_declaration=True)
    return root


FIX_TO = [{"value": "note_2", "pitches": [60], "tpcs": [14]},
          {"value": "note_2", "pitches": [64], "tpcs": [18]}]


def _record_fix(song, root):
    bar = root.findall(".//Score/Staff")[0].findall("Measure")[1]
    entry = {"kind": "bar", "staff": 1, "measure": 2, "from": _bar_tokens(bar),
             "to": FIX_TO, "why": "read off the page"}
    (song / "fixes.json").write_text(json.dumps([entry]))


def _marks(path):
    return [m["text"] for m in marks(etree.parse(str(path)).getroot())]


def test_a_recorded_bar_fix_takes_the_cut_mark_off_again_on_every_clean(tmp_path):
    cleaned = tmp_path / "song_cleaned.mscx"
    root = _clean(_source(), cleaned)
    assert _marks(cleaned)[0].startswith("was 9/8, cut to 4/4 in voice 1")
    _record_fix(tmp_path, root)
    for _ in range(2):  # the first clean after recording, then a rebuild
        _clean(_source(), cleaned)
        assert apply_recorded_fixes(str(cleaned), str(tmp_path)) == 1
        assert _marks(cleaned) == []


def test_a_cut_in_voice_two_keeps_its_mark_after_the_replay(tmp_path):
    cleaned = tmp_path / "song_cleaned.mscx"
    voice2 = "<Rest><durationType>whole</durationType></Rest>" + _chord("eighth", 52)
    root = _clean(_source(voice2), cleaned)
    [mark] = _marks(cleaned)
    assert mark.startswith("was 9/8, cut to 4/4 in voice 2")
    _record_fix(tmp_path, root)
    for _ in range(2):
        _clean(_source(voice2), cleaned)
        assert apply_recorded_fixes(str(cleaned), str(tmp_path)) == 1
        assert _marks(cleaned) == [mark]
