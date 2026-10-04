"""A bar longer than its time signature is cut back to it and marked (#238).

Sangerhilsen bars 21 and 37: a triplet read as plain eighths, a bar of `len="9/8"`
under 4/4, and an extra eighth in every part of the practice track.
"""
from fractions import Fraction

from lxml import etree

from src.clean_score.utils.long_bars import trim_long_bars
from src.clean_score.utils.problem_marks import marks
from src.song_app.health import _voice_length

KINDS = {"1": "whole", "2": "half", "4": "quarter", "8": "eighth", "16": "16th"}


def _item(token):
    """`C4`/`C4.` a chord, `R8` a rest, `M` a measure rest, `T3`/`/T` a triplet, `G1/12` a gap."""
    if token == "M":
        return "<Rest><durationType>measure</durationType><duration>9/8</duration></Rest>"
    if token == "T3":
        return "<Tuplet><normalNotes>2</normalNotes><actualNotes>3</actualNotes></Tuplet>"
    if token == "/T":
        return "<endTuplet/>"
    if token.startswith("G"):
        return f"<location><fractions>{token[1:]}</fractions></location>"
    dots = token.count(".")
    dot = f"<dots>{dots}</dots>" if dots else ""
    kind = KINDS[token[1:].rstrip(".")]
    if token[0] == "R":
        return f"<Rest>{dot}<durationType>{kind}</durationType></Rest>"
    return f"<Chord>{dot}<durationType>{kind}</durationType><Note><pitch>{60 + len(token)}</pitch></Note></Chord>"


def _staff(sid, bar2, bar1="C1", bar3="C1", length="9/8", ts2=""):
    def measure(body, attr=""):
        return f"<Measure{attr}><voice>{''.join(_item(t) for t in body.split())}</voice></Measure>"
    ts = "<TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>"
    first = measure(bar1).replace("<voice>", f"<voice>{ts}", 1)
    second = measure(bar2, f' len="{length}"' if length else "").replace("<voice>", f"<voice>{ts2}", 1)
    return f'<Staff id="{sid}">{first}{second}{measure(bar3)}</Staff>'


def _score(*staves, **kw):
    parts = "".join(f'<Part><trackName>T{i}</trackName><Staff id="{i}"/></Part>'
                    for i in range(1, len(staves) + 1))
    body = "".join(_staff(i, s, **kw) for i, s in enumerate(staves, start=1))
    return etree.fromstring(f"<museScore><Score>{parts}{body}</Score></museScore>".encode())


def _bar(root, staff=1, bar=2):
    return root.findall(".//Score/Staff")[staff - 1].findall("Measure")[bar - 1]


def _length(root, staff=1, bar=2):
    return _voice_length(_bar(root, staff, bar).find("voice"), Fraction(1))[0]


def test_a_bar_an_eighth_too_long_is_cut_back_to_four_four():
    root = _score("C4. C8 C4 C8 C8 C8")
    [record] = trim_long_bars(root)
    assert record["measure"] == 2 and record["was"] == "9/8" and record["meter"] == "4/4"
    assert _length(root) == 1
    assert "len" not in _bar(root).attrib


def test_the_mark_names_the_notes_taken_out():
    root = _score("C4. C8 C4 C8 C8 C8")
    trim_long_bars(root)
    [mark] = marks(root)
    assert (mark["staff"], mark["measure"]) == (1, 2)
    assert mark["text"].startswith("was 9/8, cut to 4/4; removed ")
    assert len(mark["text"].split("removed ")[1].split(".")[0].split()) == 1


def test_a_note_across_the_barline_is_shortened():
    root = _score("C4. C8 C4 C4.")
    trim_long_bars(root)
    assert _length(root) == 1
    assert _bar(root).find("voice").findall("Chord")[-1].findtext("durationType") == "quarter"
    assert "shortened" in marks(root)[0]["text"]


def test_a_triplet_across_the_barline_goes_whole_and_rests_fill_the_bar():
    root = _score("C4. C8 C4 T3 C8 C8 C8 C8 /T")
    trim_long_bars(root)
    voice = _bar(root).find("voice")
    assert voice.find("Tuplet") is None and voice.find("endTuplet") is None
    assert _length(root) == 1


def test_only_rests_past_the_barline_cost_no_mark_but_the_bar_is_still_cut():
    root = _score("C4. C8 C4 C4 R8")
    trim_long_bars(root)
    assert _length(root) == 1
    # Nothing a singer sings changed, so nothing on this staff to look at -- but the
    # bar is still marked once, on the top staff, so the cut is not silent.
    assert [m["staff"] for m in marks(root)] == [1]
    assert "removed" not in marks(root)[0]["text"]


def test_a_resting_staff_gets_a_bar_long_rest():
    root = _score("C4. C8 C4 C8 C8 C8", "M")
    trim_long_bars(root)
    assert _bar(root, 2).find("voice/Rest/duration").text == "1/1"
    assert "len" not in _bar(root, 2).attrib
    assert [m["staff"] for m in marks(root)] == [1]


def test_a_voice_off_the_beat_grid_is_left_short_rather_than_padded_wrong():
    root = _score("G1/12 C4. C8 C4 C8 C8")
    trim_long_bars(root)
    assert _length(root) < 1
    assert [el.tag for el in _bar(root).find("voice")][-1] == "Chord"


def test_a_tie_into_the_part_cut_away_is_cut_too():
    tie = ('<Spanner type="Tie"><Tie/><next><location><fractions>1/4</fractions>'
           '</location></next></Spanner>')
    root = _score("C4. C8 C4 C4 C8")
    chord = _bar(root).find("voice").findall("Chord")[2]
    chord.find("Note").append(etree.fromstring(tie))
    trim_long_bars(root)
    # The tie pointed at beat 4, which survives; a tie to the cut-away eighth would go.
    assert chord.find("Note/Spanner") is not None
    root = _score("C4. C8 C4 C4 C8")
    last = _bar(root).find("voice").findall("Chord")[3]
    last.find("Note").append(etree.fromstring(tie))
    trim_long_bars(root)
    assert _bar(root).find("voice").findall("Chord")[-1].find("Note/Spanner") is None


def test_bars_that_are_not_too_long_are_left_alone():
    for root in (_score("C4. C8 C4", length="3/4"),          # short
                 _score("C4. C8 C4 C8 C8 C8", ts2="<TimeSig><sigN>9</sigN><sigD>8</sigD></TimeSig>"),
                 _score("C1", length="")):
        before = etree.tostring(root)
        assert trim_long_bars(root) == []
        assert etree.tostring(root) == before


def test_the_neighbouring_bars_are_untouched_and_a_second_run_finds_nothing():
    root = _score("C4. C8 C4 C8 C8 C8")
    first, third = (etree.tostring(_bar(root, 1, b)) for b in (1, 3))
    trim_long_bars(root)
    assert etree.tostring(_bar(root, 1, 1)) == first and etree.tostring(_bar(root, 1, 3)) == third
    after = etree.tostring(root)
    assert trim_long_bars(root) == []
    assert etree.tostring(root) == after
