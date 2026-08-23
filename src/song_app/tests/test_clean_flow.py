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


def test_clean_without_answers_reports_no_output(song_dir):
    with pytest.raises(RuntimeError, match="no parts declared"):
        pipeline.run_clean(_source(song_dir), song_dir, per_system=True)
