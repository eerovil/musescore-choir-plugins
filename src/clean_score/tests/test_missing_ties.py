"""add_missing_ties copies a tie only onto a voice that sings the donor's rhythm.

The false case is Vieläkö huvittaisi's A1 ostinato (#284): the alto line holds a
tie from the third eighth to the following quarter, and the "dn tshi-ka" line
under it strikes the same pitch on the same beats as separate notes, each with
its own syllable. Pitch and span alone copied the tie into every bar.
"""

from lxml import etree

from src.clean_score.utils.missing_ties import add_missing_ties

TIE_START = ('<Spanner type="Tie"><Tie/><next><location><fractions>1/8</fractions>'
             "</location></next></Spanner>")
TIE_END = ('<Spanner type="Tie"><prev><location><fractions>-1/8</fractions>'
           "</location></prev></Spanner>")


def _chord(token):
    """'e66~' -> eighth F#4 starting a tie; 'q66' -> quarter; 'r8' -> eighth rest."""
    if token.startswith("r"):
        kind = {"8": "eighth", "4": "quarter", "2": "half"}[token[1:]]
        return f"<Rest><durationType>{kind}</durationType></Rest>"
    kind = {"e": "eighth", "q": "quarter", "h": "half", "w": "whole"}[token[0]]
    tie = token.endswith("~")
    pitch = token[1:].rstrip("~")
    return (f"<Chord><durationType>{kind}</durationType><Note>"
            f"{TIE_START if tie else ''}<pitch>{pitch}</pitch></Note></Chord>")


def _score(*staves):
    """Each staff is a list of bars, each bar a space-separated token string."""
    xml = ["<museScore><Score>"]
    for sid, bars in enumerate(staves, start=1):
        xml.append(f'<Staff id="{sid}">')
        for bar in bars:
            xml.append("<Measure><voice>")
            xml.extend(_chord(t) for t in bar.split())
            xml.append("</voice></Measure>")
        xml.append("</Staff>")
    xml.append("</Score></museScore>")
    root = etree.fromstring("".join(xml).encode("utf-8"))
    _close_ties(root)
    return root


def _close_ties(root):
    """Give the note after every tie start its `prev` half, as MuseScore writes it."""
    for staff in root.findall(".//Score/Staff"):
        chords = staff.findall(".//Chord")
        for a, b in zip(chords, chords[1:]):
            if a.find(".//Spanner[@type='Tie']/next") is not None:
                b.find("Note").insert(0, etree.fromstring(TIE_END))


def _ties(root, staff_id):
    """[(bar, chord index in bar)] of every tie start on a staff."""
    staff = root.find(f".//Score/Staff[@id='{staff_id}']")
    out = []
    for m, measure in enumerate(staff.findall("Measure"), start=1):
        for i, chord in enumerate(measure.findall(".//Chord")):
            if chord.find(".//Spanner[@type='Tie']/next") is not None:
                out.append((m, i))
    return out


def test_a_voice_singing_the_same_rhythm_gets_the_dropped_tie():
    root = _score(["e62 e64~ h64 q64"], ["e55 e57 h57 q57"])
    add_missing_ties(root)
    assert _ties(root, 2) == [(1, 1)]


def test_an_ostinato_on_the_same_pitch_is_not_tied():
    # Vieläkö huvittaisi bar 30: the donor rests where the ostinato strikes.
    donor = "e62 r8 r8 e62~ q62 e62 e62"
    ostinato = "q66 e66 e66 q66 e66 e66"
    root = _score([donor] * 3, [ostinato] * 3)
    added = add_missing_ties(root)
    assert added == []
    assert _ties(root, 2) == []


def test_a_tie_across_the_barline_ignores_what_follows_it():
    # The voices agree up to the held note and part straight after it.
    root = _score(["q60 q60 q60 q64~", "q64 h67 q65"],
                  ["q55 q55 q55 q59", "q59 e60 e60 h60"])
    add_missing_ties(root)
    assert _ties(root, 2) == [(1, 3)]


def test_a_different_rhythm_before_the_tie_in_the_next_bar_stops_it():
    root = _score(["q60 q60 q60 q64~", "q64 h67 q65"],
                  ["q55 q55 q55 e59 e59", "q59 h60 q60"])
    add_missing_ties(root)
    assert _ties(root, 2) == []


def test_two_different_pitches_are_never_tied():
    root = _score(["e62 e64~ h64 q64"], ["e55 e57 h59 q59"])
    add_missing_ties(root)
    assert _ties(root, 2) == []


def test_any_donor_in_the_same_rhythm_will_do():
    # The first voice holding a tie there sings another rhythm; the second matches.
    root = _score(["e60 e60 q64~ q64 q60"], ["q60 q64~ q64 q60"],
                  ["q55 q59 q59 q55"])
    add_missing_ties(root)
    assert _ties(root, 3) == [(1, 1)]
