"""
Per-system assignment tests, driven by the real (badly-parsed) Laulun aika score.

The physical staves change role per system; these tests pin the behavior that turns
that into one clean staff per part (T1, T2, T3, B), driving the module the way its
two adapters do: with an `Answers` mapping ({system: {staff_id: "T1,T2"}}).
"""

import json
import os

import pytest
from lxml import etree

from src.clean_score.utils.per_system import (
    clean_per_system,
    layout_for_file,
    save_answers,
    saved_answers,
    has_answers,
    system_layout,
    system_ranges,
    use_answer_file,
)
from src.clean_score.utils.per_system_prompt import prompt_for_answers

FIXTURE = os.path.join(os.path.dirname(__file__), "test_files", "laulun_aika.mscx")


def _load():
    return etree.parse(FIXTURE).getroot()


# The user's reading of the score, per system: staff_id -> its voices, top to bottom.
# Systems: 0=m1-6, 1=m7-11, 2=m12-15, 3=m16-19, 4=m20-25, 5=m26-29, 6=m30-35.
ANSWERS = {
    0: {1: "T1,T2", 2: "B"},
    1: {1: "T1,T2", 2: "B"},
    2: {1: "T1,T2", 2: "B"},
    3: {1: "T1,T2", 2: "B"},
    4: {1: "T3", 2: "B", 3: "T1,T2"},
    5: {1: "T1", 2: "B", 3: "T2"},
    6: {1: "T3", 2: "T1", 3: "T2", 4: "B"},
}


@pytest.fixture(autouse=True)
def isolated_store(tmp_path):
    """Never touch the repo's real answer file from a test."""
    with use_answer_file(str(tmp_path / "answers.json")):
        yield


def _rebuilt(answers=None):
    """Rebuild the fixture from answers; returns (result, root, staves-by-part)."""
    root = _load()
    result = clean_per_system(root, answers_from=lambda _layouts: answers or ANSWERS)
    staves = {p.find("trackName").text: s
              for p, s in zip(root.findall(".//Part"), root.findall(".//Score/Staff"))}
    return result, root, staves


def _pitches(staff, measure_index):
    m = staff.findall("Measure")[measure_index]
    return [
        n.findtext("pitch")
        for v in m.findall("voice")
        for ch in v.findall("Chord")
        for n in ch.findall("Note")
    ]


# --------------------------------------------------------------------------- #
# System discovery + layout
# --------------------------------------------------------------------------- #

def test_system_ranges_are_1_based_measure_spans():
    ranges = [(r.start, r.end) for r in system_ranges(_load())]
    assert ranges == [(1, 6), (7, 11), (12, 15), (16, 19), (20, 25), (26, 29), (30, 35)]


def test_layout_reports_the_staves_to_name_per_system():
    layouts = system_layout(_load())
    assert [l.index for l in layouts] == list(range(7))
    first = layouts[0]
    assert (first.start, first.end) == (1, 6)
    # System 1: staff 1 carries two voices (T1+T2 divisi), staff 2 one.
    assert [(r.staff_id, r.voices) for r in first.staves] == [(1, 2), (2, 1)]
    assert first.staves[0].summary != "(empty)"
    assert first.staves[0].answer == ""  # nothing recorded yet
    # System 7: all four staves sound.
    assert [r.staff_id for r in layouts[6].staves] == [1, 2, 3, 4]


def test_layout_prefills_recorded_answers():
    save_answers(FIXTURE, ANSWERS)
    layouts = layout_for_file(FIXTURE)
    assert layouts[4].staves[0].answer == "T3"
    assert layouts[4].staves[2].answer == "T1,T2"
    # to_dict is the shape the web grid consumes.
    d = layouts[0].to_dict()
    assert d["system"] == 0 and d["measure_start"] == 1 and d["measure_end"] == 6
    assert d["staves"][0]["staff_id"] == 1 and d["staves"][0]["voices"] == 2


# --------------------------------------------------------------------------- #
# Reconstruction
# --------------------------------------------------------------------------- #

def test_parts_order_and_count():
    result, root, _ = _rebuilt()
    assert result.parts == ["T1", "T2", "T3", "B"]
    assert len(root.findall(".//Score/Staff")) == 4
    assert [p.find("trackName").text for p in root.findall(".//Part")] == result.parts


def test_absent_part_gets_measure_rests_until_it_enters():
    src = _load()  # untouched copy for source-content comparison
    _, _, staves = _rebuilt()
    t3 = staves["T3"]
    # System 0 (m1) has no T3 -> measure rest, no notes.
    assert _pitches(t3, 0) == []
    assert t3.findall("Measure")[0].find("voice/Rest/durationType").text == "measure"
    # System 4 (m20) declares T3 = source staff 1 voice 0; content must match the source.
    src_staff1 = src.findall(".//Score/Staff")[0]
    assert _pitches(t3, 19) == _pitches(src_staff1, 19)
    assert _pitches(t3, 19)  # non-empty


def test_parts_pull_from_the_right_staff_per_system():
    src = _load()
    _, _, staves = _rebuilt()
    src_s = src.findall(".//Score/Staff")
    # m26 (system 5): T2 comes from source staff 3 voice 0.
    assert _pitches(staves["T2"], 25) == _pitches(src_s[2], 25)
    # m26: T1 comes from source staff 1 voice 0.
    assert _pitches(staves["T1"], 25) == _pitches(src_s[0], 25)
    # m30 (system 6): B comes from source staff 4 voice 0.
    assert _pitches(staves["B"], 29) == _pitches(src_s[3], 29)


def test_tuplet_survives_rebuild():
    """Measure 14 has a triplet (Tuplet/endTuplet) — it must survive into the rebuilt part."""
    _, _, staves = _rebuilt()
    # m14 (index 13) T1 comes from source staff 1 voice 0, which has the triplet.
    voice = staves["T1"].findall("Measure")[13].find("voice")
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
    _, root, _ = _rebuilt()
    staves = root.findall(".//Score/Staff")
    # Top staff carries the system breaks (end of each system except the last).
    assert _line_break_measures(staves[0]) == [5, 10, 14, 18, 24, 28]
    # Lower staves carry none.
    assert _line_break_measures(staves[1]) == []


def test_rebuild_strips_decorations_the_split_also_removes():
    _, root, _ = _rebuilt()
    for selector in (".//Lyrics", ".//Dynamic", ".//Harmony", ".//bracket"):
        assert root.findall(selector) == []


def test_duplicate_name_in_one_system_is_first_wins():
    """Two staves claiming the same part in one system: the first declaration wins."""
    answers = {i: dict(a) for i, a in ANSWERS.items()}
    answers[6] = {1: "T3", 2: "T1", 3: "T1", 4: "B"}  # staff 3 also claims T1
    src = _load()
    result, _, staves = _rebuilt(answers)
    assert result.parts == ["T1", "T2", "T3", "B"]
    src_s = src.findall(".//Score/Staff")
    # T1 in m30 comes from staff 2 (the first claim), not staff 3.
    assert _pitches(staves["T1"], 29) == _pitches(src_s[1], 29)
    # Nobody is named T2 there, so T2 rests through the last system.
    assert _pitches(staves["T2"], 29) == []


def test_blank_answer_carries_the_previous_system_forward():
    """Only the systems where the layout changes need an answer."""
    sparse = {0: ANSWERS[0], 4: ANSWERS[4], 5: ANSWERS[5], 6: ANSWERS[6]}
    full, _, _ = _rebuilt()
    lean, _, _ = _rebuilt(sparse)
    assert lean.parts == full.parts
    assert lean.lyric_map == full.lyric_map


def test_no_answers_leaves_the_score_untouched():
    root = _load()
    before = etree.tostring(root)
    result = clean_per_system(root, input_path=FIXTURE)  # nothing recorded
    assert not result
    assert result.parts == []
    assert etree.tostring(root) == before


# --------------------------------------------------------------------------- #
# Lyric routing metadata
# --------------------------------------------------------------------------- #

def test_lyric_map_handles_omitted_and_reordered_staves():
    """
    Printed staff numbering shifts per system as parts are omitted; the map must order
    by musical rank (not OCR source order) and merge divisi onto one printed staff.
    """
    result, _, _ = _rebuilt()  # parts ["T1","T2","T3","B"] -> ids 1,2,3,4
    by_range = {(e["start"], e["end"]): e["map"] for e in result.lyric_map}
    # System 1 (m1-6): T1+T2 share source staff 1 (divisi) -> one printed staff; B -> staff 2.
    assert by_range[(1, 6)] == {1: [1, 2], 2: [4]}
    # System 5 (m20-25): T1,T2 divisi printed first, then T3, then B (rank order, not source order).
    assert by_range[(20, 25)] == {1: [1, 2], 2: [3], 3: [4]}
    # System 6 (m26-29): T3 omitted -> printed 3 is the BASS (output staff 4), not T3.
    assert by_range[(26, 29)] == {1: [1], 2: [2], 3: [4]}
    # System 7 (m30-35): all four present, one each.
    assert by_range[(30, 35)] == {1: [1], 2: [2], 3: [3], 4: [4]}


def test_lyric_maps_are_written_into_the_score():
    """Lyric import reads the routing back out of the score's metaTags."""
    result, root, _ = _rebuilt()
    meta = {m.get("name"): m.text for m in root.findall(".//Score/metaTag")}
    assert json.loads(meta["lyricsSystemMap"]) == json.loads(json.dumps(result.lyric_map))
    # Identity fallback, one entry per output staff.
    assert meta["lyricsStaffMap"] == "1:1;2:2;3:3;4:4"


# --------------------------------------------------------------------------- #
# The two assignment adapters
# --------------------------------------------------------------------------- #

def _scripted_prompt(replies):
    """A prompt adapter whose typing is `replies` (in the order it is asked)."""
    it = iter(replies)

    def source(layouts):
        with open(os.devnull, "w") as devnull:
            return prompt_for_answers(layouts, ask=lambda _p: next(it), out=devnull)

    return source


def _typed_like(answers, layouts):
    """What a user would type at the prompt to produce `answers`, in prompt order."""
    return [answers.get(l.index, {}).get(r.staff_id, "-") for l in layouts for r in l.staves]


def test_prompt_adapter_and_grid_answers_rebuild_the_same_score():
    """The CLI prompt and the web grid are two ways into the same behavior."""
    from_grid, _, _ = _rebuilt(ANSWERS)

    root = _load()
    typed = _typed_like(ANSWERS, system_layout(root))
    from_prompt = clean_per_system(root, answers_from=_scripted_prompt(typed))

    assert from_prompt.parts == from_grid.parts
    assert from_prompt.lyric_map == from_grid.lyric_map


def test_prompt_enter_reuses_the_previous_answer(capsys):
    layouts = system_layout(_load())
    # System 1: type the answers; every later system: press Enter to reuse.
    typed = ["T1,T2", "B"] + [""] * (sum(len(l.staves) for l in layouts) - 2)
    replies = iter(typed)
    answers = prompt_for_answers(layouts, ask=lambda _p: next(replies))
    assert answers[0] == {1: "T1,T2", 2: "B"}
    assert answers[1] == {1: "T1,T2", 2: "B"}          # reused
    assert answers[6][3] == ""  # staff 3 first appears in system 5 -> nothing to reuse


def test_prompt_dash_clears_a_staff():
    layouts = system_layout(_load())[:2]
    replies = iter(["T1,T2", "B", "-", ""])
    answers = prompt_for_answers(layouts, ask=lambda _p: next(replies))
    assert answers == {0: {1: "T1,T2", 2: "B"}, 1: {1: "-", 2: "B"}}
    # A cleared staff declares nothing, and stays cleared for the later systems too
    # (staff 1 sounds again from system 5 on, but nothing is named there).
    result, _, staves = _rebuilt(answers)
    assert result.parts == ["T1", "T2", "B"]
    assert _pitches(staves["T1"], 29) == []


def test_prompt_offers_the_recorded_answer_as_the_default():
    save_answers(FIXTURE, ANSWERS)
    layouts = layout_for_file(FIXTURE)
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return ""  # accept every default

    assert prompt_for_answers(layouts, ask=ask) == ANSWERS
    assert "[T1,T2]" in prompts[0]


# --------------------------------------------------------------------------- #
# Answer persistence
# --------------------------------------------------------------------------- #

def test_answers_are_recorded_per_input_score():
    save_answers("/somewhere/song-x.mscx", {0: {1: "T1,T2", 2: "B"}, 1: {1: "T3"}})
    save_answers("/elsewhere/song-y.mscz", {0: {1: "S"}})  # a second score coexists
    assert saved_answers("songs/song-x.mscx") == {0: {1: "T1,T2", 2: "B"}, 1: {1: "T3"}}
    assert saved_answers("song-y.mscx") == {0: {1: "S"}}
    assert saved_answers("missing.mscx") is None
    assert has_answers("song-y.mscx") and not has_answers("missing.mscx")


def test_prompted_answers_are_recorded_for_the_next_run():
    root = _load()
    typed = _typed_like(ANSWERS, system_layout(root))
    clean_per_system(root, input_path=FIXTURE, answers_from=_scripted_prompt(typed))
    assert saved_answers(FIXTURE) == ANSWERS
    # A later run needs no prompting at all.
    again = clean_per_system(_load(), input_path=FIXTURE)
    assert again.parts == ["T1", "T2", "T3", "B"]
