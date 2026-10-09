"""`unvolta`, `unrepeat`, `barlen` and a longer "2." ending (#378).

Suomalainen rukous came back from the scan with a second "1." bracket and end repeat
on a bar the page does not print -- the organ's 1st ending, copied onto the choir --
and the scroll render refused it: MuseScore played that bar and the engraving never
timed it. The page prints one "1." ending (bar 18, end repeat) and a two-bar "2."
ending, 6/4 then 4/4, which the scan read as 3/2 and cleaning cut to 4/4. The score
below is that shape in five bars on two staves:

    bar 1   music
    bar 2   music, end repeat, a "1." bracket starting and ending inside the bar
    bar 3   whole rest, end repeat, a second "1." bracket (the invented bar)
    bar 4   whole rest, "2." bracket to the end of the score
    bar 5   whole rest
"""
import json
import os
import subprocess

import pytest
from lxml import etree

from src.clean_score.utils.score_fixes import FixError, apply_fixes, bar_tokens, volta_spans

TEST_FILES = os.path.join(os.path.dirname(__file__), "test_files")

ONE_IN_BAR = ('<Spanner type="Volta"><Volta><beginText>1</beginText><endings>1</endings>'
              '</Volta><next><location></location></next></Spanner>'
              '<Spanner type="Volta"><prev><location></location></prev></Spanner>')
ONE = ('<Spanner type="Volta"><Volta><endHookType>1</endHookType><beginText>1</beginText>'
       '<endings>1</endings></Volta><next><location><measures>1</measures></location>'
       '</next></Spanner>')
ONE_END = '<Spanner type="Volta"><prev><location><measures>-1</measures></location></prev></Spanner>'
TWO = ('<Spanner type="Volta"><Volta><endHookType>1</endHookType><beginText>2</beginText>'
       '<endings>2</endings></Volta><next><location><measures>1</measures>'
       '<fractions>1/1</fractions></location></next></Spanner>')
TWO_END = ('<Spanner type="Volta"><prev><location><measures>-1</measures>'
           '<fractions>-1/1</fractions></location></prev></Spanner>')
WHOLE = "<Rest><durationType>whole</durationType></Rest>"
MUSIC = ("<Chord><durationType>half</durationType><Note><pitch>58</pitch><tpc>12</tpc></Note>"
         "</Chord><Rest><durationType>half</durationType></Rest>")
END = "<endRepeat>2</endRepeat>"


def _score():
    parts = "".join(f'<Part><trackName>{n}</trackName><Staff id="{i}"/></Part>'
                    for i, n in ((1, "T1"), (2, "B1")))
    staves = ""
    for sid in (1, 2):
        top = sid == 1
        bars = [
            f"<Measure><voice><TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>{MUSIC}</voice>"
            "</Measure>",
            f"<Measure>{END}<voice>{ONE_IN_BAR if top else ''}{MUSIC}</voice></Measure>",
            # The scan wrote the invented bracket on every staff, with no end on the others.
            f"<Measure>{END}<voice>{ONE}{WHOLE}</voice></Measure>",
            f"<Measure><voice>{(ONE_END + TWO) if top else ''}{WHOLE}</voice></Measure>",
            f"<Measure><voice>{WHOLE}{TWO_END if top else ''}</voice></Measure>",
        ]
        staves += f'<Staff id="{sid}">{"".join(bars)}</Staff>'
    return etree.fromstring(
        f"<museScore version='3.02'><Score>{parts}{staves}</Score></museScore>")


W = ["whole:R"]
SUOMALAINEN = [
    {"kind": "unvolta", "measure": 2, "why": "written again below over the whole bar"},
    {"kind": "unvolta", "measure": 3, "text": "1.", "why": "the organ's 1st ending"},
    {"kind": "unrepeat", "measure": 3, "which": "end", "why": "the organ's 1st ending"},
    {"kind": "unvolta", "measure": 4, "why": "written again below over both bars"},
    {"kind": "delbar", "measure": 3, "from": W, "why": "the organ's 1st-ending bar"},
    {"kind": "volta", "measure": 2, "bars": 1, "second": 2, "why": "the page"},
    {"kind": "barlen", "measure": 3, "from": W, "to": "6/4", "why": "printed 6/4"},
]


def _staves(root):
    return root.findall(".//Score/Staff")


def _spanners(bar):
    return [etree.tostring(s).decode() for s in bar.iter("Spanner")]


def test_suomalainen_rukous_comes_back_as_the_page_prints_it():
    root = _score()
    done = apply_fixes(root, SUOMALAINEN)
    assert len(done) == len(SUOMALAINEN)
    top, other = _staves(root)
    assert [len(s.findall("Measure")) for s in (top, other)] == [4, 4]
    # One end repeat, one "1." over bar 2, one "2." over bars 3-4, on the top staff only.
    assert [[m.find("endRepeat") is not None for m in s.findall("Measure")]
            for s in (top, other)] == [[False, True, False, False]] * 2
    assert volta_spans(top) == [(2, 2), (3, 4)]
    assert volta_spans(other) == [] and not other.findall(".//Spanner")
    # The bracket closing the score ends a bar's length into the last bar.
    bars = top.findall("Measure")
    assert _spanners(bars[2]) == [
        '<Spanner type="Volta"><prev><location><measures>-1</measures></location></prev>'
        '</Spanner>',
        '<Spanner type="Volta"><Volta><beginText>2.</beginText><endings>2</endings></Volta>'
        '<next><location><measures>1</measures><fractions>1/1</fractions></location></next>'
        '</Spanner>']
    assert _spanners(bars[3]) == [
        '<Spanner type="Volta"><prev><location><measures>-1</measures>'
        '<fractions>-1/1</fractions></location></prev></Spanner>']
    # The 2nd ending's first bar is 6/4 of rest on every staff; its second stays 4/4.
    for staff in (top, other):
        third = staff.findall("Measure")[2]
        assert third.get("len") == "6/4"
        assert [r.findtext("duration") for r in third.iter("Rest")] == ["6/4"]
        assert staff.findall("Measure")[3].get("len") is None


def test_unvolta_takes_both_halves_and_only_the_bracket_named():
    root = _score()
    apply_fixes(root, [{"kind": "unvolta", "measure": 4, "text": "2", "why": "x"}])
    top = _staves(root)[0]
    assert volta_spans(top) == [(2, 2), (3, 3)]
    assert not _spanners(top.findall("Measure")[4])
    assert _spanners(top.findall("Measure")[3]) == [ONE_END]  # the "1." end stays
    with pytest.raises(FixError, match=r"m4 \(unvolta\): no volta bracket reading 1\."):
        apply_fixes(_score(), [{"kind": "unvolta", "measure": 4, "text": "1.", "why": "x"}])
    with pytest.raises(FixError, match="no volta bracket starts"):
        apply_fixes(_score(), [{"kind": "unvolta", "measure": 1, "why": "x"}])


def test_unrepeat_refuses_a_bar_with_no_sign_and_a_wrong_which():
    with pytest.raises(FixError, match=r"m4 \(unrepeat\): no repeat ends"):
        apply_fixes(_score(), [{"kind": "unrepeat", "measure": 4, "which": "end", "why": "x"}])
    with pytest.raises(FixError, match="no repeat starts"):
        apply_fixes(_score(), [{"kind": "unrepeat", "measure": 3, "which": "start", "why": "x"}])
    with pytest.raises(FixError, match="'which'"):
        apply_fixes(_score(), [{"kind": "unrepeat", "measure": 3, "why": "x"}])


def test_barlen_only_changes_a_bar_of_silence_and_is_strict_about_from():
    with pytest.raises(FixError, match="has music in this bar"):
        apply_fixes(_score(), [{"kind": "barlen", "measure": 1,
                                "from": ["half:58", "half:R"], "to": "6/4", "why": "x"}])
    with pytest.raises(FixError, match="recorded against"):
        apply_fixes(_score(), [{"kind": "barlen", "measure": 4, "from": ["measure:R"],
                                "to": "6/4", "why": "x"}])
    with pytest.raises(FixError, match="from"):
        apply_fixes(_score(), [{"kind": "barlen", "measure": 4, "to": "6/4", "why": "x"}])
    # Back to the meter in force takes the override off rather than restating it.
    root = _score()
    apply_fixes(root, [{"kind": "barlen", "measure": 4, "from": W, "to": "6/4", "why": "x"},
                       {"kind": "barlen", "measure": 4, "from": ["measure:R"], "to": "4/4",
                        "why": "x"}])
    bar = _staves(root)[1].findall("Measure")[3]
    assert bar.get("len") is None and bar_tokens(root, 2, 4) == ["measure:R"]


def test_a_longer_second_ending_refuses_past_the_last_bar():
    root = _score()
    apply_fixes(root, SUOMALAINEN[:5])
    with pytest.raises(FixError, match="no bar 5 for a 2. bracket of 3 bars"):
        apply_fixes(root, [{"kind": "volta", "measure": 2, "bars": 1, "second": 3, "why": "x"}])


def test_it_replays_on_a_rebuild(tmp_path):
    from src.song_app import pipeline  # noqa: PLC0415 - only this test needs the app

    (tmp_path / "fixes.json").write_text(json.dumps(SUOMALAINEN))
    score = tmp_path / "score_cleaned.mscx"
    for _ in range(2):
        etree.ElementTree(_score()).write(str(score))
        assert pipeline.apply_recorded_fixes(str(score), str(tmp_path)) == len(SUOMALAINEN)
        again = etree.parse(str(score)).getroot()
        assert volta_spans(_staves(again)[0]) == [(2, 2), (3, 4)]


def _musescore() -> bool:
    cli = os.getenv("MUSESCORE_CLI_PATH", "")
    return bool(cli) and os.path.exists(cli)


@pytest.mark.skipif(not _musescore(), reason="MUSESCORE_CLI_PATH is not set to a real binary")
def test_musescore_plays_the_endings_the_page_prints(tmp_path):
    """MuseScore exports one repeat, a "1." over bar 2 and a 6/4 + 2/4 "2." ending."""
    root = etree.parse(os.path.join(TEST_FILES, "medium_1_output.mscx")).getroot()
    staves = [s for s in root.findall(".//Score/Staff") if s.find("Measure") is not None]
    for staff in staves:
        bars = staff.findall("Measure")
        for bar in bars[2:]:  # the 2nd ending (2/4 here): silence, as on Suomalainen rukous
            voices = bar.findall("voice")
            for voice in voices[1:]:
                bar.remove(voice)
            for el in voices[0]:
                if el.tag in ("Chord", "Rest", "Tuplet", "endTuplet", "Beam", "Spanner"):
                    voices[0].remove(el)
            voices[0].append(etree.fromstring(
                "<Rest><durationType>measure</durationType><duration>2/4</duration></Rest>"))
        for bar in bars[1:3]:
            bar.insert(0, etree.fromstring(END))
        bars[2].find("voice").insert(0, etree.fromstring(ONE))
    staves[0].findall("Measure")[3].find("voice").insert(0, etree.fromstring(ONE_END))
    apply_fixes(root, [
        {"kind": "unvolta", "measure": 3, "why": "x"},
        {"kind": "unrepeat", "measure": 3, "which": "end", "why": "x"},
        {"kind": "volta", "measure": 2, "bars": 1, "second": 2, "why": "x"},
        {"kind": "barlen", "measure": 3, "from": ["measure:R"], "to": "6/4", "why": "x"},
    ])
    src = tmp_path / "endings.mscx"
    etree.ElementTree(root).write(str(src), encoding="UTF-8", xml_declaration=True)
    out = tmp_path / "endings.musicxml"
    subprocess.run([os.environ["MUSESCORE_CLI_PATH"], "-o", str(out), str(src)],
                   check=True, capture_output=True, timeout=120)
    part = etree.parse(str(out)).getroot().find("part")
    measures = part.findall("measure")
    endings = [(m.get("number"), e.get("number"), e.get("type"))
               for m in measures for e in m.iter("ending")]
    assert endings == [("2", "1", "start"), ("2", "1", "stop"),
                       ("3", "2", "start"), ("4", "2", "discontinue")]
    backward = [m.get("number") for m in measures
                for r in m.iter("repeat") if r.get("direction") == "backward"]
    assert backward == ["2"]
    divisions = int(part.findtext(".//divisions"))
    lengths = []
    for m in measures[2:]:
        lengths.append(sum(int(n.findtext("duration")) for n in m.findall("note")
                           if n.find("chord") is None and n.findtext("voice") == "1"))
    assert lengths == [6 * divisions, 2 * divisions]
