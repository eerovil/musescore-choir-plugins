"""Reading a score one printed system at a time.

Two halves, and they are tested differently. **Flattening** is about what homr
emits: a part is not a part, it is one or two staves wearing a name that means
nothing, and the tests here are little documents in the shapes homr actually
produced on the benchmark -- four one-staff "Voice" parts, two two-staff
"Piano" parts, and a mixture of the two. **Assembling** is about the seams that
only exist because each crop is its own document: bar 1 five times over, a
different ``divisions`` each time, and a key and time signature re-declared at
every join.

The last test is the one that matters and the only one that needs MuseScore: a
score assembled from systems of two, three and two staves, converted and handed
to ``clean_score``'s per-system machinery, has to come back as those systems
with those staves. That is the whole point -- the app assembles, and the grid
asks a person which staff is which part.

No test here runs homr. Whether it can read music is homr's business, and the
card's own acceptance ran on the frozen benchmark, which is host state.
"""
import os
import subprocess

import pytest
from lxml import etree

from src.clean_score.utils import per_system
from src.song_app import omr, omr_systems
from src.song_app.pdf_systems import SystemBounds, SystemImage


# --- little documents in the shapes homr emits ---------------------------


def a_note(step="C", octave="4", duration=4, voice="1", staff=None):
    staff_tag = f"<staff>{staff}</staff>" if staff else ""
    return (f"<note><pitch><step>{step}</step><octave>{octave}</octave></pitch>"
            f"<duration>{duration}</duration><voice>{voice}</voice><type>quarter</type>"
            f"{staff_tag}</note>")


def a_part(part_id, name, staves, bars=2, divisions=4, fifths=0, time=(4, 4)):
    """One ``<part>`` as homr writes it: ``staves`` staff rows inside it.

    Its bars are as long as the signature says, because assembly now reads the
    numerator off them: a helper that declared 3/4 over four quarters would be
    asking a real question of the meter plan and calling the honest answer wrong.
    """
    beat = divisions * 4 // time[1]
    bar_length = beat * time[0]
    clefs = "".join(f'<clef number="{n}"><sign>G</sign><line>2</line></clef>'
                    for n in range(1, staves + 1))
    attributes = (f"<attributes><divisions>{divisions}</divisions>"
                  f"<key><fifths>{fifths}</fifths></key>"
                  f"<time><beats>{time[0]}</beats><beat-type>{time[1]}</beat-type></time>"
                  f"<staves>{staves}</staves>{clefs}</attributes>")
    body = []
    for bar in range(1, bars + 1):
        content = attributes if bar == 1 else ""
        for staff in range(1, staves + 1):
            if staff > 1:
                content += f"<backup><duration>{bar_length}</duration></backup>"
            content += "".join(
                a_note("CDEFG"[(staff + n) % 5], duration=beat,
                       staff=staff if staves > 1 else None, voice=str(staff))
                for n in range(time[0]))
        body.append(f'<measure number="{bar}">{content}</measure>')
    return (f'<score-part id="{part_id}"><part-name>{name}</part-name></score-part>',
            f'<part id="{part_id}">{"".join(body)}</part>')


def a_system(tmp_path, name, parts):
    """A MusicXML file holding ``parts`` -- ``[(id, name, staff count), ...]``."""
    declared, bodies = zip(*[a_part(*p) for p in parts])
    path = tmp_path / f"{name}.musicxml"
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?><score-partwise version="4.0">'
        f'<part-list>{"".join(declared)}</part-list>{"".join(bodies)}'
        "</score-partwise>")
    return str(path)


def scan(index, staves, bars=2, divisions=4, fifths=0, time=(4, 4)):
    """A already-flattened system, for the assembly tests."""
    made = []
    for n in range(staves):
        source = a_part(f"P{n + 1}", "Voice", 1, bars=bars, divisions=divisions,
                        fifths=fifths, time=time)[1]
        part = etree.fromstring(source)
        made.extend(omr_systems.flatten_part(part))
    return omr_systems.SystemScan(index=index, musicxml="", staves=made)


# --- flattening ----------------------------------------------------------


def test_four_one_staff_parts_are_four_staves(tmp_path):
    path = a_system(tmp_path, "sys1", [(f"P{n}", "Voice", 1) for n in range(1, 5)])
    assert len(omr_systems.flatten(path)) == 4


def test_a_fused_grand_staff_is_still_two_staves(tmp_path):
    """B5's second system: four staves reported as two "Piano" parts."""
    path = a_system(tmp_path, "sys2", [("P1", "Piano", 2), ("P2", "Piano", 2)])
    staves = omr_systems.flatten(path)
    assert len(staves) == 4


def test_a_mixture_flattens_in_reading_order(tmp_path):
    """B5's third system: Voice, Piano (two staves), Voice.

    The pair in the middle stays in the middle -- its two staves are the second
    and third of the system, not two extra ones appended after the Voices. A
    system is an ordered list of lines on a page, and the order is all the grid
    has to point at them with.
    """
    def voice(part_id, octave):
        return (f'<part id="{part_id}"><measure number="1">'
                "<attributes><divisions>1</divisions></attributes>"
                + a_note("C", octave, duration=1) + "</measure></part>")

    piano = ('<part id="P2"><measure number="1">'
             "<attributes><divisions>1</divisions><staves>2</staves></attributes>"
             + a_note("C", "5", duration=1, staff=1)
             + "<backup><duration>1</duration></backup>"
             + a_note("C", "3", duration=1, staff=2) + "</measure></part>")

    path = tmp_path / "sys3.musicxml"
    path.write_text('<?xml version="1.0" encoding="UTF-8"?>'
                    "<score-partwise><part-list/>"
                    + voice("P1", "6") + piano + voice("P3", "2")
                    + "</score-partwise>")
    staves = omr_systems.flatten(str(path))
    octaves = [s.measures[0].find("note/pitch/octave").text for s in staves]
    assert octaves == ["6", "5", "3", "2"]


def test_the_part_name_is_never_read(tmp_path):
    """homr says "Voice" and "Piano" and means neither, so nothing may depend
    on which word it chose."""
    voices = a_system(tmp_path, "as-voices", [("P1", "Voice", 2)])
    pianos = a_system(tmp_path, "as-pianos", [("P1", "Piano", 2)])
    assert len(omr_systems.flatten(voices)) == len(omr_systems.flatten(pianos)) == 2


def test_a_staff_keeps_only_its_own_notes_and_its_own_clef(tmp_path):
    path = a_system(tmp_path, "pair", [("P1", "Piano", 2)])
    upper, lower = omr_systems.flatten(path)
    for staff in (upper, lower):
        first = staff.measures[0]
        assert first.findall("backup") == []
        assert [n.findtext("staff") for n in first.findall("note")] == [None] * 4
        assert first.find("attributes/staves") is None
        assert first.find("attributes/clef").get("number") is None


def test_voices_are_renumbered_from_one(tmp_path):
    """A voice number means something only inside its part, and each staff is
    about to become a part of its own."""
    path = a_system(tmp_path, "pair", [("P1", "Piano", 2)])
    lower = omr_systems.flatten(path)[1]
    assert {n.findtext("voice") for n in lower.measures[0].findall("note")} == {"1"}


def onsets(measure):
    """Where each note of a flattened bar sounds, by voice, following its cursor.

    Read the same way the flattening reads homr: a note moves the cursor on, a
    ``backup`` winds it back, a ``forward`` moves it on. What comes back is
    ``{voice: [(onset, step), ...]}`` -- the beat, and the note written there.
    """
    out, at, onset = {}, 0, 0
    for child in measure:
        if child.tag == "backup":
            at -= int(child.findtext("duration"))
        elif child.tag == "forward":
            at += int(child.findtext("duration"))
        elif child.tag == "note":
            if child.find("chord") is None:
                onset = at
                at += int(child.findtext("duration") or 0)
            out.setdefault(child.findtext("voice"), []).append(
                (onset, child.findtext("pitch/step")))
    return out


def test_two_voices_on_one_staff_start_together():
    """Two voices homr started together still start together."""
    part = etree.fromstring(
        '<part id="P1"><measure number="1">'
        "<attributes><divisions>1</divisions></attributes>"
        + a_note("C", duration=1, voice="1") + a_note("D", duration=1, voice="1")
        + "<backup><duration>2</duration></backup>"
        + a_note("E", duration=1, voice="2") + a_note("F", duration=1, voice="2")
        + "</measure></part>")
    staff = omr_systems.flatten_part(part)[0]
    assert onsets(staff.measures[0]) == {
        "1": [(0, "C"), (1, "D")], "2": [(0, "E"), (1, "F")]}


def test_a_voice_homr_wrote_later_in_the_bar_stays_there():
    """Issue #172. Three notes homr wrote one after the other sound one after
    the other, whatever voice numbers it gave them.

    This is the bar the old rebuild lost: it started every voice again at the
    head of the bar, so these three stacked up on beat one -- three singers
    holding a chord where the page has a phrase. Measured over 61 systems in
    issue #166, that slide cost 18.8 points of note accuracy.
    """
    notes = "".join(a_note("C", duration=1, voice=str(v)) for v in (1, 2, 3))
    part = etree.fromstring(
        '<part id="P1"><measure number="1">'
        "<attributes><divisions>1</divisions></attributes>" + notes + "</measure></part>")
    staff = omr_systems.flatten_part(part)[0]
    assert onsets(staff.measures[0]) == {
        "1": [(0, "C")], "2": [(1, "C")], "3": [(2, "C")]}


def test_a_voice_that_enters_mid_bar_enters_mid_bar():
    """The shape that costs the most: homr backs up part of the way, not all of
    it, and the voice it is placing sings from the middle of the bar."""
    part = etree.fromstring(
        '<part id="P1"><measure number="1">'
        "<attributes><divisions>1</divisions></attributes>"
        + "".join(a_note(s, duration=1, voice="1") for s in "CDEF")
        + "<backup><duration>2</duration></backup>"
        + a_note("G", duration=1, voice="2") + a_note("A", duration=1, voice="2")
        + "</measure></part>")
    staff = omr_systems.flatten_part(part)[0]
    assert onsets(staff.measures[0])["2"] == [(2, "G"), (3, "A")]


def test_a_chord_stays_stacked_on_the_note_it_shares_a_beat_with():
    """A chord note sounds *with* the note before it, so nothing steps between
    them -- winding back over the leader would be the slide in miniature.

    The bar is also asserted to come out with **no step element at all**, which
    is the half that guards the note *after* the chord. A cursor that counted a
    chord note's duration would stand a whole note further on than the XML does,
    and the next note would be backed up to somewhere earlier than homr put it.
    Nothing here relies on that being noticed by eye: a `backup` appearing in
    this bar fails the test.
    """
    stacked = ('<note><chord/><pitch><step>E</step><octave>4</octave></pitch>'
               "<duration>2</duration><voice>1</voice></note>")
    part = etree.fromstring(
        '<part id="P1"><measure number="1">'
        "<attributes><divisions>1</divisions></attributes>"
        + a_note("C", duration=2, voice="1") + stacked
        + a_note("D", duration=2, voice="1")
        + "</measure></part>")
    staff = omr_systems.flatten_part(part)[0]
    assert staff.measures[0].findall("backup") == []
    assert staff.measures[0].findall("forward") == []
    assert onsets(staff.measures[0]) == {"1": [(0, "C"), (0, "E"), (2, "D")]}


def test_the_staves_of_a_fused_part_each_start_at_the_head_of_the_bar():
    """The backup between two staves of a grand staff is homr saying "the second
    staff starts again here", and the second staff has to arrive believing it."""
    part = etree.fromstring(
        '<part id="P1"><measure number="1">'
        "<attributes><divisions>1</divisions><staves>2</staves></attributes>"
        + a_note("C", duration=1, voice="1", staff=1)
        + a_note("D", duration=1, voice="1", staff=1)
        + "<backup><duration>2</duration></backup>"
        + a_note("E", duration=1, voice="2", staff=2)
        + a_note("F", duration=1, voice="2", staff=2)
        + "</measure></part>")
    upper, lower = omr_systems.flatten_part(part)
    assert onsets(upper.measures[0]) == {"1": [(0, "C"), (1, "D")]}
    assert onsets(lower.measures[0]) == {"1": [(0, "E"), (1, "F")]}


# --- the same thing on a real parse --------------------------------------

#: One printed system of Herää Suomi as homr really read it, kept beside the
#: test rather than read out of `songs/`, which is live and has changed under
#: two cards already. Two staves of two voices each, written the way homr writes
#: them: a note, a backup, the next voice's note, over and over. The page is in
#: the public-domain slice of the benchmark (`fixtures/omr-benchmark`, B1a).
A_REAL_SYSTEM = os.path.join(os.path.dirname(__file__), "test_files",
                             "voices_across_the_bar.musicxml")


def where_homr_put_them(path, staff):
    """One staff's notes as homr placed them: ``{bar: {(beat, pitch), ...}}``.

    Read off the part's own cursor, which is the thing the flattening has to
    keep. Beats are in that document's divisions.
    """
    root = etree.parse(path).getroot()
    out = {}
    for part in root.findall("part"):
        for bar, measure in enumerate(part.findall("measure"), start=1):
            found, at, onset = set(), 0, 0
            for child in measure:
                if child.tag == "backup":
                    at -= int(child.findtext("duration"))
                elif child.tag == "forward":
                    at += int(child.findtext("duration"))
                elif child.tag == "note":
                    if child.find("chord") is None:
                        onset = at
                        at += int(child.findtext("duration") or 0)
                    if (child.findtext("staff") or "1") == str(staff):
                        found.add((onset, child.findtext("pitch/step"),
                                   child.findtext("pitch/octave")))
            out[bar] = found
    return out


def where_they_came_out(staff):
    out = {}
    for bar, measure in enumerate(staff.measures, start=1):
        found, at, onset = set(), 0, 0
        for child in measure:
            if child.tag == "backup":
                at -= int(child.findtext("duration"))
            elif child.tag == "forward":
                at += int(child.findtext("duration"))
            elif child.tag == "note":
                if child.find("chord") is None:
                    onset = at
                    at += int(child.findtext("duration") or 0)
                found.add((onset, child.findtext("pitch/step"),
                           child.findtext("pitch/octave")))
        out[bar] = found
    return out


def test_a_real_system_comes_out_on_the_beats_homr_put_it_on():
    """Issue #172's acceptance in one file: every note of a real parse, on the
    beat homr gave it. The old rebuild moved 16 of this system's 57."""
    staves = omr_systems.flatten(A_REAL_SYSTEM)
    assert len(staves) == 2
    for number, staff in enumerate(staves, start=1):
        assert where_they_came_out(staff) == where_homr_put_them(A_REAL_SYSTEM, number)


def test_the_upper_voice_of_the_real_bar_two_enters_where_the_page_has_it():
    """The shape the old rebuild lost, named. homr reads the upper staff of bar
    2 as a whole note in one voice and a phrase in the other that starts on the
    second beat; laying that second voice from the head of the bar puts its
    every note a beat early for the rest of the bar."""
    upper = omr_systems.flatten(A_REAL_SYSTEM)[0]
    # Divisions are 4, so these are beats 2, 2¾, 3 and 4 of a bar of four.
    assert onsets(upper.measures[1])["2"] == [
        (4, "A"), (7, "A"), (8, "A"), (12, "G")]
    # The old rebuild wrote the same four notes at 0, 3, 4 and 8 -- the phrase
    # entire, a beat early, under a whole note that does start on beat one.


# --- which singer is voice 1 (issue #187) --------------------------------

#: The two printed systems either side of one join of Kaksi laulua krapulasta 2,
#: as homr really read them, committed rather than read out of `songs/`. See
#: `test_files/README.md` for what makes them the fixture.
A_JOIN = [os.path.join(os.path.dirname(__file__), "test_files", name)
          for name in ("voice_rank_join_first.musicxml",
                       "voice_rank_join_second.musicxml")]

STEPS = "CDEFGAB"


def voices_by_height(measure):
    """``{voice: mean height}`` for one flattened bar, in diatonic steps.

    Height and not pitch name, because all that is being asked is which of the
    two is written above the other.
    """
    heights = {}
    for note in measure.iter("note"):
        step = note.findtext("pitch/step")
        if step is None:
            continue
        heights.setdefault(note.findtext("voice"), []).append(
            7 * int(note.findtext("pitch/octave")) + STEPS.index(step))
    return {voice: sum(seen) / len(seen) for voice, seen in heights.items()}


def test_a_voice_keeps_its_number_when_homr_writes_it_first():
    """Issue #187, in miniature. Which voice homr writes first is a fact about
    its interleaving, not about the music, so it must not decide who is voice 1.

    Both bars hold the same two lines. The first is written lower voice first,
    the second upper voice first, and homr calls them 2 and 1 in both.
    """
    lower = a_note("C", octave="4", duration=1, voice="2")
    upper = a_note("A", octave="5", duration=1, voice="1")

    def bar(number, first, second):
        return (f'<measure number="{number}">'
                "<attributes><divisions>1</divisions></attributes>"
                + first + "<backup><duration>1</duration></backup>" + second
                + "</measure>")
    part = etree.fromstring('<part id="P1">' + bar(1, lower, upper)
                            + bar(2, upper, lower) + "</part>")
    staff = omr_systems.flatten_part(part)[0]
    # The A5 is homr's voice 1 in both bars, so it is voice 1 in both bars here.
    assert onsets(staff.measures[0])["1"] == [(0, "A")]
    assert onsets(staff.measures[1])["1"] == [(0, "A")]


def test_a_bar_only_the_lower_voice_sings_does_not_promote_it():
    """The other half of the same defect, and the one that is invisible.

    Nothing is written the wrong way round here -- the upper voice simply rests
    through the second bar. Numbering each bar on its own compacted the one
    voice left down to 1, so the lower singer took over the upper part for a
    bar and handed it back afterwards.
    """
    part = etree.fromstring(
        '<part id="P1">'
        '<measure number="1"><attributes><divisions>1</divisions></attributes>'
        + a_note("A", octave="5", duration=1, voice="1")
        + "<backup><duration>1</duration></backup>"
        + a_note("C", octave="4", duration=1, voice="2") + "</measure>"
        '<measure number="2">' + a_note("D", octave="4", duration=1, voice="2")
        + "</measure></part>")
    staff = omr_systems.flatten_part(part)[0]
    assert onsets(staff.measures[1]) == {"2": [(0, "D")]}


def test_a_voice_number_homr_never_used_is_still_compacted():
    """Renumbering from 1 is still done -- a staff arriving as voice 5 of a part
    that has one is what it is for. Only *which* voice gets which number
    changes."""
    part = etree.fromstring(
        '<part id="P1"><measure number="1">'
        "<attributes><divisions>1</divisions></attributes>"
        + a_note("A", octave="5", duration=1, voice="5")
        + "<backup><duration>1</duration></backup>"
        + a_note("C", octave="4", duration=1, voice="7") + "</measure></part>")
    staff = omr_systems.flatten_part(part)[0]
    assert onsets(staff.measures[0]) == {"1": [(0, "A")], "2": [(0, "C")]}


def test_the_real_join_keeps_the_upper_singer_on_the_upper_voice():
    """Issue #187's acceptance, on the two real parses either side of the join.

    Every bar of the upper staff that carries both lines has to put the higher
    one on voice 1 -- in the first system, where homr writes the lower one
    first in all three bars, and in the second, where it changes its mind after
    bar 1. Before this, the first system came out with the two singers swapped
    and the second swapped them back mid-phrase.
    """
    for path in A_JOIN:
        upper = omr_systems.flatten(path)[0]
        for number, measure in enumerate(upper.measures, start=1):
            heights = voices_by_height(measure)
            if len(heights) < 2:
                continue
            assert heights["1"] > heights["2"], f"{path} bar {number}"


def test_the_real_join_assembles_without_the_singers_changing_places(tmp_path):
    """The same two systems joined, which is where a swap is actually felt: one
    part of the assembled score has to be one singer the whole way through."""
    scans = [omr_systems.SystemScan(index=index, musicxml=path,
                                    staves=omr_systems.flatten(path))
             for index, path in enumerate(A_JOIN, start=1)]
    out = omr_systems.assemble(scans, str(tmp_path / "joined.musicxml"))
    part = read(out).findall("part")[0]
    both = [measure for measure in part.findall("measure")
            if len(voices_by_height(measure)) > 1]
    # All three bars of the first system and all five of the second.
    assert len(both) == 8
    for measure in both:
        heights = voices_by_height(measure)
        assert heights["1"] > heights["2"], measure.get("number")


# --- assembling ----------------------------------------------------------


def read(path):
    return etree.parse(path).getroot()


def test_one_part_per_staff_column(tmp_path):
    out = omr_systems.assemble([scan(1, 4), scan(2, 4)], str(tmp_path / "s.musicxml"))
    assert len(read(out).findall("part")) == 4


def test_the_widest_system_sets_the_part_count(tmp_path):
    out = omr_systems.assemble([scan(1, 2), scan(2, 3), scan(3, 2)],
                               str(tmp_path / "s.musicxml"))
    assert len(read(out).findall("part")) == 3


def test_a_column_a_system_does_not_have_is_measure_rests(tmp_path):
    """Not a claim about which voice is absent -- a refusal to make one. The
    per-system grid asks a person."""
    out = omr_systems.assemble([scan(1, 2, bars=2), scan(2, 3, bars=2)],
                               str(tmp_path / "s.musicxml"))
    third = read(out).findall("part")[2]
    first_system = third.findall("measure")[:2]
    for measure in first_system:
        assert measure.find("note/rest").get("measure") == "yes"


def test_bars_are_numbered_across_the_whole_score(tmp_path):
    """Every crop starts its own bar 1, and a score cannot."""
    out = omr_systems.assemble([scan(1, 2, bars=3), scan(2, 2, bars=4)],
                               str(tmp_path / "s.musicxml"))
    numbers = [m.get("number") for m in read(out).findall("part")[0].findall("measure")]
    assert numbers == ["1", "2", "3", "4", "5", "6", "7"]


def test_each_seam_is_a_system_break(tmp_path):
    out = omr_systems.assemble([scan(1, 2, bars=3), scan(2, 2, bars=4), scan(3, 2, bars=2)],
                               str(tmp_path / "s.musicxml"))
    for part in read(out).findall("part"):
        broken = [m.get("number") for m in part.findall("measure")
                  if m.find("print") is not None]
        assert broken == ["4", "8"]


def test_the_first_bar_is_not_a_break(tmp_path):
    out = omr_systems.assemble([scan(1, 2)], str(tmp_path / "s.musicxml"))
    assert read(out).find("part/measure/print") is None


def test_one_divisions_for_the_whole_score(tmp_path):
    """Each run picks its own, and a score has one."""
    out = omr_systems.assemble([scan(1, 2, divisions=2), scan(2, 2, divisions=3)],
                               str(tmp_path / "s.musicxml"))
    root = read(out)
    declared = {d.text for d in root.iter("divisions")}
    assert declared == {"6"}


def test_durations_are_rescaled_with_the_divisions(tmp_path):
    """Renaming the unit without restating the lengths would halve the music."""
    one = omr_systems.assemble([scan(1, 1, divisions=2)], str(tmp_path / "one.musicxml"))
    both = omr_systems.assemble([scan(1, 1, divisions=2), scan(2, 1, divisions=4)],
                                str(tmp_path / "both.musicxml"))
    alone = [d.text for d in read(one).find("part/measure").iter("duration")]
    joined = [d.text for d in read(both).find("part/measure").iter("duration")]
    assert alone == ["2", "2", "2", "2"]
    assert joined == ["4", "4", "4", "4"]


def test_a_repeated_key_is_not_declared_again(tmp_path):
    """Every crop re-declares its key, because every crop has just begun."""
    out = omr_systems.assemble([scan(1, 2, fifths=2), scan(2, 2, fifths=2)],
                               str(tmp_path / "s.musicxml"))
    part = read(out).findall("part")[0]
    assert len(part.findall("measure/attributes/key")) == 1


def test_a_key_that_really_changed_is_kept(tmp_path):
    out = omr_systems.assemble([scan(1, 2, fifths=2), scan(2, 2, fifths=-1)],
                               str(tmp_path / "s.musicxml"))
    part = read(out).findall("part")[0]
    assert [k.findtext("fifths") for k in part.findall("measure/attributes/key")] == ["2", "-1"]


def test_a_meter_change_inside_a_system_is_left_alone(tmp_path):
    """Only the seam is rewritten. B4's last system goes 3/4, 5/4, 4/4 inside
    one system, and correcting those away would be losing music to tidy a join.
    """
    part = etree.fromstring(
        '<part id="P1">'
        '<measure number="1"><attributes><divisions>1</divisions>'
        "<time><beats>3</beats><beat-type>4</beat-type></time>"
        "<clef><sign>G</sign><line>2</line></clef></attributes>"
        + a_note("C", duration=1) + "</measure>"
        '<measure number="2"><attributes><divisions>1</divisions>'
        "<time><beats>5</beats><beat-type>4</beat-type></time></attributes>"
        + a_note("D", duration=1) + "</measure></part>")
    only = omr_systems.SystemScan(index=1, musicxml="",
                                  staves=omr_systems.flatten_part(part))
    out = omr_systems.assemble([only], str(tmp_path / "s.musicxml"))
    bars = read(out).find("part").findall("measure")
    assert bars[1].findtext("attributes/time/beats") == "5"
    # ...but the one divisions the score has is not restated bar by bar.
    assert bars[1].find("attributes/divisions") is None


def test_a_resting_column_inherits_the_system_meter(tmp_path):
    """Its bars have to be as long as everybody else's, or the rest overruns."""
    out = omr_systems.assemble([scan(1, 3, bars=1, divisions=4, time=(3, 4)),
                                scan(2, 2, bars=1, divisions=4, time=(3, 4))],
                               str(tmp_path / "s.musicxml"))
    third = read(out).findall("part")[2]
    resting = third.findall("measure")[1]
    assert resting.findtext("note/duration") == "12"      # 3/4 at 4 per quarter


# --- the meter -----------------------------------------------------------
#
# homr has no token for a numerator: it reads the denominator and infers the
# number of beats from how long its own decoded bars came out, one crop at a
# time. These are the cases where that inference is wrong and the assembly can
# see why -- and, just as much, the cases where it must keep its hands off.


def a_staff(lengths, divisions=4, time=(4, 4), changes=None):
    """One ``<part>`` of one staff: bar N holds ``lengths[N]`` quarter notes.

    ``None`` in place of a length is a whole-measure rest. ``time`` is what the
    crop declares at its head -- every crop declares one, because every crop is a
    document that has just begun -- and ``changes`` (``{bar: (beats, type)}``) is
    a signature it declares later, which is the shape of one the page prints.
    """
    changes = changes or {}
    body = []
    for n, quarters in enumerate(lengths):
        declared = ""
        if n == 0:
            declared = (f"<attributes><divisions>{divisions}</divisions>"
                        "<key><fifths>0</fifths></key>"
                        f"<time><beats>{time[0]}</beats>"
                        f"<beat-type>{time[1]}</beat-type></time>"
                        "<clef><sign>G</sign><line>2</line></clef></attributes>")
        elif n in changes:
            declared = (f"<attributes><time><beats>{changes[n][0]}</beats>"
                        f"<beat-type>{changes[n][1]}</beat-type></time></attributes>")
        if quarters is None:
            notes = ('<note><rest measure="yes"/>'
                     f"<duration>{divisions * 4}</duration><voice>1</voice></note>")
        else:
            notes = "".join(a_note("C", duration=divisions) for _ in range(quarters))
        body.append(f'<measure number="{n + 1}">{declared}{notes}</measure>')
    return f'<part id="P1">{"".join(body)}</part>'


def a_scan(index, staves):
    """A system already flattened, out of ``a_staff`` documents."""
    made = []
    for xml in staves:
        made.extend(omr_systems.flatten_part(etree.fromstring(xml)))
    return omr_systems.SystemScan(index=index, musicxml="", staves=made)


def declared_meters(part):
    """Every signature the part writes, as ``(bar number, "beats/type")``."""
    return [(measure.get("number"),
             f'{measure.findtext("attributes/time/beats")}/'
             f'{measure.findtext("attributes/time/beat-type")}')
            for measure in part.findall("measure")
            if measure.find("attributes/time") is not None]


def assembled(tmp_path, *scans):
    return read(omr_systems.assemble(list(scans), str(tmp_path / "s.musicxml")))


def test_a_seam_carries_the_meter_rather_than_the_crops_own_guess(tmp_path):
    """The second crop begins mid-piece, so nothing in it prints a signature and
    homr's inferred 3/4 is a claim about bars it could not see the start of."""
    score = assembled(
        tmp_path,
        a_scan(1, [a_staff([4, 4]), a_staff([4, 4])]),
        a_scan(2, [a_staff([4, 4], time=(3, 4)), a_staff([4, 4], time=(3, 4))]),
    )
    assert declared_meters(score.findall("part")[0]) == [("1", "4/4")]


def test_a_printed_signature_gets_the_numerator_its_bars_have(tmp_path):
    """Virta venhettä vie's m13 prints 4/4 and came back 3/4. The change is real
    -- it is only the number that homr had no way to read."""
    staves = [a_staff([3, 3, 4, 4], time=(3, 4), changes={2: (3, 4)})] * 2
    score = assembled(tmp_path, a_scan(1, staves))
    assert declared_meters(score.findall("part")[0]) == [("1", "3/4"), ("3", "4/4")]


def test_a_meter_the_page_really_changes_at_a_seam_is_written(tmp_path):
    score = assembled(
        tmp_path,
        a_scan(1, [a_staff([4, 4]), a_staff([4, 4])]),
        a_scan(2, [a_staff([3, 3], time=(4, 4)), a_staff([3, 3], time=(4, 4))]),
    )
    assert declared_meters(score.findall("part")[0]) == [("1", "4/4"), ("3", "3/4")]


def test_one_short_voice_does_not_move_the_meter(tmp_path):
    """Virta's lower divisi voice is a quarter short in these bars. That is a
    note error, not a meter, and it has to stay one: a signature bent to fit it
    would make the bar consistent and the health check silent."""
    score = assembled(tmp_path, a_scan(1, [a_staff([4, 4]), a_staff([4, 3])]))
    parts = score.findall("part")
    assert declared_meters(parts[0]) == [("1", "4/4")]
    short = parts[1].findall("measure")[1]
    assert sum(int(d.text) for d in short.findall("note/duration")) == 12


def test_a_bar_that_disagrees_on_its_own_is_left_disagreeing(tmp_path):
    """The 9/8 bar the Soundslice score has too. Giving it a signature of its own
    would paper over the reading; it gets none, and still overruns the meter."""
    score = assembled(tmp_path, a_scan(1, [a_staff([4, 6, 4, 4])] * 2))
    part = score.findall("part")[0]
    assert declared_meters(part) == [("1", "4/4")]
    odd = part.findall("measure")[1]
    assert sum(int(d.text) for d in odd.findall("note/duration")) == 24


def test_a_single_bar_is_not_enough_to_overrule_a_signature(tmp_path):
    """One staff of one bar agreeing with itself is not evidence. B4's last
    system goes 3/4, 5/4, 4/4 and every one of those numbers has to survive."""
    score = assembled(tmp_path, a_scan(1, [a_staff([3], time=(4, 4))]))
    assert declared_meters(score.findall("part")[0]) == [("1", "4/4")]


def test_two_staves_of_one_bar_are_still_one_bar(tmp_path):
    """The threshold counts bars, not staff copies of a bar. Both staves reading
    three quarters where the signature says four is one reading of one bar -- and
    a duration misread the same way on both staves looks exactly like this."""
    score = assembled(tmp_path, a_scan(1, [a_staff([3], time=(4, 4)),
                                           a_staff([3], time=(4, 4))]))
    assert declared_meters(score.findall("part")[0]) == [("1", "4/4")]


def test_a_signature_that_only_restates_the_meter_is_not_a_reading(tmp_path):
    """homr writes one at the head of every crop and again wherever its decoding
    wobbled, so "the same as before" says nothing the page said. One bar of music
    both staves agree on may overrule that -- this is Virta's m13, where the page
    prints 4/4 and the crop restated the 2/4 already in force."""
    staves = [a_staff([2, 2, 4], time=(2, 4), changes={2: (2, 4)})] * 2
    score = assembled(tmp_path, a_scan(1, staves))
    assert declared_meters(score.findall("part")[0]) == [("1", "2/4"), ("3", "4/4")]


def test_a_one_bar_change_the_page_prints_is_kept(tmp_path):
    """The same bar, but the crop declares a *change* there. That is a reading of
    the page, and one bar of music is not enough to argue with it."""
    staves = [a_staff([2, 2, 4], time=(2, 4), changes={2: (5, 4)})] * 2
    score = assembled(tmp_path, a_scan(1, staves))
    assert declared_meters(score.findall("part")[0]) == [("1", "2/4"), ("3", "5/4")]


def test_a_bar_whose_staves_disagree_is_not_an_observation(tmp_path):
    """Two staves, two answers, and nothing to choose between them: that bar says
    nothing about the meter, so a span made of such bars keeps what it was given.
    """
    score = assembled(tmp_path, a_scan(1, [a_staff([3, 3], time=(4, 4)),
                                           a_staff([2, 2], time=(4, 4))]))
    assert declared_meters(score.findall("part")[0]) == [("1", "4/4")]


def test_a_whole_measure_rest_is_not_evidence_of_the_bar_length(tmp_path):
    """homr writes one a whole note long whatever the meter, so counting it
    would drag every span with a resting staff in it towards 4/4."""
    score = assembled(tmp_path, a_scan(1, [a_staff([3, 3], time=(3, 4)),
                                           a_staff([None, None], time=(3, 4))]))
    assert declared_meters(score.findall("part")[0]) == [("1", "3/4")]


def test_a_length_no_numerator_fits_keeps_the_signature(tmp_path):
    """Three and a half quarters is a bar that lost something, not a meter."""
    part = etree.fromstring(
        '<part id="P1">'
        '<measure number="1"><attributes><divisions>4</divisions>'
        "<key><fifths>0</fifths></key>"
        "<time><beats>4</beats><beat-type>4</beat-type></time>"
        "<clef><sign>G</sign><line>2</line></clef></attributes>"
        + a_note("C", duration=4) * 3 + a_note("D", duration=2) + "</measure>"
        '<measure number="2">'
        + a_note("C", duration=4) * 3 + a_note("D", duration=2) + "</measure></part>")
    scanned = omr_systems.SystemScan(index=1, musicxml="",
                                     staves=omr_systems.flatten_part(part))
    score = assembled(tmp_path, scanned)
    assert declared_meters(score.findall("part")[0]) == [("1", "4/4")]


def test_a_resting_column_gets_the_corrected_bar_length(tmp_path):
    """The rest is written to the meter the bars have, not the one the crop
    guessed -- a measure rest the wrong length is silence that overruns the bar,
    which nothing downstream checks and the scrolling render refuses."""
    score = assembled(
        tmp_path,
        a_scan(1, [a_staff([3, 3], time=(3, 4)), a_staff([3, 3], time=(3, 4))]),
        a_scan(2, [a_staff([3, 3], time=(4, 4))]),
    )
    resting = score.findall("part")[1].findall("measure")[2]
    assert resting.findtext("note/duration") == "12"


def test_every_part_gets_a_clef_and_a_key_to_start_with(tmp_path):
    """A column that is silent in the first system still has to open somehow."""
    out = omr_systems.assemble([scan(1, 1, bars=1), scan(2, 2, bars=1)],
                               str(tmp_path / "s.musicxml"))
    opening = read(out).findall("part")[1].find("measure/attributes")
    assert opening.find("divisions") is not None
    assert opening.find("clef") is not None
    assert opening.find("key") is not None
    assert opening.find("time") is not None


def test_nothing_to_assemble_says_so(tmp_path):
    with pytest.raises(omr_systems.ScanError):
        omr_systems.assemble([], str(tmp_path / "s.musicxml"))


# --- reading -------------------------------------------------------------


def test_no_bounds_names_where_bounds_come_from(tmp_path):
    """Bounds are a precondition, and the error has to say so: there is nothing
    a caller can do about "no systems" without being told where they live."""
    with pytest.raises(omr_systems.ScanError) as caught:
        omr_systems.read_systems("score.pdf", [], str(tmp_path))
    assert ".systems.json" in str(caught.value)


def test_a_system_is_read_under_its_own_slot(tmp_path, monkeypatch):
    """One lease per system, not one per song: a shorter hold lets a render or
    a suite in between bands, and an interruption costs one band."""
    asked = []

    def fake_read_page(image_path, out_dir=None, log=None, label=None, queue=True, **kw):
        asked.append((label, queue))
        produced = os.path.join(out_dir, os.path.basename(image_path) + ".musicxml")
        with open(produced, "w") as f:
            f.write('<score-partwise><part-list><score-part id="P1"/></part-list>'
                    '<part id="P1"><measure number="1"/></part></score-partwise>')
        return produced

    monkeypatch.setattr(omr_systems.omr, "read_page", fake_read_page)
    monkeypatch.setattr(omr_systems, "crop_systems", lambda *a, **k: [
        SystemImage(bounds=SystemBounds(index=n, page=1, top=0.0, bottom=0.5),
                    path=str(tmp_path / f"band-{n}.png"))
        for n in (1, 2, 3)])

    scans = omr_systems.read_systems("score.pdf", [1, 2, 3], str(tmp_path))
    assert len(scans) == 3
    assert [q for _label, q in asked] == [True, True, True]
    # The label has to name the band, or the queue shows three of the same job.
    assert len({label for label, _ in asked}) == 3


def test_a_caller_holding_a_lease_does_not_ask_for_another(tmp_path, monkeypatch):
    """Nesting would deadlock a one-slot pool."""
    asked = []

    def fake_read_page(image_path, out_dir=None, log=None, label=None, queue=True, **kw):
        asked.append(queue)
        produced = os.path.join(out_dir, "one.musicxml")
        with open(produced, "w") as f:
            f.write('<score-partwise><part-list/><part id="P1">'
                    '<measure number="1"/></part></score-partwise>')
        return produced

    monkeypatch.setattr(omr_systems.omr, "read_page", fake_read_page)
    monkeypatch.setattr(omr_systems, "crop_systems", lambda *a, **k: [
        SystemImage(bounds=SystemBounds(index=1, page=1, top=0.0, bottom=0.5),
                    path=str(tmp_path / "band-1.png"))])

    omr_systems.read_systems("score.pdf", [1], str(tmp_path), queue=False)
    assert asked == [False]


# --- what the whole thing is for -----------------------------------------


def _musescore():
    import dotenv
    dotenv.load_dotenv(".env")
    return os.getenv("MUSESCORE_CLI_PATH")


@pytest.mark.skipif(not _musescore() or not os.path.exists(_musescore() or ""),
                    reason="needs the MuseScore CLI")
def test_the_per_system_grid_sees_the_systems_the_page_has(tmp_path):
    """The acceptance, minus homr: systems of 2, 3 and 2 staves survive
    assembly, conversion and ``clean_score``'s per-system reading.

    This is the shape B4 comes out in (2-3-2-3-3) and the reason the whole
    route exists: a page whose staves change role per system is not a
    rectangle, and the grid is what asks a person which staff is which part.
    """
    assembled = omr_systems.assemble(
        [scan(1, 2, bars=2), scan(2, 3, bars=2), scan(3, 2, bars=2)],
        str(tmp_path / "assembled.musicxml"))
    mscx = str(tmp_path / "assembled.mscx")
    subprocess.run([_musescore(), "-o", mscx, assembled],
                   check=True, capture_output=True, timeout=300)

    root = etree.parse(mscx).getroot()
    layout = per_system.system_layout(root)
    assert [(s.start, s.end) for s in layout] == [(1, 2), (3, 4), (5, 6)]
    assert [[row.staff_id for row in s.staves] for s in layout] == [
        [1, 2], [1, 2, 3], [1, 2]]
