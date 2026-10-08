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
    dropped_voices,
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


def test_a_page_break_ends_a_system_like_a_line_break():
    """A page turn starts a new printed system, so it has to cut one here too.

    Only "line" used to count, and a score whose page turns are page breaks then
    reported one system straddling the turn: the grid asked one question for two
    printed systems and the lyric editor offered one cell for both.
    """
    root = _load()
    top = root.findall(".//Score/Staff")[0].findall("Measure")
    before = [(r.start, r.end) for r in system_ranges(root)]
    # m8 sits inside the first system (m1-6 ends at 6, so m7-11 is the second).
    etree.SubElement(etree.SubElement(top[7], "LayoutBreak"), "subtype").text = "page"
    after = [(r.start, r.end) for r in system_ranges(root)]
    assert (7, 11) in before and (7, 11) not in after
    assert (7, 8) in after and (9, 11) in after
    assert len(after) == len(before) + 1


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
    assert saved_answers("/somewhere/song-x.mscx") == {0: {1: "T1,T2", 2: "B"}, 1: {1: "T3"}}
    assert saved_answers("/elsewhere/song-y.mscx") == {0: {1: "S"}}
    assert saved_answers("/somewhere/missing.mscx") is None
    assert has_answers("/elsewhere/song-y.mscx") and not has_answers("/somewhere/missing.mscx")


def test_two_songs_with_the_same_file_name_keep_their_own_answers():
    """Every scanned song's input is called `scanned`; answering one must not
    replace another's."""
    save_answers("/songs/a/scanned.mscx", {0: {1: "S1"}})
    save_answers("/songs/b/scanned.mscx", {0: {1: "T1"}})
    assert saved_answers("/songs/a/scanned.mscx") == {0: {1: "S1"}}
    assert saved_answers("/songs/b/scanned.mscx") == {0: {1: "T1"}}


def test_answers_recorded_under_the_file_name_alone_are_still_read(tmp_path):
    path = tmp_path / "answers.json"
    path.write_text(json.dumps({"old-song": {"0": {"1": "B"}}}))
    with use_answer_file(str(path)):
        assert saved_answers("/songs/old/old-song.mscx") == {0: {1: "B"}}


def test_prompted_answers_are_recorded_for_the_next_run():
    root = _load()
    typed = _typed_like(ANSWERS, system_layout(root))
    clean_per_system(root, input_path=FIXTURE, answers_from=_scripted_prompt(typed))
    assert saved_answers(FIXTURE) == ANSWERS
    # A later run needs no prompting at all.
    again = clean_per_system(_load(), input_path=FIXTURE)
    assert again.parts == ["T1", "T2", "T3", "B"]


# --------------------------------------------------------------------------- #
# Divisi written as a chord in one voice
# --------------------------------------------------------------------------- #

def _stacked_score(pitches=("43", "50")):
    """One measure, one staff, one voice, whose chord stacks two noteheads.

    What an engraver writes when two singers hold a chord together — the shape that
    used to hand both notes to the upper part and leave the lower one silent.
    """
    root = etree.Element("museScore")
    score = etree.SubElement(root, "Score")
    part = etree.SubElement(score, "Part")
    etree.SubElement(part, "trackName").text = "B"
    etree.SubElement(part, "Staff", id="1")
    staff = etree.SubElement(score, "Staff", id="1")
    measure = etree.SubElement(staff, "Measure")
    voice = etree.SubElement(measure, "voice")
    chord = etree.SubElement(voice, "Chord")
    etree.SubElement(chord, "durationType").text = "whole"
    for pitch in pitches:
        etree.SubElement(etree.SubElement(chord, "Note"), "pitch").text = pitch
    return root


def _by_part(root):
    return {p.find("trackName").text: s
            for p, s in zip(root.findall(".//Part"), root.findall(".//Score/Staff"))}


def test_two_parts_on_a_stacked_chord_take_one_notehead_each():
    root = _stacked_score()
    clean_per_system(root, answers_from=lambda _l: {0: {1: "B1,B2"}})
    staves = _by_part(root)
    assert _pitches(staves["B1"], 0) == ["50"]   # upper notehead
    assert _pitches(staves["B2"], 0) == ["43"]   # lower one, which used to be silence


def test_where_the_stack_narrows_the_parts_sing_in_unison():
    """Inside a stacked bar, a lone notehead is the two voices converging, not a rest."""
    root = _stacked_score()
    voice = root.find(".//Score/Staff/Measure/voice")
    single = etree.SubElement(voice, "Chord")
    etree.SubElement(single, "durationType").text = "whole"
    etree.SubElement(etree.SubElement(single, "Note"), "pitch").text = "48"
    clean_per_system(root, answers_from=lambda _l: {0: {1: "B1,B2"}})
    staves = _by_part(root)
    assert _pitches(staves["B1"], 0) == ["50", "48"]
    assert _pitches(staves["B2"], 0) == ["43", "48"]   # not a bar of silence


def test_one_voice_with_no_stack_is_left_to_rest():
    """One printed line in a bar of a two-voice staff: unison or a tacit voice is a
    reading of the page, so the rebuild declines to guess and the second part rests."""
    root = _stacked_score(pitches=("50",))
    staff = root.find(".//Score/Staff")
    lower = etree.SubElement(staff.find("Measure"), "voice")   # a real second voice
    chord = etree.SubElement(lower, "Chord")
    etree.SubElement(chord, "durationType").text = "whole"
    etree.SubElement(etree.SubElement(chord, "Note"), "pitch").text = "43"
    measure2 = etree.SubElement(staff, "Measure")              # ...absent in the next bar
    single = etree.SubElement(etree.SubElement(measure2, "voice"), "Chord")
    etree.SubElement(single, "durationType").text = "whole"
    etree.SubElement(etree.SubElement(single, "Note"), "pitch").text = "48"

    clean_per_system(root, answers_from=lambda _l: {0: {1: "B1,B2"}})
    staves = _by_part(root)
    assert _pitches(staves["B1"], 1) == ["48"]
    assert _pitches(staves["B2"], 1) == []        # a measure rest, as before the change


def test_a_staff_with_its_own_second_voice_is_left_alone():
    """Two real voices still copy verbatim: chords there are genuine double stops."""
    root = _stacked_score()
    voice2 = etree.SubElement(root.find(".//Measure"), "voice")
    chord = etree.SubElement(voice2, "Chord")
    etree.SubElement(chord, "durationType").text = "whole"
    etree.SubElement(etree.SubElement(chord, "Note"), "pitch").text = "38"
    clean_per_system(root, answers_from=lambda _l: {0: {1: "B1,B2"}})
    staves = _by_part(root)
    assert _pitches(staves["B1"], 0) == ["43", "50"]   # voice 1, both noteheads
    assert _pitches(staves["B2"], 0) == ["38"]         # voice 2


# --------------------------------------------------------------------------- #
# A tenor read off a plain treble staff
# --------------------------------------------------------------------------- #

def _treble_score(clef):
    root = _stacked_score(pitches=("69",))
    voice = root.find(".//Score/Staff/Measure/voice")
    mark = etree.Element("Clef")
    etree.SubElement(mark, "concertClefType").text = clef
    etree.SubElement(mark, "transposingClefType").text = clef
    voice.insert(0, mark)
    return root


def test_a_tenor_off_a_plain_treble_staff_moves_down_an_octave():
    """The staff becomes G8vb, so notes taken at treble pitch have to move with it,
    or the practice track sings the tenor line an octave above the men."""
    root = _treble_score("G")
    clean_per_system(root, answers_from=lambda _l: {0: {1: "T1"}})
    assert _pitches(_by_part(root)["T1"], 0) == ["57"]


def test_a_tenor_off_an_8vb_staff_keeps_its_pitch():
    root = _treble_score("G8vb")
    clean_per_system(root, answers_from=lambda _l: {0: {1: "T1"}})
    assert _pitches(_by_part(root)["T1"], 0) == ["69"]


def test_a_soprano_off_a_treble_staff_keeps_its_pitch():
    root = _treble_score("G")
    clean_per_system(root, answers_from=lambda _l: {0: {1: "S1"}})
    assert _pitches(_by_part(root)["S1"], 0) == ["69"]


# --------------------------------------------------------------------------- #
# A "b" part falls back to its base part (S1b sings S1 where it has nothing)
# --------------------------------------------------------------------------- #

def _score(staves, breaks=()):
    """{staff_id: [bar, ...]}, a bar a list of voices, a voice a list of whole notes:
    a pitch string, "r" for a rest, or a tuple of pitches for a stacked chord.
    `breaks` are 0-based bars that end a printed system."""
    root = etree.Element("museScore")
    score = etree.SubElement(root, "Score")
    for sid in staves:
        part = etree.SubElement(score, "Part")
        etree.SubElement(part, "trackName").text = f"P{sid}"
        etree.SubElement(part, "Staff", id=str(sid))
    for sid, bars in staves.items():
        staff = etree.SubElement(score, "Staff", id=str(sid))
        for mi, bar in enumerate(bars):
            measure = etree.SubElement(staff, "Measure")
            for notes in bar:
                voice = etree.SubElement(measure, "voice")
                for note in notes:
                    if note == "r":
                        rest = etree.SubElement(voice, "Rest")
                        etree.SubElement(rest, "durationType").text = "measure"
                        continue
                    chord = etree.SubElement(voice, "Chord")
                    etree.SubElement(chord, "durationType").text = "whole"
                    for pitch in (note if isinstance(note, tuple) else (note,)):
                        etree.SubElement(etree.SubElement(chord, "Note"), "pitch").text = pitch
            if mi in breaks and sid == min(staves):
                lb = etree.SubElement(measure, "LayoutBreak")
                etree.SubElement(lb, "subtype").text = "line"
    return root


def test_a_b_part_not_named_in_a_system_sings_its_base_part():
    # System 0 (bar 0) names S1 alone; system 1 (bar 1) splits the stacked chord.
    root = _score({1: [[["72"]], [[("72", "67")]]]}, breaks=(0,))
    clean_per_system(root, answers_from=lambda _l: {0: {1: "S1"}, 1: {1: "S1, S1b"}})
    staves = _by_part(root)
    assert _pitches(staves["S1b"], 0) == ["72"]     # was a bar of silence
    assert _pitches(staves["S1b"], 1) == ["67"]     # its own notehead, as before


def test_a_b_part_on_one_unstacked_line_sings_its_base_part():
    """The bar #293 was about: S1, S1b named, but the page prints a single line."""
    root = _score({1: [[["72"], ["67"]], [["74"]]]})
    clean_per_system(root, answers_from=lambda _l: {0: {1: "S1, S1b"}})
    staves = _by_part(root)
    assert _pitches(staves["S1b"], 0) == ["67"]
    assert _pitches(staves["S1b"], 1) == ["74"]     # unison, by the name's say-so


def test_a_rest_the_scan_wrote_for_the_b_part_stays_a_rest():
    root = _score({1: [[["72"]], [["74"]]], 2: [[["r"]], [["67"]]]})
    clean_per_system(root, answers_from=lambda _l: {0: {1: "S1", 2: "S1b"}})
    staves = _by_part(root)
    assert _pitches(staves["S1b"], 0) == []         # its own staff says it rests
    assert _pitches(staves["S1b"], 1) == ["67"]


def test_only_a_base_name_plus_one_lowercase_letter_falls_back():
    root = _score({1: [[["72"]], [["74"]]], 2: [[["60"]], [["62"]]]}, breaks=(0,))
    clean_per_system(root, answers_from=lambda _l: {0: {1: "S1", 2: "A1"},
                                                     1: {1: "S2", 2: "Ab"}})
    staves = _by_part(root)
    assert _pitches(staves["S2"], 0) == []          # S2 is not S1's fallback
    assert _pitches(staves["Ab"], 0) == []          # no part named "A"
    assert _pitches(staves["S1"], 1) == []


def test_a_fallback_is_one_level_only():
    """S1bc is not S1b's fallback: S1b's bar here is borrowed from S1, not its own."""
    root = _score({1: [[["72"]], [["74"], ["67"]]], 2: [[["r"]], [["60"]]]}, breaks=(0,))
    result = clean_per_system(root, answers_from=lambda _l: {0: {1: "S1"},
                                                             1: {1: "S1, S1b", 2: "S1bc"}})
    staves = _by_part(root)
    assert _pitches(staves["S1b"], 0) == ["72"]     # S1b still borrows from S1
    assert _pitches(staves["S1bc"], 0) == []        # ...but S1bc does not borrow that
    by_start = {e["start"]: e["map"] for e in result.lyric_map}
    follow = {e["start"]: e.get("follow") for e in result.lyric_map}
    assert by_start[1] == {1: [1]}
    assert follow[1] == {1: [2]}                    # S1's words reach S1b, not S1bc


def test_lyrics_follow_the_notes_a_b_part_borrows():
    root = _score({1: [[["72"]], [[("72", "67")]]]}, breaks=(0,))
    result = clean_per_system(root, answers_from=lambda _l: {0: {1: "S1"}, 1: {1: "S1, S1b"}})
    assert result.parts == ["S1", "S1b"]
    by_start = {e["start"]: e for e in result.lyric_map}
    assert by_start[1]["map"] == {1: [1]}           # the printed staff is S1's alone
    assert by_start[1]["follow"] == {1: [2]}        # ...and S1's words go to S1b too
    assert by_start[2]["map"] == {1: [1, 2]}        # divisi, as before
    assert "follow" not in by_start[2]


def test_a_b_part_takes_its_base_words_on_a_staff_the_base_shares():
    """S1 above and S2 below one printed staff: S1b follows S1's lane, not both."""
    from src.clean_score.lyric_txt import place_lyrics
    from src.clean_score.tests.scorebuilder import placed_lyrics

    root = _score({1: [[[("72", "67")]], [[("72", "67")]]]}, breaks=(0,))
    result = clean_per_system(root, answers_from=lambda _l: {0: {1: "S1, S2"},
                                                             1: {1: "S1, S1b"}})
    assert result.parts == ["S1", "S1b", "S2"]
    first = result.lyric_map[0]
    assert first["map"] == {1: [1, 3]}              # the above/below pair is untouched
    assert first["follow"] == {1: [2]}

    block = {"measure_start": 1, "lyrics": [
        {"text": "la", "staff_number": 1, "position": "above", "verse": 1},
        {"text": "lo", "staff_number": 1, "position": "below", "verse": 1}]}
    root = etree.fromstring(etree.tostring(root))   # read the metaTags back as import does
    place_lyrics(root, json.dumps([block]), fmt="json", replace=True)
    assert placed_lyrics(root) == {1: "la", 2: "la", 3: "lo"}


def test_a_b_part_with_words_of_its_own_keeps_them():
    from src.clean_score.lyric_txt import place_lyrics
    from src.clean_score.tests.scorebuilder import placed_lyrics

    root = _score({1: [[["72"]], [[("72", "67")]]]}, breaks=(0,))
    clean_per_system(root, answers_from=lambda _l: {0: {1: "S1"}, 1: {1: "S1, S1b"}})
    block = {"measure_start": 1, "lyrics": [
        {"text": "la", "staff_number": 1, "verse": 1},
        {"text": "lu", "parts": ["S1b"], "verse": 1}]}
    place_lyrics(root, json.dumps([block]), fmt="json", replace=True)
    assert placed_lyrics(root) == {1: "la", 2: "lu"}


def _tie(note, side, measures):
    spanner = etree.SubElement(note, "Spanner", type="Tie")
    etree.SubElement(spanner, "Tie")
    loc = etree.SubElement(etree.SubElement(spanner, side), "location")
    etree.SubElement(loc, "measures").text = str(measures)


def test_a_tie_from_a_borrowed_bar_into_an_own_bar_is_cut():
    # Bar 0: one line (S1b borrows it), tied into bar 1 where S1b has its own voice.
    root = _score({1: [[["67"]], [["67"], ["60"]], [["64"]]]})
    notes = [m.find("voice/Chord/Note") for m in root.findall(".//Score/Staff/Measure")]
    _tie(notes[0], "next", 1)
    _tie(notes[1], "prev", -1)
    clean_per_system(root, answers_from=lambda _l: {0: {1: "S1, S1b"}})
    staves = _by_part(root)
    assert staves["S1"].findall(".//Spanner") and len(staves["S1"].findall(".//Spanner")) == 2
    assert staves["S1b"].findall(".//Spanner") == []   # its bar 1 is a different note
    assert _pitches(staves["S1b"], 0) == ["67"]


# --------------------------------------------------------------------------- #
# A line the answers leave unnamed is said out loud (#330)
# --------------------------------------------------------------------------- #

def _two_voice_later():
    """Staff 1 prints one line in system 0 and two in system 1 (Lemmen nosto's shape)."""
    return _score({1: [[["72"]], [["72"], ["67"]]]}, breaks=(0,))


def test_an_inherited_single_name_on_a_two_voice_staff_is_reported():
    lost = dropped_voices(_two_voice_later(), {0: {1: "A1"}, 1: {1: ""}})
    assert [(d.system, d.staff_id, d.voice, d.notes, d.answered_in) for d in lost] == \
        [(1, 1, 1, 1, 0)]
    message = lost[0].message()
    assert "System 2" in message and "lower voice" in message
    assert "carried over from system 1" in message and '"A1, A1b"' in message


def test_the_rebuild_still_drops_it_and_logs_why(caplog):
    root = _two_voice_later()
    clean_per_system(root, answers_from=lambda _l: {0: {1: "A1"}, 1: {1: ""}})
    assert list(_by_part(root)) == ["A1"]
    assert any("lower voice" in r.getMessage() for r in caplog.records)


def test_naming_every_line_reports_nothing():
    assert dropped_voices(_two_voice_later(), {0: {1: "A1"}, 1: {1: "A1, A1b"}}) == []


def test_a_cleared_or_never_named_staff_is_not_this_report():
    # The grid already asks about a staff named nowhere; this is the quieter case.
    assert dropped_voices(_two_voice_later(), {0: {1: "A1"}, 1: {1: "-"}}) == []
    assert dropped_voices(_two_voice_later(), {}) == []


def test_a_second_voice_of_rests_is_not_a_lost_line():
    root = _score({1: [[["72"]], [["72"], ["r"]]]}, breaks=(0,))
    assert dropped_voices(root, {0: {1: "A1"}}) == []


def test_the_lower_notes_of_chords_under_one_name_are_reported_as_kept_in_the_chord():
    """With one name the rebuild copies the chords whole: nothing is lost, but the
    lower notes have no part of their own — the report says that, not "dropped"."""
    answers = {0: {1: "S1"}}
    root = _score({1: [[["72"]], [[("72", "67"), ("74", "69")]]]}, breaks=(0,))
    lost = dropped_voices(root, answers)
    assert [(d.system, d.voice, d.notes, d.kind) for d in lost] == [(1, 1, 2, "kept")]
    message = lost[0].message()
    assert "lower notes of its chords (2 notes) stay in S1's chords" in message
    assert "dropped" not in message
    clean_per_system(root, answers_from=lambda _l: answers)
    assert _pitches(_by_part(root)["S1"], 1) == ["72", "67", "74", "69"]   # kept


def test_chord_notes_past_the_names_stay_in_the_lowest_parts_chord():
    """#330: two names on a three-note chord split the top note off and leave the
    lowest part the rest as its chord — one voice may sing a chord — not dropped."""
    answers = {0: {1: "A2, A2b"}}
    root = _score({1: [[[("74", "69", "62")]]]})
    lost = dropped_voices(root, answers)
    assert [(d.voice, d.notes, d.kind, d.holder) for d in lost] == [(2, 1, "kept", "A2b")]
    assert "stay in A2b's chords" in lost[0].message()
    clean_per_system(root, answers_from=lambda _l: answers)
    staves = _by_part(root)
    assert _pitches(staves["A2"], 0) == ["74"]
    assert _pitches(staves["A2b"], 0) == ["69", "62"]

def test_a_borrowed_bar_leaves_the_base_parts_red_mark_behind():
    """S1b borrowing S1's bar takes the notes, not S1's ⚠ mark: one doubt, one row."""
    root = _score({1: [[["72"]], [[("72", "67")]]]}, breaks=(0,))
    voice = root.find(".//Score/Staff/Measure/voice")
    mark = etree.Element("StaffText")
    etree.SubElement(mark, "text").text = "⚠ rhythm?"
    voice.insert(0, mark)
    clean_per_system(root, answers_from=lambda _l: {0: {1: "S1"}, 1: {1: "S1, S1b"}})
    staves = _by_part(root)
    first = lambda part: staves[part].findall("Measure")[0]
    assert _pitches(staves["S1b"], 0) == ["72"]
    assert first("S1").find(".//StaffText") is not None
    assert first("S1b").find(".//StaffText") is None


def test_a_dropped_voice_and_kept_chord_notes_in_one_system_are_said_apart():
    """Review of #332: bar 1 has two voices, bar 2 one stacked voice, under one name.
    Bar 1's lower voice is lost, bar 2's lower note stays in the chord — two reports."""
    answers = {0: {1: "A1"}}
    root = _score({1: [[["72"], ["67"]], [[("72", "67")]]]})
    lost = dropped_voices(root, answers)
    assert [(d.kind, d.voice, d.notes) for d in lost] == [("voice", 1, 1), ("kept", 1, 1)]
    assert "lower voice (1 note) was dropped" in lost[0].message()
    assert "stay in A1's chords" in lost[1].message()
    clean_per_system(root, answers_from=lambda _l: answers)
    staves = _by_part(root)
    assert _pitches(staves["A1"], 0) == ["72"]           # the lower voice is gone
    assert _pitches(staves["A1"], 1) == ["72", "67"]     # the chord is whole


def test_a_voice_under_an_all_rest_voice_is_counted_by_its_written_index():
    """Review of #332: the rebuild copies voice 0 (all rests) to the one name and
    drops voice 1, so the report has to count voice 1 too."""
    answers = {0: {1: "A1"}}
    root = _score({1: [[["r"], ["67"]]]})
    lost = dropped_voices(root, answers)
    assert [(d.kind, d.voice, d.notes) for d in lost] == [("voice", 1, 1)]
    clean_per_system(root, answers_from=lambda _l: answers)
    assert _pitches(_by_part(root)["A1"], 0) == []


def test_a_second_voice_beside_a_split_chord_is_reported_dropped():
    """Review of #332: with more names than voices and a stacked top voice, the
    rebuild cuts every part from that voice's noteheads and never copies voice 2."""
    answers = {0: {1: "A2, A2b, A2c"}}
    root = _score({1: [[[("74", "69")], ["62"]], [[("74", "69", "62")]]]})
    lost = dropped_voices(root, answers)
    assert [(d.kind, d.voice, d.notes) for d in lost] == [("beside", 1, 1)]
    assert "not copied" in lost[0].message()
    clean_per_system(root, answers_from=lambda _l: answers)
    staves = _by_part(root)
    first_bar = [p for part in ("A2", "A2b", "A2c") for p in _pitches(staves[part], 0)]
    assert "62" not in first_bar                       # voice 2's note really is lost
    assert [_pitches(staves[p], 1) for p in ("A2", "A2b", "A2c")] == [["74"], ["69"], ["62"]]


def test_a_dash_answers_its_line_and_an_empty_slot_does_not():
    """Review of #332: "T1, -" leaves the lower line silent on purpose and is not
    reported; "A1," declares only A1, drops voice 2 the same way, and is."""
    root = _two_voice_later()
    assert dropped_voices(root, {0: {1: "A1"}, 1: {1: "A1, -"}}) == []
    lost = dropped_voices(root, {0: {1: "A1"}, 1: {1: "A1,"}})
    assert [(d.system, d.kind, d.voice, d.notes) for d in lost] == [(1, "voice", 1, 1)]
    assert '"A1, A1b"' in lost[0].message()
    for answer in ("A1, -", "A1,"):
        rebuilt = _two_voice_later()
        clean_per_system(rebuilt, answers_from=lambda _l, a=answer: {0: {1: "A1"}, 1: {1: a}})
        assert list(_by_part(rebuilt)) == ["A1"]     # both drop voice 2


def test_naming_a_voice_under_an_all_rest_voice_gives_it_a_part():
    """Review of #332: the line count follows the written voice index, so the second
    name is not capped away — A1b gets the note and nothing is reported."""
    answers = {0: {1: "A1, A1b"}}
    root = _score({1: [[["r"], ["67"]]]})
    assert [r.voices for r in system_layout(root)[0].staves] == [2]
    assert dropped_voices(root, answers) == []
    clean_per_system(root, answers_from=lambda _l: answers)
    staves = _by_part(root)
    assert _pitches(staves["A1b"], 0) == ["67"]
    assert _pitches(staves["A1"], 0) == []


def test_a_chords_noteheads_count_as_parts_but_not_as_lines_to_warn_about():
    """#330: the grid may name each notehead (voices) but warns only about written
    voices (lines), since unnamed chord notes stay in the lowest part's chord."""
    row = system_layout(_score({1: [[[("74", "69", "62")]]]}))[0].staves[0]
    assert (row.voices, row.lines) == (3, 1)
    row = system_layout(_score({1: [[["r"], ["67"]]]}))[0].staves[0]
    assert (row.voices, row.lines) == (2, 2)
