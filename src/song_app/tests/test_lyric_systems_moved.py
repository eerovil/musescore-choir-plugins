"""The printed systems the lyric import cuts at, in the cleaned numbering (#446).

The systems' bar labels in `.systems.json` count the bars of the scan. A
``delbar`` or ``insbar`` moves the cleaned score's bars and not those labels, so
the import cut every later system at the wrong bars: on Tonttujen jouluyö two
invented bars in system 6 gave every system from bar 25 on the wrong syllables.
"""
import json

from lxml import etree

from src.clean_score.tests.scorebuilder import build_score
from src.song_app import pdf_systems, pipeline


def bounds(song, ranges):
    pdf_systems.save_bounds(str(song), [
        pdf_systems.SystemBounds(index=i, page=1, top=0.05 * i, bottom=0.05 * i + 0.05,
                                 measure_start=s, measure_end=e)
        for i, (s, e) in enumerate(ranges, 1)])


def fixes(song, *entries):
    (song / "fixes.json").write_text(json.dumps(list(entries)))


def test_tonttujen_jouluyo_after_its_two_delbars(tmp_path):
    bounds(tmp_path, [(17, 20), (21, 26), (27, 30), (31, 34)])
    fixes(tmp_path, {"kind": "delbar", "measure": 25, "from": []},
          {"kind": "delbar", "measure": 25, "from": []})
    assert pipeline._printed_systems(str(tmp_path)) == [
        (17, 20), (21, 24), (25, 28), (29, 32)]


def test_no_fixes_leaves_the_systems_alone(tmp_path):
    bounds(tmp_path, [(1, 4), (5, 8)])
    assert pipeline._printed_systems(str(tmp_path)) == [(1, 4), (5, 8)]


def test_a_deleted_first_bar_starts_the_system_at_the_next():
    assert pipeline.moved_system(5, 8, [("del", 5)]) == (5, 7)
    assert pipeline.moved_system(1, 4, [("del", 5)]) == (1, 4)


def test_a_bar_put_in_counts_in_its_system_and_moves_the_later_ones():
    """Kristallen den fina: a barline the scan lost, put back after bar 3."""
    moves = [("ins", 3)]
    assert pipeline.moved_system(1, 4, moves) == (1, 5)
    assert pipeline.moved_system(5, 8, moves) == (6, 9)


def test_moves_count_in_file_order():
    moves = [("ins", 2), ("del", 6)]
    assert [pipeline.moved_system(s, e, moves) for s, e in [(1, 4), (5, 8)]] == [
        (1, 5), (6, 8)]


def test_a_system_every_bar_of_which_was_taken_out_is_no_system(tmp_path):
    bounds(tmp_path, [(1, 2), (3, 3), (4, 5)])
    fixes(tmp_path, {"kind": "delbar", "measure": 3, "from": []})
    assert pipeline._printed_systems(str(tmp_path)) == [(1, 2), (3, 4)]


def test_the_import_stops_a_line_at_the_moved_system_end(tmp_path):
    """A block with no measure_end runs to its system's end; one cut at the scan's
    numbering spilled into the next system and came back too_many."""
    score = tmp_path / "s.mscx"
    score.write_bytes(etree.tostring(build_score(staff_ids=(1,), measures=4, chords=2,
                                                 names={1: "T1"})))
    bounds(tmp_path, [(1, 3), (4, 5)])
    fixes(tmp_path, {"kind": "delbar", "measure": 2, "from": []})
    lyrics = tmp_path / "lyrics.json"
    lyrics.write_text(json.dumps([
        {"measure_start": 1, "lyrics": [{"text": "a b c d", "parts": ["T1"]}]},
        {"measure_start": 3, "lyrics": [{"text": "e f g h", "parts": ["T1"]}]}]))
    result = pipeline.run_lyric_import(str(lyrics), str(score), song_dir=str(tmp_path))
    assert result.mismatches == []
