"""The by-system lyric editor's model (#393, #397): one box = one verse x one printed
system x one part x one voice, and an import changes only the boxes it sends.

Each test builds a small score, imports what the editor would send for one verse, and
reads the words back off the notes.
"""

from lxml import etree

from src.clean_score.lyric_txt import (
    TOO_FEW, TOO_MANY, blocks_from_boxes, editor_grid, place_lyrics, printed_staves,
)

from .scorebuilder import build_score

SYSTEMS = [(1, 2), (3, 4), (5, 6)]


def _staff(root, sid):
    return [s for s in root.find(".//Score").findall("Staff") if s.get("id") == str(sid)][0]


def _add_voice2(root, sid, measure, chords=2):
    bar = _staff(root, sid).findall("Measure")[measure - 1]
    voice = etree.Element("voice")
    for _ in range(chords):
        voice.append(etree.fromstring(
            "<Chord><durationType>quarter</durationType><Note><pitch>55</pitch></Note></Chord>"))
    bar.findall("voice")[-1].addnext(voice)


def _words(root, sid, voice=1, verse=1):
    """[(bar, syllabic, text)] of one voice and verse, in order."""
    out = []
    for m, bar in enumerate(_staff(root, sid).findall("Measure"), start=1):
        tracks = bar.findall("voice")
        if len(tracks) < voice:
            continue
        for chord in tracks[voice - 1].findall("Chord"):
            for ly in chord.findall("Lyrics"):
                no = (ly.findtext("no") or "").strip()
                if (int(no) + 1 if no.isdigit() else 1) == verse:
                    out.append((m, ly.findtext("syllabic"), ly.findtext("text")))
    return out


def _import(root, verse, boxes, systems=SYSTEMS):
    grid = editor_grid(root, systems=systems)
    blocks = blocks_from_boxes(grid, verse, boxes)
    return place_lyrics(root, blocks, fmt="json", systems=systems)


def _score():
    return build_score(staff_ids=(1, 2), measures=6, chords=2)


def test_an_import_changes_only_the_boxes_it_sends():
    root = _score()
    _import(root, 1, {0: {"1": "a b c d"}, 1: {"1": "e f g h"}, 2: {"2": "x y z w"}})
    before_p2 = _words(root, 2)
    result = _import(root, 1, {1: {"1": "uu vv ww zz"}})
    assert not result.mismatches
    assert [t for _, _, t in _words(root, 1)] == ["a", "b", "c", "d", "uu", "vv", "ww", "zz"]
    assert _words(root, 2) == before_p2


def test_emptying_a_box_removes_its_words_for_that_verse_and_voice_only():
    root = _score()
    _add_voice2(root, 1, 3)
    _import(root, 1, {0: {"1": "a b c d"}, 1: {"1": "e f g h", "1@2": "ke vät"}})
    _import(root, 2, {1: {"1": "toi nen sä ke"}})
    _import(root, 1, {1: {"1": ""}})
    assert [m for m, _, _ in _words(root, 1)] == [1, 1, 2, 2]
    assert [t for _, _, t in _words(root, 1, voice=2)] == ["ke", "vät"]
    assert [t for _, _, t in _words(root, 1, verse=2)] == ["toi", "nen", "sä", "ke"]


def test_a_verse_is_written_to_its_own_number_and_shown_on_its_own_tab():
    root = _score()
    _import(root, 1, {0: {"1": "yk-si kak-si"}})
    _import(root, 3, {0: {"1": "kol-me nel-jä"}})
    assert [t for _, _, t in _words(root, 1)] == ["yk", "si", "kak", "si"]
    assert [t for _, _, t in _words(root, 1, verse=3)] == ["kol", "me", "nel", "jä"]
    grid = editor_grid(root, systems=SYSTEMS)
    assert grid.verses == [1, 2, 3]
    box = [b for b in grid.boxes[0] if b.key == "1"][0]
    assert box.text == {1: "yk-si kak-si", 2: "", 3: "kol-me nel-jä"}


def test_a_second_voice_box_exists_only_where_that_voice_has_notes():
    root = _score()
    _add_voice2(root, 2, 4, chords=3)
    grid = editor_grid(root, systems=SYSTEMS)
    keys = {si: [b.key for b in boxes] for si, boxes in grid.boxes.items()}
    assert keys == {0: ["1", "2"], 1: ["1", "2", "2@2"], 2: ["1", "2"]}
    v2 = [b for b in grid.boxes[1] if b.key == "2@2"][0]
    assert (v2.voice, v2.slots) == (2, 3)
    result = _import(root, 1, {1: {"2@2": "ke-vät on"}})
    assert not result.mismatches
    assert _words(root, 2, voice=2) == [(4, "begin", "ke"), (4, "end", "vät"), (4, "single", "on")]
    assert _words(root, 2) == []


def test_voice_1_words_go_above_the_staff_only_in_systems_with_voice_2_words():
    root = _score()
    _add_voice2(root, 1, 3)
    _import(root, 1, {0: {"1": "a b c d"}, 1: {"1": "e f g h", "1@2": "ke vät"}})

    def placements(voice):
        return {m: [ly.findtext("placement") for ly in chord.findall("Lyrics")][0]
                for m, bar in enumerate(_staff(root, 1).findall("Measure"), start=1)
                for tracks in [bar.findall("voice")] if len(tracks) >= voice
                for chord in tracks[voice - 1].findall("Chord") if chord.find("Lyrics") is not None}

    assert placements(1) == {1: None, 2: None, 3: "above", 4: "above"}
    assert placements(2) == {3: None}
    _import(root, 1, {1: {"1@2": ""}})
    assert placements(1) == {1: None, 2: None, 3: None, 4: None}


def test_held_words_keep_their_end_syllable():
    """Kyrie's `e-lei- _ _ son`: the notes under `_` hold the word, `son` ends it."""
    root = build_score(staff_ids=(1,), measures=2, chords=4)
    _import(root, 1, {0: {"1": "e-lei- _ _ son, Ky-ri- e"}}, systems=[(1, 2)])
    assert _words(root, 1) == [
        (1, "begin", "e"), (1, "middle", "lei"), (2, "end", "son,"),
        (2, "begin", "Ky"), (2, "middle", "ri"), (2, "end", "e")]
    grid = editor_grid(root, systems=[(1, 2)])
    assert grid.boxes[0][0].text[1] == "e-lei- _ _ son, Ky-ri-e"


def test_a_word_open_at_a_systems_end_is_continued_by_the_next_system_alone():
    root = _score()
    _import(root, 1, {0: {"1": "a b c lai-"}})
    _import(root, 1, {1: {"1": "ne-hil-le d"}})
    assert _words(root, 1)[3:] == [(2, "begin", "lai"), (3, "middle", "ne"),
                                   (3, "middle", "hil"), (4, "end", "le"), (4, "single", "d")]


def test_a_long_line_runs_into_an_empty_system_and_stops_at_one_with_words():
    root = _score()
    _import(root, 1, {2: {"1": "x y z w"}})
    result = _import(root, 1, {0: {"1": "a b c d e f g h i j"}})
    assert [t for _, _, t in _words(root, 1)][:8] == list("abcdefg") + ["h i j"]
    assert [t for m, _, t in _words(root, 1) if m >= 5] == ["x", "y", "z", "w"]
    assert [(w.kind, w.measure_start, w.measure_end) for w in result.mismatches] == [
        (TOO_MANY, 1, 4)]


def test_a_short_box_clears_the_rest_of_its_system_and_says_so():
    root = _score()
    _import(root, 1, {0: {"1": "a b c d"}})
    result = _import(root, 1, {0: {"1": "a b"}})
    assert [t for _, _, t in _words(root, 1)] == ["a", "b"]
    assert [(w.kind, w.slots, w.syllables) for w in result.mismatches] == [(TOO_FEW, 4, 2)]
    assert result.written[0].to_dict() == {"staff": 1, "voice": 1, "verse": 1, "start": 1, "end": 2}


def test_a_paste_carries_every_verse_and_second_voices():
    root = _score()
    _add_voice2(root, 2, 1)
    result = place_lyrics(root, [
        {"measure_start": 1, "lyrics": [
            {"parts": ["P1"], "text": "a b c d"},
            {"parts": ["P1"], "verse": 2, "text": "e f g h"},
            {"parts": ["P2"], "voice": 2, "text": "ke vät"}]},
        {"measure_start": 3, "lyrics": [{"parts": ["P1"], "text": "i j k l"}]},
    ], fmt="json", systems=SYSTEMS)
    assert not result.mismatches
    assert [t for _, _, t in _words(root, 1)] == list("abcdijkl")
    assert [t for _, _, t in _words(root, 1, verse=2)] == list("efgh")
    assert [t for _, _, t in _words(root, 2, voice=2)] == ["ke", "vät"]


def test_the_printed_staves_of_a_system_are_read_off_the_part_to_staff_record():
    root = build_score(staff_ids=(1, 2, 3), measures=2, chords=2, staff_map="1:1,2;2:3")
    for bar in _staff(root, 3).findall("Measure"):
        for chord in bar.find("voice").findall("Chord"):
            chord.tag = "Rest"
    assert printed_staves(root, 1, 2) == ([1, 2], [1])
    grid = editor_grid(root, systems=[(1, 2)])
    assert {b.key: b.printed for b in grid.boxes[0]} == {"1": 1, "2": 1}


def test_two_parts_with_one_name_keep_their_own_boxes():
    """Kaipaava has two parts named T2: their boxes are keyed by staff, not by name."""
    root = build_score(staff_ids=(1, 2), measures=2, chords=2, names={1: "T2", 2: "T2"})
    grid = editor_grid(root, systems=[(1, 2)])
    assert [b.key for b in grid.boxes[0]] == ["1", "2"]
    _import(root, 1, {0: {"2": "a b c d"}}, systems=[(1, 2)])
    assert _words(root, 1) == []
    assert [t for _, _, t in _words(root, 2)] == ["a", "b", "c", "d"]
