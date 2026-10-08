"""An `append` fix spells the notes it adds by the key in force (#357).

The spelling is derived, never typed, because hand-typed tpcs went wrong three times
in four. But it was derived from the pitch alone, with sharps: in a flat key a B flat
came out as A sharp, and the agent fixing Finlandia-hymni had to write a `bar` fix
with explicit tpcs to get the page's spelling.
"""
from lxml import etree

from src.clean_score.utils.score_fixes import apply_fixes, key_in_force, read_bar


def _score(*keys):
    """One staff, one bar per key: an empty value means the bar sets no key."""
    bars = []
    for key in keys:
        sig = f"<KeySig><accidental>{key}</accidental></KeySig>" if key != "" else ""
        bars.append(f"<Measure><voice>{sig}<Chord><durationType>half</durationType>"
                    "<Note><pitch>60</pitch><tpc>14</tpc></Note></Chord></voice></Measure>")
    return etree.fromstring(
        ('<museScore><Score><Part><trackName>T1</trackName><Staff id="1"/></Part>'
         f'<Staff id="1">{"".join(bars)}</Staff></Score></museScore>').encode())


def _append(root, measure, pitch):
    apply_fixes(root, [{"kind": "append", "staff": 1, "measure": measure,
                        "from": ["half:60"], "add": [f"half:{pitch}"], "why": "..."}])
    return read_bar(root, 1, measure)[-1]


def test_a_flat_key_spells_b_flat():
    note = _append(_score(-4), 1, 58)
    assert note["name"] == "Bb3"
    assert note["pitches"][0]["pitch"] == 58


def test_a_sharp_key_spells_a_sharp():
    assert _append(_score(2), 1, 58)["name"] == "A#3"


def test_a_white_key_stays_natural_in_a_flat_key():
    assert _append(_score(-4), 1, 59)["name"] == "B3"
    assert _append(_score(-1), 1, 64)["name"] == "E4"


def test_the_key_in_force_is_the_last_one_before_the_bar():
    root = _score(-3, "", 2, "")
    assert _append(root, 2, 63)["name"] == "Eb4"
    assert _append(root, 4, 63)["name"] == "D#4"
    measures = root.findall(".//Staff/Measure")
    assert [key_in_force(m) for m in measures] == [-3, -3, 2, 2]


def test_no_key_signature_keeps_the_old_spelling():
    assert _append(_score(""), 1, 61)["name"] == "C#4"
