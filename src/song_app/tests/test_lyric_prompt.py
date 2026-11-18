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


def test_a_name_two_parts_share_is_listed_by_staff_id(tmp_path):
    from src.clean_score.tests.scorebuilder import build_score

    path = tmp_path / "s.mscx"
    path.write_bytes(etree.tostring(build_score(staff_ids=(1, 2, 3), measures=1, chords=1,
                                                names={1: "T1", 2: "T2", 3: "T2"})))
    prompt = pipeline.lyric_prompt("p\n", str(path))
    assert 'top staff first: "T1", 2 ("T2"), 3 ("T2").' in prompt
    assert "staff id" in prompt


def test_a_score_that_does_not_parse_gets_the_plain_prompt(tmp_path):
    path = tmp_path / "broken.mscx"
    path.write_text("<museScore><Score>")
    assert pipeline.lyric_prompt("p\n", str(path)) == "p\n"


def _two_tenors_t2_resting_in_bar_2():
    from src.clean_score.tests.scorebuilder import build_score
    root = build_score(staff_ids=(1, 2), measures=3, chords=2, names={1: "T1", 2: "T2"},
                       line_breaks=(1, 2))
    voice = root.findall(".//Score/Staff[@id='2']/Measure")[1].find("voice")
    for chord in voice.findall("Chord"):
        voice.remove(chord)
    rest = etree.SubElement(voice, "Rest")
    etree.SubElement(rest, "durationType").text = "measure"
    etree.SubElement(rest, "duration").text = "4/4"
    return root


SYSTEMS = [(1, 1), (2, 2), (3, 3)]


def _paste(root, *lyrics):
    return lyric_txt.place_lyrics(root, [{"measure_start": 2, "lyrics": list(lyrics)}],
                                  systems=SYSTEMS)


def test_a_part_rests_warning_stays_until_its_own_line_is_sent_again():
    root = _two_tenors_t2_resting_in_bar_2()
    first = _paste(root, {"staff_number": 2, "text": "kak-si", "parts": ["T2"]})
    old = [m.to_dict() for m in first.mismatches]
    assert [w["kind"] for w in old] == ["part_rests"]
    # Another part pasted into the same system does not answer it.
    other = _paste(root, {"staff_number": 1, "text": "yk-si", "parts": ["T1"]})
    assert server._kept_warnings(old, other) == old
    # The same printed line with its name corrected answers it.
    fixed = _paste(root, {"staff_number": 2, "text": "kak-si", "parts": ["T1"]})
    assert server._kept_warnings(old, fixed) == []
    # Sent again unchanged: the new warning stands and the old one is not doubled.
    again = _paste(root, {"staff_number": 2, "text": "kak-si", "parts": ["T2"]})
    assert server._kept_warnings(old, again) == []
    assert [m.kind for m in again.mismatches] == ["part_rests"]


def test_correcting_one_of_two_lines_keeps_the_others_warning():
    root = _two_tenors_t2_resting_in_bar_2()
    first = _paste(root,
                   {"staff_number": 1, "position": "below", "text": "yk-si", "parts": ["T2"]},
                   {"staff_number": 2, "position": "below", "text": "kak-si", "parts": ["T2"]})
    old = [m.to_dict() for m in first.mismatches]
    assert [w["kind"] for w in old] == ["part_rests", "part_rests"]
    fixed = _paste(root, {"staff_number": 2, "position": "below", "text": "kak-si",
                          "parts": ["T1"]})
    kept = server._kept_warnings(old, fixed)
    assert [w["lines"] for w in kept] == [w["lines"] for w in old
                                          if "staff 1" in w["lines"][0]]


def test_an_unknown_part_warning_stays_until_its_own_line_is_sent_again():
    root = _two_tenors_t2_resting_in_bar_2()
    first = _paste(root, {"text": "yk-si", "parts": ["Typo"]})
    old = [m.to_dict() for m in first.mismatches]
    assert [w["kind"] for w in old] == ["unknown_part"]
    other = _paste(root, {"text": "yk-si", "parts": ["T1"]})
    assert server._kept_warnings(old, other) == old
    again = _paste(root, {"text": "yk-si", "parts": ["Typo"]})
    assert server._kept_warnings(old, again) == []



def test_a_pasted_lines_warning_is_answered_by_typing_that_staff_in_the_editor(song):
    """The paste named a part that rests; typing the words into the box of the part
    printed on that staff answers it, and the song moves on to Review (#482)."""
    api, slug, dest = song
    tree = etree.ElementTree(_two_tenors_t2_resting_in_bar_2())
    tree.write(str(dest / CLEANED), encoding="utf-8", xml_declaration=True)
    for name in os.listdir(dest):
        if name.endswith(".systems.json"):
            os.remove(dest / name)
    pasted = api.post(f"/api/songs/{slug}/lyrics", json={"json": json.dumps([
        {"measure_start": 1, "lyrics": [{"staff_number": 1, "text": "ou-ou", "parts": ["T1"]},
                                        {"staff_number": 2, "text": "ii-ii", "parts": ["T2"]}]},
        {"measure_start": 2, "lyrics": [{"staff_number": 1, "text": "yk-si", "parts": ["T2"]}]},
        {"measure_start": 3, "lyrics": [{"staff_number": 1, "text": "aa-aa", "parts": ["T1"]},
                                        {"staff_number": 2, "text": "ee-ee", "parts": ["T2"]}]},
    ])})
    assert pasted.status_code == 200, pasted.text
    assert [w["kind"] for w in pasted.json()["mismatches"]] == ["part_rests"]
    assert json.loads((dest / ".song.json").read_text())["stage"] == "lyrics"
    grid = api.get(f"/api/songs/{slug}/lyric-grid").json()
    keys = [b["key"] for b in grid["boxes"]["1"]]
    assert keys == ["1"]                                 # T2 rests: no box of its own
    typed = api.post(f"/api/songs/{slug}/lyrics", json={"verse": 1, "boxes": {"1": {
        "1": "yk-si"}}})
    assert typed.status_code == 200, typed.text
    assert typed.json()["mismatches"] == []
    assert json.loads((dest / ".song.json").read_text())["stage"] == "review"


def test_typing_another_staff_keeps_the_pasted_lines_warning(song):
    api, slug, dest = song
    tree = etree.ElementTree(_two_tenors_t2_resting_in_bar_2())
    tree.write(str(dest / CLEANED), encoding="utf-8", xml_declaration=True)
    for name in os.listdir(dest):
        if name.endswith(".systems.json"):
            os.remove(dest / name)
    api.post(f"/api/songs/{slug}/lyrics", json={"json": json.dumps([
        {"measure_start": 2, "lyrics": [{"staff_number": 1, "text": "yk-si", "parts": ["T2"]}]},
    ])})
    typed = api.post(f"/api/songs/{slug}/lyrics", json={"verse": 1, "boxes": {"2": {
        "2": "ee-ee"}}})
    assert [w["kind"] for w in typed.json()["mismatches"]] == ["part_rests"]
