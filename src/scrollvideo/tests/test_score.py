"""Leaving out staves that carry no music."""

import pytest
from lxml import etree

from src.scrollvideo.score import (START, add_opening_tempo, add_tempo_changes, drop_parts,
                                   has_opening_tempo, hold_fermatas, prepare,
                                   silent_parts)

SCORE = """<museScore><Score>
  <Part><trackName>T1</trackName><Staff id="1"/></Part>
  <Part><trackName>Drumset</trackName><Staff id="2"/>
    <Instrument id="drumset"><useDrumset>1</useDrumset></Instrument></Part>
  <Part><trackName>Click</trackName><Staff id="3"/></Part>
  <Staff id="1"><Measure><voice><Chord><Note><pitch>60</pitch></Note></Chord></voice></Measure></Staff>
  <Staff id="2"><Measure><voice><Rest/></voice></Measure></Staff>
  <Staff id="3"><Measure><voice><Rest/></voice></Measure></Staff>
</Score></museScore>"""

SINGING_ONLY = """<museScore><Score>
  <Part><trackName>T1</trackName><Staff id="1"/></Part>
  <Staff id="1"><Measure><voice><Chord><Note><pitch>60</pitch></Note></Chord></voice></Measure></Staff>
</Score></museScore>"""


def test_percussion_and_rest_only_staves_are_silent():
    assert silent_parts(etree.fromstring(SCORE)) == ["Drumset", "Click"]


def test_a_singing_part_is_never_silent():
    assert silent_parts(etree.fromstring(SINGING_ONLY)) == []


def test_dropping_a_part_takes_its_staff_with_it():
    root = etree.fromstring(SCORE)
    assert drop_parts(root, ["Drumset", "Click"]) == 2
    assert [p.findtext("trackName") for p in root.iter("Part")] == ["T1"]
    assert [s.get("id") for s in root.find("Score").findall("Staff")] == ["1"]


def test_prepare_leaves_the_original_file_alone(tmp_path):
    original = tmp_path / "score.mscx"
    original.write_text(SCORE)
    before = original.read_text()

    path, dropped = prepare(str(original), str(tmp_path))
    assert dropped == ["Drumset", "Click"]
    assert path != str(original)
    assert original.read_text() == before


def test_prepare_uses_the_score_as_is_when_there_is_nothing_to_drop(tmp_path):
    original = tmp_path / "score.mscx"
    original.write_text(SINGING_ONLY)
    path, dropped = prepare(str(original), str(tmp_path))
    assert (path, dropped) == (str(original), [])


def test_keep_silent_skips_the_whole_thing(tmp_path):
    original = tmp_path / "score.mscx"
    original.write_text(SCORE)
    assert prepare(str(original), str(tmp_path), keep_silent=True) == (str(original), [])


def test_an_opening_tempo_must_precede_the_first_musical_event():
    root = etree.fromstring(
        b"<museScore><Score><Staff><Measure><voice>"
        b"<Tempo><tempo>1.5</tempo></Tempo><Chord/>"
        b"</voice></Measure></Staff></Score></museScore>")
    assert has_opening_tempo(root)

    root.find(".//voice").insert(0, etree.Element("Chord"))
    assert not has_opening_tempo(root)


def test_adding_an_opening_tempo_preserves_later_changes():
    root = etree.fromstring(
        b"<museScore><Score><Staff><Measure><voice>"
        b"<TimeSig/><Chord/><Tempo><tempo>1</tempo></Tempo>"
        b"</voice></Measure></Staff></Score></museScore>")

    assert add_opening_tempo(root, 80)
    tempos = root.findall(".//Tempo")
    assert [t.findtext("tempo") for t in tempos] == ["1.33333333333", "1"]
    assert tempos[0].findtext("visible") == "0"
    assert "80" in "".join(tempos[0].itertext())


def test_prepare_applies_tempo_only_to_its_temporary_copy(tmp_path):
    original = tmp_path / "score.mscx"
    original.write_text(SINGING_ONLY)
    before = original.read_text()

    path, dropped = prepare(str(original), str(tmp_path), initial_bpm=80)

    assert dropped == []
    assert path != str(original)
    assert has_opening_tempo(etree.parse(path).getroot())
    assert original.read_text() == before


def test_prepare_does_not_override_an_existing_opening_tempo(tmp_path):
    original = tmp_path / "score.mscx"
    original.write_text(
        "<museScore><Score><Staff><Measure><voice>"
        "<Tempo><tempo>1.5</tempo></Tempo><Chord/>"
        "</voice></Measure></Staff></Score></museScore>")

    path, dropped = prepare(str(original), str(tmp_path), initial_bpm=80)

    assert (path, dropped) == (str(original), [])
    assert etree.parse(path).findtext(".//Tempo/tempo") == "1.5"


# A fermata holds one beat longer than written (#380) -----------------------

def _bar(sig, *voices, staves=1):
    """One bar per staff; each voice a list of ("half", fermata?) or ("half.", ...)."""
    def chord(kind, held):
        dots = "<dots>1</dots>" if kind.endswith(".") else ""
        fermata = ("<Fermata><subtype>fermataAbove</subtype>"
                   "<timeStretch>3</timeStretch></Fermata>") if held else ""
        return (f"{fermata}<Chord>{dots}<durationType>{kind.rstrip('.')}</durationType>"
                "<Note><pitch>60</pitch></Note></Chord>")
    n, d = sig
    staff_xml = []
    for staff, voice in enumerate(voices, 1):
        tsig = f"<TimeSig><sigN>{n}</sigN><sigD>{d}</sigD></TimeSig>"
        body = "".join(chord(kind, held) for kind, held in voice)
        staff_xml.append(f'<Staff id="{staff}"><Measure><voice>{tsig}{body}'
                         "</voice></Measure></Staff>")
    return etree.fromstring("<museScore><Score>" + "".join(staff_xml)
                            + "</Score></museScore>")


def _stretches(root):
    return [float(t.text) for t in root.iter("timeStretch")]


def test_a_fermata_adds_one_beat_whatever_the_note():
    root = _bar((4, 4), [("half", True), ("quarter", True), ("quarter", False)])
    assert hold_fermatas(root) == 2
    # A half is two beats, held three; a quarter one, held two.
    assert _stretches(root) == [1.5, 2.0]

    root = _bar((4, 4), [("half.", True), ("quarter", False)])
    hold_fermatas(root)
    assert _stretches(root) == [pytest.approx(4 / 3, abs=1e-5)]


def test_the_beat_is_the_one_moving_on_any_staff():
    """MuseScore stretches until the next note on any staff starts, so a held half
    over two moving quarters adds its beat to the first quarter's span."""
    root = _bar((4, 4), [("half", True), ("half", False)],
                [("quarter", False), ("quarter", False), ("half", False)])
    hold_fermatas(root)
    assert _stretches(root) == [2.0]


def test_a_compound_meter_counts_in_dotted_quarters():
    root = _bar((6, 8), [("quarter.", True), ("quarter.", False)])
    hold_fermatas(root)
    assert _stretches(root) == [2.0]


def test_a_fermata_marked_not_to_play_is_left_alone():
    root = _bar((4, 4), [("whole", True)])
    etree.SubElement(root.find(".//Fermata"), "play").text = "0"
    assert hold_fermatas(root) == 0
    assert _stretches(root) == [3.0]


def test_prepare_holds_fermatas_only_in_its_copy(tmp_path):
    original = tmp_path / "score.mscx"
    original.write_bytes(etree.tostring(_bar((4, 4), [("whole", True)])))
    before = original.read_bytes()
    path, _ = prepare(str(original), str(tmp_path))
    assert path != str(original)
    assert _stretches(etree.parse(path).getroot()) == [1.25]
    assert original.read_bytes() == before


# Tempo changes a person records (#387) -------------------------------------

def _bars(count, staves=2, opening=None):
    tempo = (f"<Tempo><tempo>{opening}</tempo></Tempo>" if opening else "")
    def staff(i):
        bars = "".join(
            f"<Measure><voice>{tempo if (n == 0 and i == 1) else ''}"
            "<Chord><durationType>whole</durationType></Chord></voice></Measure>"
            for n in range(count))
        return f'<Staff id="{i}">{bars}</Staff>'
    return etree.fromstring(
        f"<museScore><Score>{''.join(staff(i) for i in range(1, staves + 1))}"
        "</Score></museScore>")


def _tempos(root):
    """(bar, bpm) of every tempo mark, top staff first."""
    out = []
    for staff in root.findall("./Score/Staff"):
        for n, measure in enumerate(staff.findall("Measure"), 1):
            for tempo in measure.iter("Tempo"):
                out.append((n, round(float(tempo.findtext("tempo")) * 60, 3)))
    return out


def test_a_tempo_change_lands_at_the_start_of_its_bar_on_the_top_staff():
    root = _bars(4)
    assert add_tempo_changes(root, [{"measure": 3, "bpm": 112}]) == 1

    assert _tempos(root) == [(3, 112.0)]
    voice = root.find("./Score/Staff/Measure[3]/voice")
    assert voice[0].tag == "Tempo" and voice[1].tag == "Chord"
    assert voice[0].findtext("visible") == "0"


def test_back_to_start_plays_at_the_opening_tempo():
    root = _bars(5)
    add_opening_tempo(root, 80)
    add_tempo_changes(root, [{"measure": 2, "bpm": 120}, {"measure": 4, "bpm": START}])

    assert _tempos(root) == [(1, 80.0), (2, 120.0), (4, 80.0)]


def test_back_to_start_follows_a_score_that_carries_its_own_opening():
    root = _bars(3, opening="1.5")
    add_tempo_changes(root, [{"measure": 3, "bpm": START}])
    assert _tempos(root) == [(1, 90.0), (3, 90.0)]


def test_a_mark_already_at_that_bar_is_replaced_on_every_staff():
    root = _bars(3)
    lower = root.find("./Score/Staff[2]/Measure[2]/voice")
    lower.insert(0, etree.fromstring("<Tempo><tempo>2</tempo></Tempo>"))

    add_tempo_changes(root, [{"measure": 2, "bpm": 60}])
    assert _tempos(root) == [(2, 60.0)]


@pytest.mark.parametrize("change, message", [
    ({"measure": 1, "bpm": 100}, "bar 1"),
    ({"measure": 9, "bpm": 100}, "bars 1-3"),
    ({"measure": 2, "bpm": START}, "has none"),
])
def test_a_change_the_score_cannot_take_is_refused(change, message):
    with pytest.raises(ValueError, match=message):
        add_tempo_changes(_bars(3), [change])


def test_prepare_puts_the_changes_only_in_its_copy(tmp_path):
    original = tmp_path / "score.mscx"
    original.write_bytes(etree.tostring(_bars(3)))
    before = original.read_text()

    path, _ = prepare(str(original), str(tmp_path), initial_bpm=80,
                      tempo_changes=[{"measure": 2, "bpm": 132},
                                     {"measure": 3, "bpm": START}])

    assert _tempos(etree.parse(path).getroot()) == [(1, 80.0), (2, 132.0), (3, 80.0)]
    assert original.read_text() == before
