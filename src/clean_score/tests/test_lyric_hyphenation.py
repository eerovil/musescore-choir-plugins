"""A word that crosses a barline must stay one word.

The JSON import cuts each line into per-measure chunks and, in between, writes those
chunks back out as plain text. That text can say "this word carries on into the next
measure" (a trailing hyphen) but for a long time could not say "this word carries on
FROM the previous one" -- so every chunk that began mid-word was read as a fresh word.

Measured on the real songs, this hit any word split by a barline: `lai-ne-hil-le` was
stored as `lai-ne-hil` plus a stray `le`, and the by-system editor then showed the
singer "hil le" where the imported text said "hil-le". The syllables land on the right
notes either way, which is why nothing else caught it.
"""

from lxml import etree

from src.clean_score.lyric_txt import editor_grid, place_lyrics

from .scorebuilder import build_score


def _states(root, staff_id, measure):
    """[(text, syllabic)] for one staff's measure, in note order."""
    staff = [s for s in root.find(".//Score").findall("Staff")
             if s.get("id") == str(staff_id)][0]
    m = staff.findall("Measure")[measure - 1]
    return [(ly.findtext("text"), ly.findtext("syllabic")) for ly in m.iter("Lyrics")]


def test_a_word_split_by_a_barline_stays_one_word():
    root = build_score(staff_ids=(1,), measures=2, chords=3)
    place_lyrics(root, [{"measure_start": 1,
                         "lyrics": [{"parts": ["P1"], "text": "lai-ne-hil-le län-ti"}]}],
                 fmt="json", replace=True)
    # "lai-ne-hil-le" runs out of measure 1 after three notes and finishes in measure 2.
    assert _states(root, 1, 1) == [("lai", "begin"), ("ne", "middle"), ("hil", "middle")]
    assert _states(root, 1, 2) == [("le", "end"), ("län", "begin"), ("ti", "end")]


def test_a_word_carried_in_from_the_previous_block_stays_one_word():
    """The real shape: consecutive JSON blocks, the first ending mid-word."""
    root = build_score(staff_ids=(1,), measures=2, chords=2)
    place_lyrics(root, [{"measure_start": 1,
                         "lyrics": [{"parts": ["P1"], "text": "lai-ne-"}]},
                        {"measure_start": 2,
                         "lyrics": [{"parts": ["P1"], "text": "hil-le"}]}],
                 fmt="json", replace=True)
    assert _states(root, 1, 1) == [("lai", "begin"), ("ne", "middle")]
    assert _states(root, 1, 2) == [("hil", "middle"), ("le", "end")]


def test_a_leading_hyphen_marks_a_continuation_and_is_not_sung():
    """Writing the carried-over line as `-hil-le` is allowed, and means the same thing."""
    root = build_score(staff_ids=(1,), measures=2, chords=2)
    place_lyrics(root, [{"measure_start": 1,
                         "lyrics": [{"parts": ["P1"], "text": "lai-ne-"}]},
                        {"measure_start": 2,
                         "lyrics": [{"parts": ["P1"], "text": "-hil-le"}]}],
                 fmt="json", replace=True)
    assert _states(root, 1, 2) == [("hil", "middle"), ("le", "end")]


def test_a_word_inside_one_measure_is_untouched():
    """The guard: most words never cross a barline and must come through as before."""
    root = build_score(staff_ids=(1,), measures=2, chords=2)
    place_lyrics(root, [{"measure_start": 1,
                         "lyrics": [{"parts": ["P1"], "text": "il-man kuu-ta"}]}],
                 fmt="json", replace=True)
    assert _states(root, 1, 1) == [("il", "begin"), ("man", "end")]
    assert _states(root, 1, 2) == [("kuu", "begin"), ("ta", "end")]


def test_the_editor_reads_back_the_words_that_were_imported():
    """What the by-system editor shows must be the text that went in, or the two
    ways of editing lyrics disagree about the same score."""
    # Two notes to a measure, so the break lands mid-word with two syllables still to
    # come -- the shape that used to read back as "hil le", two words instead of one.
    text = "lai-ne-hil-le"
    root = build_score(staff_ids=(1,), measures=2, chords=2)
    place_lyrics(root, [{"measure_start": 1, "lyrics": [{"parts": ["P1"], "text": text}]}],
                 fmt="json", replace=True)
    assert editor_grid(root, systems=[(1, 2)]).text(0, "P1") == text
