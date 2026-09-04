"""
The song app's clean path: grid answers in, rebuilt score and lyric routing out.

This drives the web adapter (pipeline.system_grid / save_system_answers / run_clean)
over the same per-system module the CLI prompt uses, so the two adapters stay pinned
to one behavior.
"""

import json
import os
import shutil

import pytest
from lxml import etree

from src.clean_score.tests.test_per_system import ANSWERS  # the fixture's reading
from src.clean_score.implode import implode
from src.clean_score.utils.per_system import use_answer_file
from src.song_app import pipeline

FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "clean_score", "tests", "test_files", "laulun_aika.mscx",
)


@pytest.fixture
def song_dir(tmp_path):
    """A song folder holding the fixture, with an answer file of its own."""
    d = tmp_path / "song"
    d.mkdir()
    shutil.copy2(FIXTURE, d / "laulun_aika.mscx")
    with use_answer_file(str(tmp_path / "answers.json")):
        yield str(d)


def _source(song_dir):
    return os.path.join(song_dir, "laulun_aika.mscx")


def test_grid_describes_every_system(song_dir):
    grid = pipeline.system_grid(_source(song_dir))
    assert len(grid) == 7
    assert grid[0]["measure_start"] == 1 and grid[0]["measure_end"] == 6
    assert [s["staff_id"] for s in grid[0]["staves"]] == [1, 2]
    assert grid[0]["staves"][0]["voices"] == 2
    assert all(s["answer"] == "" for sys in grid for s in sys["staves"])
    assert [sys["can_reuse_previous"] for sys in grid[:4]] == [False, True, True, False]


def test_grid_answers_survive_a_reload(song_dir):
    src = _source(song_dir)
    assert not pipeline.has_system_answers(src)
    pipeline.save_system_answers(src, ANSWERS)
    assert pipeline.has_system_answers(src)
    grid = pipeline.system_grid(src)
    assert grid[4]["staves"][0]["answer"] == "T3"


def test_grid_answers_clean_headless_into_parts_and_lyric_routing(song_dir):
    src = _source(song_dir)
    pipeline.save_system_answers(src, ANSWERS)

    cleaned, mscx = pipeline.run_clean(src, song_dir, per_system=True)
    assert mscx == src  # a .mscx source needs no conversion
    assert os.path.exists(cleaned)

    root = etree.parse(cleaned).getroot()
    assert [p.findtext("trackName") for p in root.findall(".//Part")] == ["T1", "T2", "T3", "B"]
    assert len(root.findall(".//Score/Staff")) == 4

    # The lyric importer routes by these; printed numbering shifts per system.
    meta = {m.get("name"): m.text for m in root.findall(".//Score/metaTag")}
    smap = {(e["start"], e["end"]): e["map"] for e in json.loads(meta["lyricsSystemMap"])}
    assert smap[(1, 6)] == {"1": [1, 2], "2": [4]}      # T1+T2 divisi, then B
    assert smap[(26, 29)] == {"1": [1], "2": [2], "3": [4]}  # T3 absent -> printed 3 is B
    assert meta["lyricsStaffMap"] == "1:1;2:2;3:3;4:4"


def test_cleaned_system_map_implodes_divisi_and_later_split_staves(song_dir):
    src = _source(song_dir)
    pipeline.save_system_answers(src, ANSWERS)
    cleaned, _ = pipeline.run_clean(src, song_dir, per_system=True)
    root = etree.parse(cleaned).getroot()

    implode(root)

    staves = root.findall(".//Score/Staff")

    def singing_voices(staff, measure):
        return sum(
            voice.find("Chord") is not None
            for voice in staff.findall("Measure")[measure].findall("voice")
        )

    # m1 prints T1+T2 together above B; the two unused fixed positions hide.
    assert [singing_voices(staff, 0) for staff in staves] == [2, 1, 0, 0]
    # m26 prints T1, T2 and B on three separate staves.
    assert [singing_voices(staff, 25) for staff in staves] == [1, 1, 1, 0]
    # Printed position 2 was the bass staff in the earlier systems. When T3
    # takes that position at m20, its inherited tenor clef must take over too.
    assert (
        staves[1]
        .findall("Measure")[19]
        .findtext("voice/Clef/concertClefType")
        == "G8vb"
    )


def test_clean_without_answers_reports_no_output(song_dir):
    with pytest.raises(RuntimeError, match="no parts declared"):
        pipeline.run_clean(_source(song_dir), song_dir, per_system=True)


# --------------------------------------------------------------------------- #
# The lyric stage: the manual editor's cells go in, structured mismatches come out
# --------------------------------------------------------------------------- #

def _cleaned_song(song_dir):
    """Clean the fixture so there is a score to put lyrics into."""
    src = _source(song_dir)
    pipeline.save_system_answers(src, ANSWERS)
    cleaned, _ = pipeline.run_clean(src, song_dir, per_system=True)
    return cleaned


def test_editor_cells_become_lyrics_in_the_cleaned_score(song_dir):
    from lxml import etree

    from src.clean_score.lyric_txt import blocks_from_cells, editor_grid

    cleaned = _cleaned_song(song_dir)
    grid = editor_grid(etree.parse(cleaned).getroot())
    assert [p.name for p in grid.parts] == ["T1", "T2", "T3", "B"]

    cells = {0: {"T1": "en-sim mäi-nen", "B": "bas-so"}}
    blocks = blocks_from_cells(grid, cells)
    json_path = os.path.join(song_dir, "lyrics.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(blocks, f, ensure_ascii=False)

    result = pipeline.run_lyric_import(json_path, cleaned, replace=True)
    # Mismatches are fields, not text scraped off stderr.
    assert all(hasattr(m, "kind") and m.staff_ids for m in result.mismatches)
    back = editor_grid(etree.parse(cleaned).getroot())
    assert back.text(0, "T1").startswith("en-sim")
    assert back.text(0, "B").startswith("bas-so")


def test_import_reports_a_too_short_line_against_its_own_system(song_dir):
    from lxml import etree

    from src.clean_score.lyric_txt import TOO_FEW, blocks_from_cells, editor_grid

    cleaned = _cleaned_song(song_dir)
    grid = editor_grid(etree.parse(cleaned).getroot())
    system = grid.systems[0]
    blocks = blocks_from_cells(grid, {0: {"T1": "yk"}})  # one syllable for a whole system
    json_path = os.path.join(song_dir, "lyrics.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(blocks, f, ensure_ascii=False)

    result = pipeline.run_lyric_import(json_path, cleaned, replace=True)
    short = [m for m in result.mismatches if m.kind == TOO_FEW]
    assert short, "a one-syllable line over a whole system must be reported"
    m = short[0]
    assert m.measure_start == system.start          # attaches to the system it starts in
    assert m.staff_ids == (1,)                      # T1 is output staff 1
    assert m.syllables == 1 and m.slots > 1
