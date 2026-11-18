"""The AI lyric prompt names the song's parts and asks for them in every line (#482)."""

import json
import os
import shutil

import pytest
from fastapi.testclient import TestClient
from lxml import etree

from src.clean_score import lyric_txt
from src.song_app import pipeline, server, state

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "fixtures", "virta-venhetta-vie")
CLEANED = "Virta-venhetta-vie_cleaned.mscx"


@pytest.fixture
def song(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "SONGS_DIR", str(tmp_path))
    dest = tmp_path / "virta-venhetta-vie"
    dest.mkdir()
    for stage in ("00-registered", "10-cleaned"):
        src = os.path.join(FIXTURE, stage)
        for name in os.listdir(src):
            shutil.copyfile(os.path.join(src, name), dest / name)
    data = json.loads((dest / ".song.json").read_text())
    data.update(cleaned=CLEANED, stage="lyrics")
    (dest / ".song.json").write_text(json.dumps(data))
    return TestClient(server.app), "virta-venhetta-vie", dest


def test_the_prompt_for_a_song_lists_its_part_names(song):
    api, slug, dest = song
    names = [p.name for p in lyric_txt.lyric_parts(etree.parse(str(dest / CLEANED)).getroot())]
    assert len(names) >= 2
    prompt = api.get(f"/api/prompt?slug={slug}").json()["prompt"]
    tail = prompt.split("THIS SONG'S PARTS, top staff first:")[1]
    assert tail.split(".")[0].strip() == ", ".join(f'"{n}"' for n in names)
    assert 'names of the parts that sing that' in prompt


def test_the_prompt_without_a_song_is_the_plain_file(song):
    api, _slug, _dest = song
    prompt = api.get("/api/prompt").json()["prompt"]
    assert "THIS SONG'S PARTS" not in prompt
    assert 'leave "parts": []' in prompt


def test_a_song_not_cleaned_yet_gets_the_plain_prompt():
    assert pipeline.lyric_prompt("the prompt\n", "") == "the prompt\n"
