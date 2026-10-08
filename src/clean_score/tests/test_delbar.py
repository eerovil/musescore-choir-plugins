"""The `delbar` fix (#346): take out a bar the scan invented, on every staff.

Kun poijat ne raitilla's scan put an empty bar between the "1." ending (bar 9) and
the "2." ending (bar 11), so the "1." bracket ran over two bars and the practice
track played a bar of silence the page does not print. The score below is that
shape in five bars: a "1." bracket over bars 3-4 with the end repeat on bar 3, the
invented empty bar 4, and the "2." bracket on bar 5. What these pin: the bar goes on
every staff, the brackets end up over bars 3 and 4, a spanner across the bar keeps
both its notes, fixes count bars in file order, and the fix refuses a bar that is
part of the music.
"""
import json

import pytest
from lxml import etree

from src.clean_score.utils.score_fixes import (FixError, _measure, after_moves, apply_fixes,
                                               bar_moves, bar_tokens, before_moves,
                                               volta_spans)


def _chord(dur, pitch, inside=""):
    return f"<Chord><durationType>{dur}</durationType><Note>{inside}<pitch>{pitch}</pitch>" \
           f"<tpc>14</tpc></Note></Chord>"


EMPTY = ("<Rest><visible>0</visible><durationType>measure</durationType>"
         "<duration>4/4</duration></Rest>")
VOLTA1 = ('<Spanner type="Volta"><Volta><endHookType>1</endHookType><beginText>1</beginText>'
          '<endings>1</endings></Volta><next><location><measures>2</measures></location>'
          '</next></Spanner>')
VOLTA1_END = ('<Spanner type="Volta"><prev><location><measures>-2</measures></location>'
              '</prev></Spanner>')
VOLTA2 = ('<Spanner type="Volta"><Volta><beginText>2</beginText><endings>2</endings></Volta>'
          '<next><location></location></next></Spanner>'
          '<Spanner type="Volta"><prev><location></location></prev></Spanner>')
SLUR_OUT = ('<Spanner type="Slur"><Slur></Slur><next><location><measures>2</measures>'
            '</location></next></Spanner>')
SLUR_IN = ('<Spanner type="Slur"><prev><location><measures>-2</measures></location>'
           '</prev></Spanner>')


def _bars(volta: bool):
    v1, v1_end, v2 = (VOLTA1, VOLTA1_END, VOLTA2) if volta else ("", "", "")
    return [_chord("whole", 60), _chord("whole", 62),
            v1 + _chord("half", 64) + _chord("half", 65),
            EMPTY,
            v1_end + v2 + _chord("whole", 67)]


def _score(bars=None, meta=""):
    bars = bars or {1: _bars(True), 2: _bars(False)}
    parts = "".join(f'<Part><trackName>{n}</trackName><Staff id="{i}"/></Part>'
                    for i, n in ((1, "T1"), (2, "T2")))
    staves = ""
    for sid, content in bars.items():
        measures = "".join(
            f"<Measure>{'<endRepeat>2</endRepeat>' if i == 2 else ''}<voice>{bar}</voice>"
            f"</Measure>" for i, bar in enumerate(content))
        staves += f'<Staff id="{sid}">{measures}</Staff>'
    return etree.fromstring(
        f"<museScore version='3.02'><Score>{meta}{parts}{staves}</Score></museScore>")


@pytest.fixture
def root():
    return _score()


DELBAR = {"kind": "delbar", "measure": 4, "from": ["measure:R"],
          "why": "the page prints no bar between the 1. and 2. endings"}


def _staff(root, sid):
    return next(s for s in root.findall(".//Score/Staff") if s.get("id") == str(sid))


def test_the_bar_goes_on_every_staff_and_the_brackets_close_up(root):
    done = apply_fixes(root, [dict(DELBAR)])
    assert "all 2 staves" in done[0]
    for sid in (1, 2):
        assert len(_staff(root, sid).findall("Measure")) == 4
        assert bar_tokens(root, sid, 4) == ["whole:67"]
    # "1." over bar 3 only, "2." over the bar right after it, the repeat still on 3.
    assert volta_spans(_staff(root, 1)) == [(3, 3), (4, 4)]
    assert _measure(root, 1, 3).find("endRepeat") is not None
    end = _measure(root, 1, 4).find("voice/Spanner[@type='Volta']")
    assert end.findtext("prev/location/measures") == "-1"


def test_a_slur_across_the_bar_keeps_both_its_notes():
    bars = _bars(False)
    bars[2] = _chord("half", 64) + _chord("half", 65, inside=SLUR_OUT)
    bars[4] = _chord("whole", 67, inside=SLUR_IN)
    root = _score({1: bars, 2: _bars(False)})
    apply_fixes(root, [dict(DELBAR)])
    start = _measure(root, 1, 3).find(".//Spanner[@type='Slur']")
    end = _measure(root, 1, 4).find(".//Spanner[@type='Slur']")
    assert start.findtext("next/location/measures") == "1"
    assert end.findtext("prev/location/measures") == "-1"


def test_fixes_count_bars_in_file_order(root):
    # Recorded before the invented bar was noticed: bar 5 in the old numbering.
    before = {"kind": "undot", "staff": 2, "measure": 5, "index": 0, "why": "x"}
    # Recorded after, in the app, against the score without it: bar 4 is that bar now.
    after = {"kind": "slur", "staff": 2, "measure": 3, "index": 0, "span": 1, "why": "x"}
    root = _score({1: _bars(True), 2: _bars(False)[:4] + [_chord("whole", 67).replace(
        "<durationType>", "<dots>1</dots><durationType>")]})
    done = apply_fixes(root, [before, dict(DELBAR), after])
    assert len(done) == 3
    assert bar_tokens(root, 2, 4) == ["whole:67"]


def test_a_later_fix_in_the_old_numbering_after_it_fails_loudly(root):
    stale = {"kind": "append", "staff": 1, "measure": 5, "from": ["whole:67"], "add": [],
             "why": "x"}
    with pytest.raises(FixError, match="staff 1 m5"):
        apply_fixes(root, [dict(DELBAR), stale])


@pytest.mark.parametrize("expect, says", [
    (None, "'from'"),
    (["whole:62"], "reads \\['measure:R'\\] now"),
    ([["measure:R"]], "names 1 staves"),
])
def test_it_is_strict_about_from(root, expect, says):
    fix = dict(DELBAR, **{"from": expect})
    with pytest.raises(FixError, match="m4 .delbar.: .*" + says):
        apply_fixes(root, [fix])
    assert len(_staff(root, 1).findall("Measure")) == 5


def test_from_can_name_each_staff(root):
    apply_fixes(root, [dict(DELBAR, **{"from": [["measure:R"], ["measure:R"]]})])
    assert len(_staff(root, 2).findall("Measure")) == 4


def test_a_bar_with_music_in_it_refuses(root):
    # Bar 3 carries the end repeat and the start of the "1." bracket.
    fix = dict(DELBAR, measure=3, **{"from": ["half:64", "half:65"]})
    with pytest.raises(FixError, match="a repeat ends in this bar"):
        apply_fixes(root, [fix])


def test_a_spanner_ending_in_the_bar_refuses():
    bars = _bars(False)
    bars[2] = _chord("half", 64) + _chord("half", 65, inside=SLUR_OUT.replace("2<", "1<"))
    bars[3] = _chord("whole", 66, inside=SLUR_IN.replace("-2", "-1"))
    root = _score({1: bars, 2: _bars(False)})
    fix = dict(DELBAR, **{"from": [["whole:66"], ["measure:R"]]})
    with pytest.raises(FixError, match="Slur from bar 3 to bar 4"):
        apply_fixes(root, [fix])


def test_the_per_system_lyric_map_loses_the_bar():
    tag = json.dumps([{"start": 1, "end": 2, "map": {"1": [1]}},
                      {"start": 3, "end": 5, "map": {"1": [1, 2]}}])
    root = _score(meta=f'<metaTag name="lyricsSystemMap">{tag}</metaTag>')
    apply_fixes(root, [dict(DELBAR)])
    shifted = json.loads(root.find(".//metaTag[@name='lyricsSystemMap']").text)
    assert [(e["start"], e["end"]) for e in shifted] == [(1, 2), (3, 4)]


def test_it_replays_on_a_rebuild(root, tmp_path):
    from src.song_app import pipeline  # noqa: PLC0415 - only this test needs the app

    (tmp_path / "fixes.json").write_text(json.dumps([DELBAR]))
    score = tmp_path / "score_cleaned.mscx"
    for _ in range(2):
        etree.ElementTree(_score()).write(str(score))
        pipeline.apply_recorded_fixes(str(score), str(tmp_path))
        again = etree.parse(str(score)).getroot()
        assert volta_spans(_staff(again, 1)) == [(3, 3), (4, 4)]


def test_a_pick_recorded_after_it_survives_the_next_clean(tmp_path):
    # A Fix-panel pick is checked against the cleaned score before any fix runs, so
    # one recorded after the deletion has to be looked up in the old numbering.
    from src.song_app import pipeline  # noqa: PLC0415 - only this test needs the app
    from src.song_app.bar_readings import SOURCE  # noqa: PLC0415

    pick = {"kind": "bar", "source": SOURCE, "staff": 2, "part": "T2", "measure": 4,
            "from": ["whole:67"], "to": [{"value": "note_1", "pitches": [69],
                                         "tpcs": [17]}], "why": "picked b"}
    (tmp_path / "fixes.json").write_text(json.dumps([DELBAR, pick]))
    score = tmp_path / "score_cleaned.mscx"
    for _ in range(2):
        etree.ElementTree(_score()).write(str(score))
        pipeline.apply_recorded_fixes(str(score), str(tmp_path))
        again = etree.parse(str(score)).getroot()
        assert bar_tokens(again, 2, 4) == ["whole:69"]
    assert json.loads((tmp_path / "fixes.json").read_text()) == [DELBAR, pick]


def test_a_bracket_ending_before_the_bar_keeps_its_length():
    # The Kun poijat shape once the "1." bracket is read the right length: over bar 3
    # only, its end marker in the invented bar 4.
    bars = _bars(True)
    bars[2] = bars[2].replace("<measures>2</measures>", "<measures>1</measures>")
    bars[3] = VOLTA1_END.replace("-2", "-1") + EMPTY
    bars[4] = bars[4].replace(VOLTA1_END, "")
    root = _score({1: bars, 2: _bars(False)})
    assert volta_spans(_staff(root, 1)) == [(3, 3), (5, 5)]
    apply_fixes(root, [dict(DELBAR)])
    assert volta_spans(_staff(root, 1)) == [(3, 3), (4, 4)]
    first = _measure(root, 1, 4).find("voice")[0]
    assert first.get("type") == "Volta" and first.findtext("prev/location/measures") == "-1"


def test_a_bracket_starting_in_the_bar_refuses():
    bars = _bars(False)
    bars[3] = VOLTA2 + EMPTY
    root = _score({1: bars, 2: _bars(False)})
    with pytest.raises(FixError, match="a volta bracket starts in this bar on staff 1"):
        apply_fixes(root, [dict(DELBAR)])
    assert volta_spans(_staff(root, 1)) == [(4, 4)]
    assert len(_staff(root, 2).findall("Measure")) == 5


def test_the_removed_slurs_lose_the_bar():
    # Where cleaning took out a slur between singers, which the Fix panel asks about.
    def slur(measure, end):
        return {"measure": measure, "staff": 1, "part": "T1", "note": 0, "pos": "0",
                "end_measure": end, "end_staff": 2, "end_part": "T2", "end_note": 0,
                "end_pos": "0"}
    tag = json.dumps([slur(2, 3), slur(3, 5), slur(5, 5), slur(4, 5), slur(5, None)])
    root = _score(meta=f'<metaTag name="removedSlurs">{tag}</metaTag>')
    apply_fixes(root, [dict(DELBAR)])
    shifted = json.loads(root.find(".//metaTag[@name='removedSlurs']").text)
    assert [(r["measure"], r["end_measure"]) for r in shifted] == [
        (2, 3), (3, 4), (4, 4), (4, None)]


# --- insbar ---------------------------------------------------------------------
# Kristallen den fina: the scan lost a barline early on (two printed bars squeezed
# into one) and invented an empty bar after the "1." ending. Fixing it is "insert a
# bar after bar 2" and "delete bar 9", with the brackets and the repeat staying put.

INSBAR = {"kind": "insbar", "measure": 1, "from": [["whole:60"], ["whole:60"]],
          "why": "the scan lost the barline between printed bars 1 and 2"}


def test_an_inserted_bar_is_empty_on_every_staff_and_the_brackets_follow(root):
    done = apply_fixes(root, [dict(INSBAR)])
    assert "after bar 1 on all 2 staves" in done[0]
    for sid in (1, 2):
        assert len(_staff(root, sid).findall("Measure")) == 6
        assert bar_tokens(root, sid, 2) == ["measure:R"]
        assert bar_tokens(root, sid, 3) == ["whole:62"]
    assert _measure(root, 1, 2).findtext(".//Rest/duration") == "4/4"
    assert volta_spans(_staff(root, 1)) == [(4, 5), (6, 6)]
    assert _measure(root, 1, 4).find("endRepeat") is not None


def test_kristallens_two_fixes_in_file_order_then_the_new_bar_is_written(root):
    fill = {"kind": "bar", "staff": 1, "measure": 2, "from": ["measure:R"],
            "to": [{"value": "note_2", "pitches": [61], "tpcs": [21]},
                   {"value": "note_2", "pitches": [62], "tpcs": [16]}], "why": "page"}
    # The delbar counts the inserted bar: the invented bar 4 is bar 5 by then.
    apply_fixes(root, [dict(INSBAR), dict(DELBAR, measure=5), fill])
    assert volta_spans(_staff(root, 1)) == [(4, 4), (5, 5)]
    assert _measure(root, 1, 4).find("endRepeat") is not None
    assert bar_tokens(root, 1, 2) == ["half:61", "half:62"]
    assert [bar_tokens(root, 2, m) for m in range(1, 6)] == [
        ["whole:60"], ["measure:R"], ["whole:62"], ["half:64", "half:65"], ["whole:67"]]


def test_a_bracket_ending_on_the_barline_keeps_its_length(root):
    # "1." over bars 3-4, its end marker in bar 5: a bar put in after bar 4.
    apply_fixes(root, [dict(INSBAR, measure=4, **{"from": ["measure:R"]})])
    assert volta_spans(_staff(root, 1)) == [(3, 4), (6, 6)]
    first = _measure(root, 1, 5).find("voice")[0]
    assert first.get("type") == "Volta" and first.findtext("prev/location/measures") == "-2"


def test_a_slur_across_the_barline_is_lengthened():
    bars = _bars(False)
    bars[2] = _chord("half", 64) + _chord("half", 65, inside=SLUR_OUT)
    bars[4] = _chord("whole", 67, inside=SLUR_IN)
    root = _score({1: bars, 2: _bars(False)})
    apply_fixes(root, [dict(INSBAR, measure=3, **{"from": [["half:64", "half:65"]] * 2})])
    assert _measure(root, 1, 3).findtext(".//Spanner[@type='Slur']/next/location/measures") \
        == "3"
    assert _measure(root, 1, 6).findtext(".//Spanner[@type='Slur']/prev/location/measures") \
        == "-3"


def test_a_tie_across_the_barline_refuses():
    tie_out = ('<Spanner type="Tie"><Tie></Tie><next><location><measures>1</measures>'
               '</location></next></Spanner>')
    tie_in = ('<Spanner type="Tie"><prev><location><measures>-1</measures></location>'
              '</prev></Spanner>')
    bars = _bars(False)
    bars[0] = _chord("whole", 60, inside=tie_out)
    bars[1] = _chord("whole", 60, inside=tie_in)
    root = _score({1: bars, 2: _bars(False)})
    with pytest.raises(FixError, match="m1 .insbar.: a tie from bar 1 to bar 2"):
        apply_fixes(root, [dict(INSBAR)])
    assert len(_staff(root, 2).findall("Measure")) == 5


def test_insbar_is_strict_about_from(root):
    with pytest.raises(FixError, match="m1 .insbar.: staff 2 bar 1 reads"):
        apply_fixes(root, [dict(INSBAR, **{"from": [["whole:60"], ["whole:61"]]})])
    with pytest.raises(FixError, match="'from'"):
        apply_fixes(root, [dict(INSBAR, **{"from": None})])
    assert len(_staff(root, 1).findall("Measure")) == 5


def test_an_inserted_bar_joins_the_system_before_it_in_the_metatags():
    tag = json.dumps([{"start": 1, "end": 1, "map": {"1": [1]}},
                      {"start": 2, "end": 5, "map": {"1": [1, 2]}}])
    slurs = json.dumps([{"measure": 1, "end_measure": 2}, {"measure": 3, "end_measure": 3}])
    root = _score(meta=f'<metaTag name="lyricsSystemMap">{tag}</metaTag>'
                       f'<metaTag name="removedSlurs">{slurs}</metaTag>')
    apply_fixes(root, [dict(INSBAR)])
    systems = json.loads(root.find(".//metaTag[@name='lyricsSystemMap']").text)
    assert [(e["start"], e["end"]) for e in systems] == [(1, 2), (3, 6)]
    removed = json.loads(root.find(".//metaTag[@name='removedSlurs']").text)
    assert [(r["measure"], r["end_measure"]) for r in removed] == [(1, 3), (4, 4)]


def test_bar_numbers_map_through_the_moves_both_ways():
    moves = bar_moves([dict(INSBAR, measure=2), {"kind": "text", "what": "x"},
                       dict(DELBAR, measure=10)])
    assert moves == [("ins", 2), ("del", 10)]
    # Before both -> after both: bar 9 (the invented one, bar 10 after the insert) goes.
    assert [after_moves(b, moves) for b in (1, 2, 3, 8, 9, 10)] == [1, 2, 4, 9, None, 10]
    assert [before_moves(b, moves) for b in (1, 2, 3, 4, 9, 10)] == [1, 2, None, 3, 8, 10]


def test_a_pick_recorded_after_an_insert_survives_the_next_clean(tmp_path):
    from src.song_app import pipeline  # noqa: PLC0415 - only this test needs the app
    from src.song_app.bar_readings import SOURCE  # noqa: PLC0415

    pick = {"kind": "bar", "source": SOURCE, "staff": 2, "part": "T2", "measure": 6,
            "from": ["whole:67"], "to": [{"value": "note_1", "pitches": [69],
                                         "tpcs": [17]}], "why": "picked b"}
    fill = {"kind": "bar", "source": SOURCE, "staff": 2, "part": "T2", "measure": 2,
            "from": ["measure:R"], "to": [{"value": "note_1", "pitches": [61],
                                          "tpcs": [21]}], "why": "picked a"}
    (tmp_path / "fixes.json").write_text(json.dumps([INSBAR, pick, fill]))
    score = tmp_path / "score_cleaned.mscx"
    etree.ElementTree(_score()).write(str(score))
    pipeline.apply_recorded_fixes(str(score), str(tmp_path))
    again = etree.parse(str(score)).getroot()
    assert bar_tokens(again, 2, 6) == ["whole:69"]
    assert bar_tokens(again, 2, 2) == ["whole:61"]
    assert json.loads((tmp_path / "fixes.json").read_text()) == [INSBAR, pick, fill]
