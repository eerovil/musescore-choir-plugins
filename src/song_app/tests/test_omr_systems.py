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
    """One ``<part>`` as homr writes it: ``staves`` staff rows inside it."""
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
                content += f"<backup><duration>{divisions * 4}</duration></backup>"
            content += "".join(
                a_note("CDEFG"[(staff + n) % 5], duration=divisions,
                       staff=staff if staves > 1 else None, voice=str(staff))
                for n in range(4))
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


def test_two_voices_on_one_staff_keep_their_backup():
    """Dropping the backups is only safe because they are rebuilt: two voices
    sharing a staff must still start at the same point in the bar."""
    part = etree.fromstring(
        '<part id="P1"><measure number="1">'
        "<attributes><divisions>1</divisions></attributes>"
        + a_note("C", duration=1, voice="1") + a_note("D", duration=1, voice="1")
        + "<backup><duration>2</duration></backup>"
        + a_note("E", duration=1, voice="2") + a_note("F", duration=1, voice="2")
        + "</measure></part>")
    staff = omr_systems.flatten_part(part)[0]
    backups = staff.measures[0].findall("backup")
    assert [b.findtext("duration") for b in backups] == ["2"]


def test_three_voices_wind_back_only_one_voice_at_a_time():
    """A running total would put the third voice before the start of the bar."""
    notes = "".join(a_note("C", duration=1, voice=str(v)) for v in (1, 2, 3))
    part = etree.fromstring(
        '<part id="P1"><measure number="1">'
        "<attributes><divisions>1</divisions></attributes>" + notes + "</measure></part>")
    staff = omr_systems.flatten_part(part)[0]
    assert [b.findtext("duration")
            for b in staff.measures[0].findall("backup")] == ["1", "1"]


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
