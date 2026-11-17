"""The `bar` fix: a whole bar of one voice put as a person picked it (#295).

A pick among homr's whole-bar readings changes lengths and pitches together. What
these pin: the bar comes out as picked; when the notes stay in their places the ties,
slurs and words stay with them; when they do not — homr's second reading can have
more or fewer notes — ties reaching in from outside are cut, the words go back on the
notes in order, and the red mark goes either way. And it is strict like every kind.
"""
import pytest
from lxml import etree

from src.clean_score.utils.score_fixes import FixError, _bar_tokens, _measure, apply_fixes

SCORE = """<museScore version="3.02"><Score>
<Part><trackName>A1</trackName><Staff id="1"/></Part>
<Staff id="1">
  <Measure><voice>
    <Rest><durationType>half</durationType></Rest>
    <Chord><durationType>half</durationType><Note>
      <Spanner type="Tie"><Tie/><next><location><measures>1</measures><fractions>-1/2</fractions></location></next></Spanner>
      <pitch>60</pitch><tpc>14</tpc></Note></Chord>
  </voice></Measure>
  <Measure><voice>
    <StaffText><text>⚠ notes?</text></StaffText>
    <Chord><durationType>quarter</durationType><Lyrics><text>la</text></Lyrics><Note>
      <Spanner type="Tie"><prev><location><measures>-1</measures><fractions>1/2</fractions></location></prev></Spanner>
      <pitch>60</pitch><tpc>14</tpc></Note></Chord>
    <Chord><durationType>quarter</durationType><Lyrics><text>le</text></Lyrics><Note><pitch>62</pitch><tpc>16</tpc></Note></Chord>
    <Chord><durationType>half</durationType><Lyrics><text>li</text></Lyrics><Note><color r="255" g="0" b="0" a="255"/><pitch>64</pitch><tpc>18</tpc></Note></Chord>
  </voice></Measure>
  <Measure><voice>
    <Rest><durationType>measure</durationType><duration>4/4</duration></Rest>
  </voice></Measure>
</Staff>
</Score></museScore>"""

FROM = ["quarter:60", "quarter:62", "half:64"]


@pytest.fixture
def root():
    return etree.fromstring(SCORE)


def _fix(to, expect=FROM):
    return {"kind": "bar", "staff": 1, "measure": 2, "from": expect, "to": to,
            "why": "picked against the page"}


def _note(value, pitch, tpc):
    return {"value": value, "pitches": [pitch], "tpcs": [tpc]}


def _words(root, measure=2):
    return [lyr.findtext("text") for lyr in _measure(root, 1, measure).iter("Lyrics")]


def _ties(root, measure):
    return _measure(root, 1, measure).findall(".//Spanner[@type='Tie']")


def test_same_places_keep_the_tie_and_the_words(root):
    apply_fixes(root, [_fix([_note("note_4.", 60, 14), _note("note_8", 62, 16),
                             _note("note_2", 65, 13)])])
    assert _bar_tokens(_measure(root, 1, 2)) == ["quarter.:60", "eighth:62", "half:65"]
    assert _words(root) == ["la", "le", "li"]
    assert len(_ties(root, 1)) == 1 and len(_ties(root, 2)) == 1
    bar = _measure(root, 1, 2)
    assert bar.find(".//StaffText") is None  # the red mark
    assert bar.find(".//Note/color") is None  # and the red note
    assert [n.findtext("tpc") for n in bar.iter("Note")] == ["14", "16", "13"]


def test_a_tied_note_changing_pitch_loses_its_tie(root):
    apply_fixes(root, [_fix([_note("note_4", 59, 19), _note("note_4", 62, 16),
                             _note("note_2", 64, 18)])])
    assert _bar_tokens(_measure(root, 1, 2)) == ["quarter:59", "quarter:62", "half:64"]
    assert _ties(root, 1) == [] and _ties(root, 2) == []
    assert _words(root) == ["la", "le", "li"]


def test_a_bar_with_other_notes_is_written_afresh(root):
    apply_fixes(root, [_fix([_note("note_2", 60, 14), _note("note_2", 65, 13)])])
    assert _bar_tokens(_measure(root, 1, 2)) == ["half:60", "half:65"]
    # The tie from bar 1 pointed at a note that is not there any more.
    assert _ties(root, 1) == [] and _ties(root, 2) == []
    # The words stay in order; the one left over goes.
    assert _words(root) == ["la", "le"]
    assert _measure(root, 1, 2).find(".//StaffText") is None
    # The bars either side are untouched.
    assert _bar_tokens(_measure(root, 1, 3)) == ["measure:R"]


def test_a_fresh_bar_brackets_its_triplets_and_keeps_rests(root):
    apply_fixes(root, [_fix([_note("note_12", 60, 14), _note("note_12", 62, 16),
                             _note("note_12", 64, 18), {"value": "rest_4", "pitches": []},
                             _note("note_2", 65, 13)])])
    assert _bar_tokens(_measure(root, 1, 2)) == [
        "[tuplet", "eighth:60", "eighth:62", "eighth:64", "tuplet]", "quarter:R", "half:65"]


def test_a_chord_is_written_with_both_notes(root):
    apply_fixes(root, [_fix([{"value": "note_1", "pitches": [64, 60], "tpcs": [18, 14]}])])
    assert _bar_tokens(_measure(root, 1, 2)) == ["whole:60+64"]


def test_it_is_strict(root):
    with pytest.raises(FixError, match="recorded against"):
        apply_fixes(root, [_fix([_note("note_1", 60, 14)], expect=["whole:60"])])
    with pytest.raises(FixError, match="adds up"):
        apply_fixes(root, [_fix([_note("note_2", 60, 14)])])
    with pytest.raises(FixError, match="spellings"):
        apply_fixes(root, [_fix([{"value": "note_1", "pitches": [60]}])])
    assert _bar_tokens(_measure(root, 1, 2)) == FROM


@pytest.mark.parametrize("shape", ["same places", "new places"])
def test_a_whole_bar_answers_its_notes_and_leaves_the_other_problems(root, shape):
    """#290: `voice?` and cleaning's own marks are about something else in the bar."""
    from src.clean_score.utils.problem_marks import mark_bar
    bar = _measure(root, 1, 2)
    for el in list(bar.iter("StaffText")):
        el.getparent().remove(el)
    mark_bar(bar, "pitch? rhythm? voice? notes?")
    mark_bar(bar, "slur from A2 bar 1 removed; check the page")
    to = ([_note("note_4.", 60, 14), _note("note_8", 62, 16), _note("note_2", 65, 13)]
          if shape == "same places" else
          [_note("note_2", 60, 14), _note("note_4", 62, 16), _note("note_4", 65, 13)])
    apply_fixes(root, [_fix(to)])
    assert sorted(el.findtext("text") for el in bar.iter("StaffText")) == [
        "⚠ slur from A2 bar 1 removed; check the page", "⚠ voice?"]


# Cleaning's "cut to" mark (#482): `long_bars` writes it on the staff it cut, it has
# no `?` word, and only `unmark` took it off — after a `bar` fix had written the bar.
CUT = "⚠ was 9/8, cut to 4/4; removed E4"


def _with_cut(root):
    voice = _measure(root, 1, 2).find("voice")
    mark = etree.SubElement(voice, "StaffText")
    etree.SubElement(mark, "text").text = CUT
    voice.insert(0, mark)
    return root


def _texts(root, measure=2):
    return [el.findtext("text") for el in _measure(root, 1, measure).iter("StaffText")]


def test_a_bar_fix_takes_the_cut_mark_off_and_the_notes_go_black(root):
    _with_cut(root)
    to = [_note("note_4", 59, 19), _note("note_4", 62, 16), _note("note_2", 64, 18)]
    done = apply_fixes(root, [_fix(to)])
    assert CUT not in _texts(root)
    assert not any(t.startswith("⚠") for t in _texts(root))
    assert not _measure(root, 1, 2).findall(".//Note/color")
    assert "was 9/8, cut to 4/4" in done[0]


def test_another_kind_leaves_the_cut_mark(root):
    _with_cut(root)
    apply_fixes(root, [{"kind": "unmark", "staff": 1, "measure": 2, "text": "notes?"}])
    assert CUT in _texts(root)
    assert _measure(root, 1, 2).findall(".//Note/color")


def test_a_bar_fix_leaves_another_staffs_cut_mark():
    two = SCORE.replace('<Part><trackName>A1</trackName><Staff id="1"/></Part>',
                        '<Part><trackName>A1</trackName><Staff id="1"/></Part>'
                        '<Part><trackName>A2</trackName><Staff id="2"/></Part>')
    other = two[two.index('<Staff id="1">'):two.index("</Score>")].replace(
        '<Staff id="1">', '<Staff id="2">', 1)
    root = etree.fromstring(two.replace("</Score>", other + "</Score>"))
    voice = _measure(root, 2, 2).find("voice")
    mark = etree.Element("StaffText")
    etree.SubElement(mark, "text").text = CUT
    voice.insert(0, mark)
    to = [_note("note_4", 59, 19), _note("note_4", 62, 16), _note("note_2", 64, 18)]
    apply_fixes(root, [_fix(to)])
    assert CUT in [el.findtext("text") for el in _measure(root, 2, 2).iter("StaffText")]


def test_a_short_bar_keeps_its_cut_mark():
    # Cleaning cut the bar but could not spell the rest, so the voice holds 3/4 of
    # 4/4; a `bar` fix may keep it that short, and then the mark has to stay.
    short = SCORE.replace(
        '<Chord><durationType>half</durationType><Lyrics><text>li</text></Lyrics>',
        '<Chord><durationType>quarter</durationType><Lyrics><text>li</text></Lyrics>')
    root = _with_cut(etree.fromstring(short))
    done = apply_fixes(root, [_fix([_note("note_4", 59, 19), _note("note_4", 62, 16),
                                    _note("note_4", 64, 18)],
                                   expect=["quarter:60", "quarter:62", "quarter:64"])])
    assert CUT in _texts(root)
    assert "cut to" not in done[0]


def test_a_cut_in_another_voice_keeps_the_mark(root):
    _with_cut(root)
    second = etree.SubElement(_measure(root, 1, 2), "voice")
    second.append(etree.fromstring(
        "<Chord><durationType>whole</durationType><Note><pitch>52</pitch></Note></Chord>"))
    apply_fixes(root, [_fix([_note("note_4", 59, 19), _note("note_4", 62, 16),
                             _note("note_2", 64, 18)])])
    assert CUT in _texts(root)
