"""Spacing: the scroll should not lurch, and no bar should pay for a bar it is far from."""

from fractions import Fraction

import pytest
from lxml import etree

from src.scrollvideo.engrave import engrave
from src.scrollvideo.geometry import Layout, NoteGeom
from src.scrollvideo.spacing import (DEFAULT_MAX_RATIO, SPACER_ID, _Bar,
                                     add_spacer_staff, capped_targets,
                                     even_engraving, measure_durations,
                                     measure_onsets, measure_widths, slot_durations,
                                     solve_plan, target_widths, visible_height)

SCORE = """<score-partwise version="3.1">
  <part-list><score-part id="P1"><part-name>T1</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>4</divisions>
        <time><beats>4</beats><beat-type>4</beat-type></time></attributes>
      <note><pitch><step>C</step><octave>4</octave></pitch><duration>16</duration></note>
    </measure>
    <measure number="2">
      <note><pitch><step>D</step><octave>4</octave></pitch><duration>4</duration></note>
      <note><chord/><pitch><step>F</step><octave>4</octave></pitch><duration>4</duration></note>
      <note><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration></note>
    </measure>
  </part>
</score-partwise>"""


def _spacer(tmp_path, slots, xml=SCORE):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "in.musicxml"
    source.write_text(xml)
    out = tmp_path / "out.musicxml"
    result = add_spacer_staff(str(source), str(out), slots)
    return result, etree.parse(str(out)).getroot() if result else None


# --- the score under test: ordinary bars, and the reported 4-note/32-note pair ---

def _note(duration, kind, step="C"):
    return (f"<note><pitch><step>{step}</step><octave>4</octave></pitch>"
            f"<duration>{duration}</duration><type>{kind}</type></note>")


SPARSE = _note(8, "quarter") * 4          # four quarter notes filling a 4/4 bar
DENSE = _note(1, "32nd") * 32             # the same bar as thirty-two 32nd notes


def _score(path, bars, beats=4):
    attributes = (f"<attributes><divisions>8</divisions><time><beats>{beats}</beats>"
                  "<beat-type>4</beat-type></time></attributes>")
    body = "".join(f'<measure number="{i + 1}">{attributes if i == 0 else ""}{bar}</measure>'
                   for i, bar in enumerate(bars))
    path.write_text(
        '<score-partwise version="3.1"><part-list><score-part id="P1">'
        "<part-name>T1</part-name></score-part></part-list>"
        f'<part id="P1">{body}</part></score-partwise>')
    return str(path)


def _engraved(tmp_path, bars, **kwargs):
    """Run the production spacing path and report what came out, per beat."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = _score(tmp_path / "score.musicxml", bars)
    engraving, spaced = even_engraving(source, str(tmp_path), engrave, **kwargs)
    widths = measure_widths(engraving.svg)
    durations = measure_durations(source)
    per_beat = [w / float(d) for w, d in zip(widths, durations)]
    return per_beat, spaced


def _worst_step(per_beat):
    return max(max(a / b, b / a) for a, b in zip(per_beat, per_beat[1:]))


# --- the arithmetic, on its own ---

def _bars(widths, onsets=4, slope=200.0):
    return [_Bar(natural=w, onsets=onsets, slots=onsets, width=w, slope=slope)
            for w in widths]


def test_a_score_already_within_the_cap_asks_for_nothing():
    widths = [1000.0, 1100.0, 1000.0]
    durations = [Fraction(4)] * 3
    assert capped_targets(widths, durations, 1.3) == [250.0, 275.0, 250.0]
    assert target_widths(widths, durations, 1.3) == widths
    assert solve_plan(_bars(widths), target_widths(widths, durations, 1.3)) == [4, 4, 4]


def test_a_dense_bar_tapers_away_instead_of_lifting_the_whole_song():
    """The pull from one wide bar dies away geometrically, so distant bars keep
    their natural width. This is the difference from a song-wide grid."""
    widths = [1000.0] * 4 + [8000.0] + [1000.0] * 4
    durations = [Fraction(4)] * 9
    targets = capped_targets(widths, durations, 2.0)

    assert targets == [250.0, 250.0, 500.0, 1000.0, 2000.0, 1000.0, 500.0, 250.0, 250.0]
    assert targets[0] == pytest.approx(widths[0] / 4)     # the far end is untouched


def test_the_cap_compares_speed_and_not_width():
    """A 3/4 bar beside a 4/4 bar of the same density is narrower and must not be
    read as a step; a 3/4 bar as wide as the 4/4 one must."""
    even = capped_targets([1000.0, 750.0], [Fraction(4), Fraction(3)], 1.3)
    assert even == [250.0, 250.0]                       # nothing to fix

    uneven = capped_targets([1000.0, 1000.0], [Fraction(4), Fraction(3)], 1.3)
    assert uneven[1] == pytest.approx(1000.0 / 3)
    assert uneven[0] == pytest.approx(1000.0 / 3 / 1.3)  # the 4/4 bar has to widen


def test_no_bar_is_given_a_rest_it_does_not_need():
    """Minimality within the mechanism: one fewer rest anywhere misses a target."""
    widths = [1000.0] * 4 + [8000.0] + [1000.0] * 4
    durations = [Fraction(4)] * 9
    slope, onsets = 200.0, 4
    wanted = target_widths(widths, durations, 1.3)
    slots = solve_plan(_bars(widths, onsets, slope), wanted)

    for index, count in enumerate(slots):
        assert count >= onsets                              # never below its own music
        reached = widths[index] + slope * (count - onsets)
        assert reached >= wanted[index] - 1e-6
        if count > onsets:
            assert widths[index] + slope * (count - 1 - onsets) < wanted[index]


# --- the same thing through verovio ---

def test_an_ordinary_score_is_engraved_at_its_natural_width(tmp_path):
    """Eight plain 4-note bars need no help, so no spacer staff is built at all."""
    per_beat, spaced = _engraved(tmp_path, [SPARSE] * 8)

    assert spaced is False
    assert _worst_step(per_beat) < DEFAULT_MAX_RATIO
    assert per_beat[1:] == pytest.approx([per_beat[1]] * 7)


def test_the_reported_four_against_thirty_two_stops_lurching(tmp_path):
    """The bug as reported: equal-length bars, 4 notes against 32."""
    bars = [SPARSE, DENSE, SPARSE, SPARSE]
    natural, _ = _engraved(tmp_path / "natural", bars, max_ratio=0)
    fixed, spaced = _engraved(tmp_path / "fixed", bars)

    assert _worst_step(natural) > 5.0
    assert spaced is True
    # One slot is the smallest step the rest staff can take, so the engraved step
    # lands within a slot's worth of the cap rather than exactly on it.
    assert _worst_step(fixed) < DEFAULT_MAX_RATIO * 1.05


def test_one_dense_bar_widens_its_neighbours_and_not_the_whole_song(tmp_path):
    """A song-wide grid would widen bar 1 as much as bar 9. This must not."""
    bars = [SPARSE] * 8 + [DENSE] + [SPARSE] * 8
    natural, _ = _engraved(tmp_path / "natural", bars, max_ratio=0)
    fixed, _ = _engraved(tmp_path / "fixed", bars)

    assert fixed[-1] == pytest.approx(natural[-1])       # the far end is untouched
    assert fixed[8] == pytest.approx(natural[8])         # the dense bar is not widened
    tail = fixed[8:]
    assert tail == sorted(tail, reverse=True)            # and it tapers, monotonically
    assert sum(fixed) < sum([fixed[8]] * len(fixed)) / 2  # nowhere near a global grid


def test_a_short_bar_is_not_widened_for_being_short(tmp_path):
    """A 2/4 bar among 4/4 bars is half the width and exactly the same speed.

    Compared on raw width it looks like a 2x step and would be widened to match
    its neighbours — which would make the video crawl through it. Compared per
    beat there is nothing wrong with it, and nothing is added to the score."""
    half = _note(8, "quarter") * 2
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "score.musicxml"
    body = (f'<measure number="1"><attributes><divisions>8</divisions><time>'
            f"<beats>4</beats><beat-type>4</beat-type></time></attributes>{SPARSE}</measure>"
            f'<measure number="2"><attributes><time><beats>2</beats>'
            f"<beat-type>4</beat-type></time></attributes>{half}</measure>"
            f'<measure number="3"><attributes><time><beats>4</beats>'
            f"<beat-type>4</beat-type></time></attributes>{SPARSE}</measure>")
    source.write_text(
        '<score-partwise version="3.1"><part-list><score-part id="P1">'
        "<part-name>T1</part-name></score-part></part-list>"
        f'<part id="P1">{body}</part></score-partwise>')

    assert measure_durations(str(source)) == [Fraction(4), Fraction(2), Fraction(4)]
    engraving, spaced = even_engraving(str(source), str(tmp_path), engrave)
    widths = measure_widths(engraving.svg)

    assert spaced is False
    assert widths[2] / widths[1] > 1.6               # it really is a much narrower bar
    assert _worst_step([widths[1] / 2, widths[2] / 4]) < DEFAULT_MAX_RATIO


def test_more_rests_widen_a_bar_and_never_narrow_it(tmp_path):
    """The one fact the planner reasons from, and the shape of it.

    A bar does not budge until it is asked for more moments than its music already
    has — a quarter rest over a quarter note is the same moment — and past that it
    widens steadily. The planner starts each bar at its own moment count for exactly
    that reason, and measures the rest instead of assuming it."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = _score(tmp_path / "s.musicxml", [SPARSE] * 3)
    widths = {}
    for count in (1, 2, 4, 8, 16):
        path = add_spacer_staff(source, str(tmp_path / f"n{count}.musicxml"),
                                [count] * 3)
        widths[count] = measure_widths(engrave(path).svg)[2]

    assert measure_onsets(source)[2] == 4                 # four quarter notes
    assert widths[1] == widths[2] == widths[4]            # nothing new to space
    assert widths[8] > widths[4] and widths[16] > widths[8]


def test_the_spacer_does_not_move_the_music_in_time(tmp_path):
    """The rests are written as real note values, and this is why.

    Verovio reads what a rest is *written* as, not its `<duration>`, so a rest of
    "one fifth of a bar" is taken for a whole rest: the spacer part then runs longer
    than the music, every following bar starts late, and the highlights drift off
    the audio. Nothing in the picture shows it — the bars still look right."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = _score(tmp_path / "s.musicxml", [SPARSE] * 3)
    plain = max(float(e.get("qstamp", 0)) for e in engrave(source).timemap)

    for slots in ([5, 5, 5], [3, 7, 11], [13, 13, 13]):
        path = add_spacer_staff(source, str(tmp_path / "spaced.musicxml"), slots)
        spaced = max(float(e.get("qstamp", 0)) for e in engrave(path).timemap)
        assert spaced == plain


# --- writing the spacer part ---

def test_a_spacer_part_is_appended(tmp_path):
    _, root = _spacer(tmp_path, [4, 4])
    ids = [p.get("id") for p in root.findall("part")]
    assert ids == ["P1", SPACER_ID]
    assert [sp.get("id") for sp in root.find("part-list").findall("score-part")][-1] == SPACER_ID


def test_each_measure_gets_the_slots_it_was_planned(tmp_path):
    _, root = _spacer(tmp_path, [7, 3])
    spacer = root.findall("part")[-1]
    assert [len(m.findall("note")) for m in spacer.findall("measure")] == [7, 3]


def test_a_count_that_divides_the_bar_unevenly_is_still_written(tmp_path):
    """Five rests in a 4/4 bar: no note value is a fifth of a bar, so they cannot
    all be the same. Written as three quarters and two eighths they can, and the
    granularity of the whole mechanism would be twice as coarse otherwise."""
    assert slot_durations(Fraction(4), 5) == [Fraction(1, 2), Fraction(1, 2),
                                              Fraction(1), Fraction(1), Fraction(1)]
    _, root = _spacer(tmp_path, [5, 5])
    first = root.findall("part")[-1].find("measure")
    divisions = int(first.findtext("attributes/divisions"))
    assert len(first.findall("note")) == 5
    assert sum(int(n.findtext("duration")) for n in first.iter("note")) == 4 * divisions
    assert {n.findtext("type") for n in first.iter("note")} == {"quarter", "eighth"}


def test_an_odd_length_bar_is_written_in_the_values_it_takes(tmp_path):
    """7/8 is a dotted half plus an eighth, and halving those keeps them writable."""
    assert slot_durations(Fraction(7, 2), 2) == [Fraction(3), Fraction(1, 2)]
    assert slot_durations(Fraction(7, 2), 3) == [Fraction(3, 2), Fraction(3, 2),
                                                 Fraction(1, 2)]
    assert sum(slot_durations(Fraction(7, 2), 6)) == Fraction(7, 2)


def test_a_chord_does_not_lengthen_a_measure(tmp_path):
    """Measure 2 is half a bar long despite its chord; its rests must say so."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "in.musicxml"
    source.write_text(SCORE)
    assert measure_durations(str(source)) == [Fraction(4), Fraction(2)]

    _, root = _spacer(tmp_path, [4, 4])
    second = root.findall("part")[-1].findall("measure")[1]
    assert sum(slot_durations(Fraction(2), 4)) == Fraction(2)
    assert len(second.findall("note")) == 4


def test_the_spacer_holds_only_rests(tmp_path):
    _, root = _spacer(tmp_path, [4, 4])
    spacer = root.findall("part")[-1]
    for note in spacer.iter("note"):
        assert note.find("rest") is not None
        assert note.find("pitch") is None


def test_the_singing_parts_are_untouched(tmp_path):
    _, root = _spacer(tmp_path, [4, 4])
    original = etree.fromstring(SCORE.encode())
    assert (etree.tostring(root.find("part[@id='P1']"))
            == etree.tostring(original.find("part[@id='P1']")))


def test_a_plan_that_does_not_fit_the_score_is_refused(tmp_path):
    result, _ = _spacer(tmp_path, [4, 4, 4])
    assert result is None


def test_a_two_voice_bar_is_not_read_as_twice_as_long(tmp_path):
    """The second voice is written after the first with the cursor wound back, so
    adding up every note reports the bar as twice its length — and every target
    computed for it would then be half what it should be."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "voices.musicxml"
    lower = ('<note><pitch><step>E</step><octave>3</octave></pitch><duration>16</duration>'
             "<voice>2</voice><type>half</type></note>") * 2
    source.write_text(
        '<score-partwise version="3.1"><part-list><score-part id="P1">'
        "<part-name>T1</part-name></score-part></part-list><part id=\"P1\">"
        '<measure number="1"><attributes><divisions>8</divisions><time><beats>4</beats>'
        f"<beat-type>4</beat-type></time></attributes>{SPARSE}"
        f"<backup><duration>32</duration></backup>{lower}</measure></part></score-partwise>")

    assert measure_durations(str(source)) == [Fraction(4)]


def test_a_bar_of_an_unwritable_length_keeps_the_music_in_time(tmp_path):
    """An OCR-damaged bar can be a length no rest spells. It must not be filled
    approximately: the spacer part would then be a different length from the music
    and everything after it would sound late. It gets a whole-measure rest."""
    seven_thirds = _note(4, "eighth") * 7          # 28 of 12 divisions = 7/3 quarters
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "odd.musicxml"
    plain = _note(12, "quarter") * 4
    body = ('<measure number="1"><attributes><divisions>12</divisions><time>'
            "<beats>4</beats><beat-type>4</beat-type></time></attributes>"
            f'{plain}</measure><measure number="2">{seven_thirds}</measure>'
            f'<measure number="3">{plain}</measure>')
    source.write_text(
        '<score-partwise version="3.1"><part-list><score-part id="P1">'
        "<part-name>T1</part-name></score-part></part-list>"
        f'<part id="P1">{body}</part></score-partwise>')

    assert measure_durations(str(source))[1] == Fraction(7, 3)
    assert slot_durations(Fraction(7, 3), 6) == []       # nothing fills it exactly
    path = add_spacer_staff(str(source), str(tmp_path / "spaced.musicxml"), [12, 8, 12])
    odd = etree.parse(path).getroot().findall("part")[-1].findall("measure")[1]

    assert [n.find("rest").get("measure") for n in odd.findall("note")] == ["yes"]
    ends = lambda p: max(float(e.get("qstamp", 0)) for e in engrave(p).timemap)
    assert ends(path) == ends(str(source))


def test_the_spacer_counts_finely_enough_for_what_it_wrote(tmp_path):
    """A source counting in whole quarters can still carry a bar of 16 rests: the
    spacer part owns its own divisions and picks whatever makes its rests whole."""
    coarse = SCORE.replace("<divisions>4</divisions>", "<divisions>1</divisions>") \
        .replace("<duration>16</duration>", "<duration>WHOLE</duration>") \
        .replace("<duration>4</duration>", "<duration>1</duration>") \
        .replace("<duration>WHOLE</duration>", "<duration>4</duration>")
    _result, root = _spacer(tmp_path, [16, 8], xml=coarse)
    first = root.findall("part")[-1].find("measure")
    divisions = int(first.findtext("attributes/divisions"))

    assert divisions == 4                                    # 16ths of a quarter note
    assert len(first.findall("note")) == 16
    assert {n.findtext("duration") for n in first.iter("note")} == {"1"}


def test_source_division_changes_are_read_where_they_are_declared(tmp_path):
    """A part that recounts mid-piece still gets bars of the right length.

    Measure 2 says the same music in eighths of a quarter instead of quarters, so
    its numbers double. Read against measure 1's divisions it would come out twice
    as long, and every target computed for it would be half what it should be."""
    changed = SCORE.replace(
        '<measure number="2">',
        '<measure number="2"><attributes><divisions>8</divisions></attributes>'
    ).replace("<duration>4</duration>", "<duration>8</duration>")

    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "in.musicxml"
    source.write_text(changed)
    assert measure_durations(str(source)) == [Fraction(4), Fraction(2)]


# --- cropping the spacer back off ---

def _layout(staff_tops, spacing=100.0, height=1000.0):
    notes = {f"n{i}": NoteGeom(0.0, top + 50, top, spacing)
             for i, top in enumerate(staff_tops)}
    return Layout(width=5000.0, height=height, notes=notes, staff_tops=list(staff_tops))


def test_visible_height_cuts_just_above_the_spacer_staff():
    """Only enough margin to clear the staff line: the last singing staff's
    lyrics live in that gap and a generous margin clips them."""
    layout = _layout([100.0, 400.0, 800.0])
    assert visible_height(layout) == pytest.approx(800.0 - 15.0)


def test_visible_height_is_the_whole_page_when_there_is_no_spacer():
    assert visible_height(_layout([100.0])) == 1000.0
