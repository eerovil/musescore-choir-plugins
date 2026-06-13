"""
Per-system re-voicing tests, driven by the real (badly-parsed) Laulun aika score.

The physical staves change role per system; these tests pin the rebuild that turns
that into one clean staff per part (T1, T2, T3, B).
"""

import os

from lxml import etree

from src.clean_score.utils import per_system
from src.clean_score.utils.per_system import (
    build_parts,
    build_system_lyric_map,
    decls_from_answers,
    find_systems,
    load_answer_cache,
    prompt_system_decls,
    save_answer_cache,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "test_files", "laulun_aika.mscx")


def _load():
    return etree.parse(FIXTURE).getroot()


# The user's reading of the score, per system (staff_id, voice_index) -> part.
# Systems: 0=m1-6, 1=m7-11, 2=m12-15, 3=m16-19, 4=m20-25, 5=m26-29, 6=m30-35.
DECLS = {
    0: {(1, 0): "T1", (1, 1): "T2", (2, 0): "B"},
    1: {(1, 0): "T1", (1, 1): "T2", (2, 0): "B"},
    2: {(1, 0): "T1", (1, 1): "T2", (2, 0): "B"},
    3: {(1, 0): "T1", (1, 1): "T2", (2, 0): "B"},
    4: {(1, 0): "T3", (2, 0): "B", (3, 0): "T1", (3, 1): "T2"},
    5: {(1, 0): "T1", (2, 0): "B", (3, 0): "T2"},
    6: {(1, 0): "T3", (2, 0): "T1", (3, 0): "T2", (4, 0): "B"},
}


def _pitches(staff, measure_index):
    m = staff.findall("Measure")[measure_index]
    return [
        n.findtext("pitch")
        for v in m.findall("voice")
        for ch in v.findall("Chord")
        for n in ch.findall("Note")
    ]


def test_find_systems():
    root = _load()
    systems = find_systems(root)
    assert systems == [(0, 5), (6, 10), (11, 14), (15, 18), (19, 24), (25, 28), (29, 34)]


def test_build_parts_order_and_count():
    root = _load()
    systems = find_systems(root)
    parts = build_parts(root, systems, DECLS)
    assert parts == ["T1", "T2", "T3", "B"]
    staves = root.findall(".//Score/Staff")
    assert len(staves) == 4
    # Track names reflect the parts.
    assert [p.find("trackName").text for p in root.findall(".//Part")] == ["T1", "T2", "T3", "B"]


def test_t3_staff_silent_until_it_enters():
    root = _load()
    src = _load()  # untouched copy for source-content comparison
    systems = find_systems(root)
    build_parts(root, systems, DECLS)
    staves = {p.find("trackName").text: s
              for p, s in zip(root.findall(".//Part"), root.findall(".//Score/Staff"))}
    t3 = staves["T3"]
    # System 0 (m1) has no T3 -> measure rest, no notes.
    assert _pitches(t3, 0) == []
    # System 4 (m20) declares T3 = source staff 1 voice 0; content must match the source.
    src_staff1 = src.findall(".//Score/Staff")[0]
    assert _pitches(t3, 19) == _pitches(src_staff1, 19)
    assert _pitches(t3, 19)  # non-empty


def test_parts_pull_from_the_right_staff_per_system():
    root = _load()
    src = _load()
    systems = find_systems(root)
    build_parts(root, systems, DECLS)
    staves = {p.find("trackName").text: s
              for p, s in zip(root.findall(".//Part"), root.findall(".//Score/Staff"))}
    src_s = src.findall(".//Score/Staff")
    # m26 (system 5): T2 comes from source staff 3 voice 0.
    assert _pitches(staves["T2"], 25) == _pitches(src_s[2], 25)
    # m26: T1 comes from source staff 1 voice 0.
    assert _pitches(staves["T1"], 25) == _pitches(src_s[0], 25)
    # m30 (system 6): B comes from source staff 4 voice 0.
    assert _pitches(staves["B"], 29) == _pitches(src_s[3], 29)


def test_tuplet_survives_rebuild():
    """Measure 14 has a triplet (Tuplet/endTuplet) — it must survive into the rebuilt part."""
    root = _load()
    systems = find_systems(root)
    build_parts(root, systems, DECLS)
    staves = {p.find("trackName").text: s
              for p, s in zip(root.findall(".//Part"), root.findall(".//Score/Staff"))}
    # m14 (index 13) T1 comes from source staff 1 voice 0, which has the triplet.
    m14 = staves["T1"].findall("Measure")[13]
    voice = m14.find("voice")
    assert voice.find("Tuplet") is not None, "Tuplet start lost in rebuild"
    assert voice.find("endTuplet") is not None, "endTuplet lost in rebuild"
    # The three triplet chords sit between Tuplet and endTuplet.
    tags = [c.tag for c in voice]
    assert tags.index("Tuplet") < tags.index("endTuplet")
    assert tags[tags.index("Tuplet"):tags.index("endTuplet")].count("Chord") == 3


def _line_break_measures(staff):
    return [
        i for i, m in enumerate(staff.findall("Measure"))
        if any((lb.findtext("subtype") or "").strip() == "line"
               for lb in m.findall("LayoutBreak"))
    ]


def test_line_breaks_re_added_on_top_staff():
    root = _load()
    systems = find_systems(root)
    build_parts(root, systems, DECLS)
    staves = root.findall(".//Score/Staff")
    # Top staff carries the system breaks (end of each system except the last).
    assert _line_break_measures(staves[0]) == [5, 10, 14, 18, 24, 28]
    # Lower staves carry none.
    assert _line_break_measures(staves[1]) == []


def _two_system_score():
    """One staff, two measures, a line break after measure 1 -> two systems."""
    root = etree.Element("museScore")
    score = etree.SubElement(root, "Score")
    part = etree.SubElement(score, "Part")
    etree.SubElement(part, "trackName").text = "Track 1"
    etree.SubElement(part, "Staff", id="1")
    instr = etree.SubElement(part, "Instrument")
    for tag in ("longName", "shortName", "trackName"):
        etree.SubElement(instr, tag).text = "Track 1"
    staff = etree.SubElement(score, "Staff", id="1")
    for pitch, brk in ((60, True), (62, False)):
        m = etree.SubElement(staff, "Measure")
        v = etree.SubElement(m, "voice")
        ch = etree.SubElement(v, "Chord")
        etree.SubElement(ch, "durationType").text = "whole"
        etree.SubElement(etree.SubElement(ch, "Note"), "pitch").text = str(pitch)
        if brk:
            lb = etree.SubElement(m, "LayoutBreak")
            etree.SubElement(lb, "subtype").text = "line"
    return root


def test_prompt_reuses_last_input(monkeypatch):
    root = _two_system_score()
    systems = find_systems(root)
    assert systems == [(0, 0), (1, 1)]
    # System 1: type "T1"; System 2: press Enter to reuse.
    answers = iter(["T1", ""])
    monkeypatch.setattr(per_system, "input", lambda *a: next(answers), raising=False)
    decls, raw = prompt_system_decls(root, systems)
    assert decls[0] == {(1, 0): "T1"}
    assert decls[1] == {(1, 0): "T1"}  # reused
    assert raw == {0: {1: "T1"}, 1: {1: "T1"}}  # saved answers (Enter resolved to "T1")


def test_prompt_dash_clears(monkeypatch):
    root = _two_system_score()
    systems = find_systems(root)
    answers = iter(["T1", "-"])  # second system explicitly skipped
    monkeypatch.setattr(per_system, "input", lambda *a: next(answers), raising=False)
    decls, raw = prompt_system_decls(root, systems)
    assert decls.get(0) == {(1, 0): "T1"}
    assert 1 not in decls  # nothing declared for system 2
    assert raw == {0: {1: "T1"}, 1: {1: ""}}  # dash recorded as empty


def test_decls_from_answers_matches_full_decls():
    """Cached answers should rebuild exactly the same decls the prompt produced."""
    root = _load()
    systems = find_systems(root)
    answers = {
        0: {1: "T1,T2", 2: "B"},
        1: {1: "T1,T2", 2: "B"},
        2: {1: "T1,T2", 2: "B"},
        3: {1: "T1,T2", 2: "B"},
        4: {1: "T3", 2: "B", 3: "T1,T2"},
        5: {1: "T1", 2: "B", 3: "T2"},
        6: {1: "T3", 2: "T1", 3: "T2", 4: "B"},
    }
    assert decls_from_answers(root, systems, answers) == DECLS


def test_system_lyric_map_handles_omitted_and_reordered_staves():
    """
    Printed staff numbering shifts per system as parts are omitted; the map must order
    by musical rank (not OCR source order) and merge divisi onto one printed staff.
    """
    root = _load()
    systems = find_systems(root)
    parts = build_parts(root, systems, DECLS)  # ["T1","T2","T3","B"] -> ids 1,2,3,4
    smap = build_system_lyric_map(systems, DECLS, parts)
    by_range = {(e["start"], e["end"]): e["map"] for e in smap}
    # System 1 (m1-6): T1+T2 share source staff 1 (divisi) -> one printed staff; B -> staff 2.
    assert by_range[(1, 6)] == {1: [1, 2], 2: [4]}
    # System 5 (m20-25): T1,T2 divisi printed first, then T3, then B (rank order, not source order).
    assert by_range[(20, 25)] == {1: [1, 2], 2: [3], 3: [4]}
    # System 6 (m26-29): T3 omitted -> printed 3 is the BASS (output staff 4), not T3.
    assert by_range[(26, 29)] == {1: [1], 2: [2], 3: [4]}
    # System 7 (m30-35): all four present, one each.
    assert by_range[(30, 35)] == {1: [1], 2: [2], 3: [3], 4: [4]}


def test_cache_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(per_system, "_CACHE_PATH", str(tmp_path / "cache.json"))
    answers = {0: {1: "T1,T2", 2: "B"}, 1: {1: "T3"}}
    save_answer_cache("song-x", answers)
    save_answer_cache("song-y", {0: {1: "S"}})  # second key coexists
    assert load_answer_cache("song-x") == answers
    assert load_answer_cache("song-y") == {0: {1: "S"}}
    assert load_answer_cache("missing") is None
