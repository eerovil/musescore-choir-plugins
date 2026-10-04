"""A rest the page prints once for two voices goes to both before the split (#235).

The scanner writes the rest into one voice; the split then leaves the other voice's
bar ending early on its own staff. `test_simple1_split.py` carries the same rule end to
end: both goldens had exactly such a gap, now a rest.
"""
from lxml import etree

from src.clean_score.utils.shared_rests import share_rests


def _item(token):
    """`C4`/`C8.` a chord, `R4` a rest, `G2` a two-quarter gap, `T3:` a triplet open."""
    kinds = {"1": "whole", "2": "half", "4": "quarter", "8": "eighth", "16": "16th"}
    if token.startswith("G"):
        return f"<location><fractions>{token[1:]}</fractions></location>"
    if token == "T3":
        return "<Tuplet><normalNotes>2</normalNotes><actualNotes>3</actualNotes></Tuplet>"
    if token == "/T":
        return "<endTuplet/>"
    dots = token.count(".")
    kind = kinds[token[1:].rstrip(".")]
    dot = f"<dots>{dots}</dots>" if dots else ""
    if token[0] == "R":
        return f"<Rest>{dot}<durationType>{kind}</durationType></Rest>"
    return f"<Chord>{dot}<durationType>{kind}</durationType><Note><pitch>60</pitch></Note></Chord>"


def _score(*voices, sig=(4, 4)):
    body = "".join("<voice>" + "".join(_item(t) for t in v.split()) + "</voice>" for v in voices)
    ts = f"<TimeSig><sigN>{sig[0]}</sigN><sigD>{sig[1]}</sigD></TimeSig>"
    return etree.fromstring(
        f'<museScore><Score><Staff id="1"><Measure><voice>{ts}</voice></Measure>'
        f"<Measure>{body}</Measure></Staff></Score></museScore>".encode())


def _voices(root):
    return [[(el.tag, el.findtext("durationType") or el.findtext("fractions"))
             for el in v if el.tag in ("Chord", "Rest", "location")]
            for v in root.findall(".//Measure")[1].findall("voice")]


def test_a_rest_shared_at_the_end_of_the_bar_goes_to_both_voices():
    # Sangerhilsen m4: half tied to an eighth, then an eighth rest and a quarter rest
    # printed once for both tenors.
    root = _score("C2 C8 R8 R4", "C2 C8")
    assert share_rests(root) == 2
    assert _voices(root)[1] == [("Chord", "half"), ("Chord", "eighth"),
                                ("Rest", "eighth"), ("Rest", "quarter")]


def test_a_rest_shared_in_the_middle_of_the_bar_replaces_the_gap():
    root = _score("C4 C4 R4 C4", "C4 C4 G1/4 C4")
    assert share_rests(root) == 1
    assert _voices(root)[1] == [("Chord", "quarter"), ("Chord", "quarter"),
                                ("Rest", "quarter"), ("Chord", "quarter")]


def test_it_works_in_either_direction():
    root = _score("C2", "C2 R2")
    assert share_rests(root) == 1
    assert _voices(root)[0] == [("Chord", "half"), ("Rest", "half")]


def test_a_gap_the_other_voice_sings_through_is_a_missing_note_and_is_left():
    root = _score("C2 C4 R4", "C2")
    assert share_rests(root) == 0
    assert _voices(root)[1] == [("Chord", "half")]


def test_rests_that_do_not_line_up_with_the_gap_are_not_copied():
    # The lower voice stops a sixteenth into the upper voice's eighth rest.
    root = _score("C2 C8 R8 R4", "C2 C8 C16")
    assert share_rests(root) == 0


def test_a_rest_inside_a_triplet_is_not_copied():
    root = _score("C2 C4 T3 R8 R8 R8 /T", "C2 C4")
    assert share_rests(root) == 0


def test_a_full_bar_is_untouched_and_running_it_twice_copies_nothing_more():
    full = _score("C2 R2", "C2 R2")
    before = etree.tostring(full)
    assert share_rests(full) == 0
    assert etree.tostring(full) == before

    root = _score("C2 C8 R8 R4", "C2 C8")
    share_rests(root)
    once = etree.tostring(root)
    assert share_rests(root) == 0
    assert etree.tostring(root) == once


def test_the_bar_length_comes_from_the_time_signature():
    root = _score("C4 R8", "C4", sig=(3, 8))
    assert share_rests(root) == 1
    assert _voices(root)[1] == [("Chord", "quarter"), ("Rest", "eighth")]


def test_a_staff_with_one_voice_is_left_alone():
    root = _score("C2")
    assert share_rests(root) == 0
