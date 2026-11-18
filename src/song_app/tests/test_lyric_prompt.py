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
    other = _paste(root, {"staff_number": 2, "text": "yk-si", "parts": ["T1"]})
    assert server._kept_warnings(old, other) == old
    # Nor does the line with its name corrected: that is another line now, and only
    # the person can say the two are one (#482, Dismiss).
    fixed = _paste(root, {"staff_number": 2, "text": "kak-si", "parts": ["T1"]})
    assert server._kept_warnings(old, fixed) == old
    # Sent again as written: the new warning stands and the old one is not doubled.
    again = _paste(root, {"staff_number": 2, "text": "kak-si", "parts": ["T2"]})
    assert server._kept_warnings(old, again) == []
    assert [m.kind for m in again.mismatches] == ["part_rests"]
    assert again.mismatches[0].to_dict()["id"] == old[0]["id"]


def test_a_two_lines_warning_stays_until_either_line_is_sent_again():
    """A staff_number line set aside for a named one on the same part (#482) is said
    until either line is pasted again, the named one alone included."""
    root = _two_tenors_t2_resting_in_bar_2()
    first = _paste(root, {"staff_number": 1, "text": "yk-si"},
                   {"staff_number": 2, "text": "kak-si", "parts": ["T1"]})
    old = [m.to_dict() for m in first.mismatches]
    assert [w["kind"] for w in old] == ["two_lines"]
    other = _paste(root, {"staff_number": 2, "verse": 2, "text": "zz", "parts": ["T1"]})
    assert server._kept_warnings(old, other) == old
    resent = _paste(root, {"staff_number": 1, "text": "yk-si"})
    assert server._kept_warnings(old, resent) == []
    # The usual correction: the extra line taken out and the named one pasted alone.
    named_alone = _paste(root, {"staff_number": 2, "text": "kak-si", "parts": ["T1"]})
    assert named_alone.mismatches == []
    assert server._kept_warnings(old, named_alone) == []


def test_two_wrongly_named_lines_are_two_warnings_neither_answered_by_a_correction():
    root = _two_tenors_t2_resting_in_bar_2()
    first = _paste(root,
                   {"staff_number": 1, "position": "below", "text": "yk-si", "parts": ["T2"]},
                   {"staff_number": 2, "position": "below", "text": "kak-si", "parts": ["T2"]})
    old = [m.to_dict() for m in first.mismatches]
    # Two lines naming one resting part, both as written: the import joins them, so
    # they are one line and one warning.
    assert [w["kind"] for w in old] == ["part_rests"]
    fixed = _paste(root, {"staff_number": 2, "position": "below", "text": "kak-si",
                          "parts": ["T1"]})
    assert server._kept_warnings(old, fixed) == old


def test_an_unknown_part_warning_is_answered_by_its_line_sent_again_as_written():
    root = _two_tenors_t2_resting_in_bar_2()
    first = _paste(root, {"text": "yk-si", "parts": ["Typo"]})
    old = [m.to_dict() for m in first.mismatches]
    assert [w["kind"] for w in old] == ["unknown_part"]
    corrected = _paste(root, {"text": "yk-si", "parts": ["T1"]})
    assert server._kept_warnings(old, corrected) == old
    again = _paste(root, {"text": "yk-si", "parts": ["Typo"]})
    assert server._kept_warnings(old, again) == []


def test_two_misspelt_names_are_two_ids():
    root = _two_tenors_t2_resting_in_bar_2()
    first = _paste(root, {"text": "a", "parts": ["TypoA"]}, {"text": "b", "parts": ["TypoB"]})
    old = [m.to_dict() for m in first.mismatches]
    assert [w["kind"] for w in old] == ["unknown_part", "unknown_part"]
    assert old[0]["id"] != old[1]["id"]
    corrected = _paste(root, {"text": "a", "parts": ["T1"]})
    assert server._kept_warnings(old, corrected) == old


def test_the_id_differs_by_kind_and_names_the_part_as_written():
    a = lyric_txt._line_warning_id("part_rests", ("m2 verse 1 voice 1 parts T2",), 2, 2)
    b = lyric_txt._line_warning_id("two_lines", ("m2 verse 1 voice 1 parts T2",), 2, 2)
    c = lyric_txt._line_warning_id("part_rests", ("m2 verse 1 voice 1 parts T1",), 2, 2)
    assert len({a, b, c}) == 3
    assert lyric_txt._line_ids(2, 1, 1, {"staff_number": 2, "parts": ["T2"]}, ["T2"]) == (
        '{"m":2,"parts":["T2"],"verse":1,"voice":1}',)
    assert lyric_txt._line_ids(2, 1, 1, {"staff_number": 2, "position": "below"}, []) == (
        '{"m":2,"position":"below","staff":2,"verse":1,"voice":1}',)
    # Types and list items are kept: a part name "2" is not staff 2 (review of #505).
    assert len({lyric_txt._line_ids(2, 1, 1, {}, p)
                for p in (["2"], [2], ["a,b"], ["a", "b"])}) == 4


def test_warning_id_keeps_a_stored_id_and_is_stable_for_an_old_warning():
    assert lyric_txt.warning_id({"id": "abc", "kind": "too_few"}) == "abc"
    old = {"kind": "too_few", "staff_ids": [1], "measure_start": 3, "measure_end": 4,
           "verse": 1, "voice": 1, "message": "Measures 3-4: ..."}
    assert lyric_txt.warning_id(old) == lyric_txt.warning_id(dict(old))
    assert lyric_txt.warning_id(old) != lyric_txt.warning_id({**old, "measure_start": 2})


def _two_tenor_song(dest):
    tree = etree.ElementTree(_two_tenors_t2_resting_in_bar_2())
    tree.write(str(dest / CLEANED), encoding="utf-8", xml_declaration=True)
    for name in os.listdir(dest):
        if name.endswith(".systems.json"):
            os.remove(dest / name)


def _post_blocks(api, slug, blocks):
    r = api.post(f"/api/songs/{slug}/lyrics", json={"json": json.dumps(blocks)})
    assert r.status_code == 200, r.text
    return r.json()


def _saved(dest):
    return json.loads((dest / ".song.json").read_text())


FULL = [
    {"measure_start": 1, "lyrics": [{"staff_number": 1, "text": "ou-ou", "parts": ["T1"]},
                                    {"staff_number": 2, "text": "ii-ii", "parts": ["T2"]}]},
    {"measure_start": 3, "lyrics": [{"staff_number": 1, "text": "aa-aa", "parts": ["T1"]},
                                    {"staff_number": 2, "text": "ee-ee", "parts": ["T2"]}]},
]


def test_typing_the_staff_does_not_answer_a_resting_parts_warning_but_dismiss_does(song):
    """The paste named a part that rests. Typing the box of the part that sings there
    is another line, so the warning stays; Dismiss takes it and the song moves on."""
    api, slug, dest = song
    _two_tenor_song(dest)
    pasted = _post_blocks(api, slug, FULL[:1] + [
        {"measure_start": 2, "lyrics": [{"staff_number": 1, "text": "yk-si", "parts": ["T2"]}]},
    ] + FULL[1:])
    assert [w["kind"] for w in pasted["mismatches"]] == ["part_rests"]
    assert _saved(dest)["stage"] == "lyrics"
    grid = api.get(f"/api/songs/{slug}/lyric-grid").json()
    assert [b["key"] for b in grid["boxes"]["1"]] == ["1"]   # T2 rests: no box
    typed = api.post(f"/api/songs/{slug}/lyrics", json={"verse": 1, "boxes": {"1": {
        "1": "yk-si"}}})
    assert typed.status_code == 200, typed.text
    [warning] = typed.json()["mismatches"]
    assert warning["kind"] == "part_rests" and warning["id"]
    shown = api.get(f"/api/songs/{slug}").json()["lyrics"]["warnings"]
    assert [w["id"] for w in shown] == [warning["id"]]
    gone = api.post(f"/api/songs/{slug}/lyrics/warnings/dismiss", json={"id": warning["id"]})
    assert gone.status_code == 200, gone.text
    assert _saved(dest)["stage"] == "review"
    assert _saved(dest)["lyrics"]["warnings"] == []
    again = api.post(f"/api/songs/{slug}/lyrics/warnings/dismiss", json={"id": warning["id"]})
    assert again.status_code == 404


def test_a_two_lines_warning_is_answered_by_typing_its_staff_for_its_verse_and_bars(song):
    api, slug, dest = song
    _two_tenor_song(dest)
    pasted = _post_blocks(api, slug, FULL[:1] + [
        {"measure_start": 2, "lyrics": [{"staff_number": 1, "text": "a b"},
                                        {"staff_number": 2, "text": "c d", "parts": ["T1"]}]},
    ] + FULL[1:])
    assert [w["kind"] for w in pasted["mismatches"]] == ["two_lines"]
    # T1's box in another system, or T1's verse 2 box in this one, does not answer it.
    other = api.post(f"/api/songs/{slug}/lyrics", json={"verse": 1, "boxes": {"2": {"1": "oo oo"}}})
    assert [w["kind"] for w in other.json()["mismatches"]] == ["two_lines"]
    verse2 = api.post(f"/api/songs/{slug}/lyrics", json={"verse": 2, "boxes": {"1": {"1": "x y"}}})
    assert [w["kind"] for w in verse2.json()["mismatches"]] == ["two_lines"]
    # An emptied box is a write: the person says there are no words there.
    typed = api.post(f"/api/songs/{slug}/lyrics", json={"verse": 1, "boxes": {"1": {"1": ""}}})
    assert typed.status_code == 200, typed.text
    assert typed.json()["mismatches"] == []
    assert _saved(dest)["stage"] == "review"


def test_a_two_lines_warning_is_answered_through_the_api_and_the_song_reaches_review(song):
    """Post both clashing lines, then the named line alone; no warning is kept and
    the song moves on to Review (#482)."""
    api, slug, dest = song
    _two_tenor_song(dest)
    pasted = _post_blocks(api, slug, FULL[:1] + [
        {"measure_start": 2, "lyrics": [{"staff_number": 1, "text": "a b"},
                                        {"staff_number": 2, "text": "c d", "parts": ["T1"]}]},
    ] + FULL[1:])
    assert [w["kind"] for w in pasted["mismatches"]] == ["two_lines"]
    assert _saved(dest)["stage"] == "lyrics"
    fixed = _post_blocks(api, slug, [
        {"measure_start": 2, "lyrics": [{"staff_number": 2, "text": "c d", "parts": ["T1"]}]},
    ])
    assert fixed["mismatches"] == []
    data = _saved(dest)
    assert data["stage"] == "review"
    assert data["lyrics"]["warnings"] == []


def test_a_wrong_name_corrected_stays_until_dismissed(song):
    api, slug, dest = song
    _two_tenor_song(dest)
    first = _post_blocks(api, slug, [
        {"measure_start": 1, "lyrics": [{"parts": ["T1"], "text": "ou-ou"},
                                        {"parts": ["T2"], "text": "ii-ii"}]},
        {"measure_start": 2, "lyrics": [{"parts": ["Typo"], "text": "a b"}]},
        {"measure_start": 3, "lyrics": [{"parts": ["T1"], "text": "aa-aa"},
                                        {"parts": ["T2"], "text": "ee-ee"}]},
    ])
    assert [w["kind"] for w in first["mismatches"]] == ["unknown_part"]
    fixed = _post_blocks(api, slug, [{"measure_start": 2, "lyrics": [{"parts": ["T1"], "text": "a b"}]}])
    [left] = fixed["mismatches"]
    assert left["kind"] == "unknown_part"
    assert _saved(dest)["stage"] == "lyrics"
    api.post(f"/api/songs/{slug}/lyrics/warnings/dismiss", json={"id": left["id"]})
    assert _saved(dest)["stage"] == "review"


def test_an_old_warning_without_an_id_can_be_dismissed(song):
    """A `too_few` saved before ids existed gets one on reading, and dismissing the
    last warning moves a song on Lyrics to Review."""
    api, slug, dest = song
    fp = state.file_fingerprint(str(dest / CLEANED))
    data = _saved(dest)
    old = {"kind": "too_few", "message": "Measures 3-4: too few", "measure_start": 3,
           "measure_end": 4, "staff_ids": [1], "verse": 1, "voice": 1}
    data["lyrics"] = {"json": "lyrics.json", "imported_against": fp, "warnings": [old]}
    (dest / ".song.json").write_text(json.dumps(data))
    [shown] = api.get(f"/api/songs/{slug}").json()["lyrics"]["warnings"]
    assert shown["id"] == lyric_txt.warning_id(old)
    assert "id" not in _saved(dest)["lyrics"]["warnings"][0]
    assert api.post(f"/api/songs/{slug}/lyrics/warnings/dismiss",
                    json={"id": shown["id"]}).status_code == 200
    assert _saved(dest)["stage"] == "review"


def test_a_warning_saved_as_a_plain_sentence_can_be_dismissed(song):
    """Songs imported before warnings had fields hold the sentence as a string. It is
    sent with an id, and Dismiss takes it off like any other (review on #505)."""
    api, slug, dest = song
    fp = state.file_fingerprint(str(dest / CLEANED))
    data = _saved(dest)
    text = "Measures 3-4 (staffs 1): too few tokens (2 syllables, 5 slots)"
    other = "Measures 5-6 (staffs 2): too many tokens (6 syllables, 5 slots)"
    data["lyrics"] = {"json": "lyrics.json", "imported_against": fp, "warnings": [text, other]}
    (dest / ".song.json").write_text(json.dumps(data))
    shown = api.get(f"/api/songs/{slug}").json()["lyrics"]["warnings"]
    assert [w["message"] for w in shown] == [text, other]
    assert shown[0]["id"] != shown[1]["id"]
    assert shown[0]["id"] == lyric_txt.warning_id(text)
    assert api.post(f"/api/songs/{slug}/lyrics/warnings/dismiss",
                    json={"id": shown[0]["id"]}).status_code == 200
    assert _saved(dest)["lyrics"]["warnings"] == [other]
    assert _saved(dest)["stage"] == "lyrics"
    api.post(f"/api/songs/{slug}/lyrics/warnings/dismiss", json={"id": shown[1]["id"]})
    assert _saved(dest)["stage"] == "review"


def test_a_dismiss_on_a_stale_score_or_another_stage_does_not_move_the_stage(song):
    api, slug, dest = song
    w = {"kind": "too_few", "message": "m", "measure_start": 3, "staff_ids": [1]}
    for stage, against in (("lyrics", "an older score"), ("fix", None)):
        data = _saved(dest)
        fp = against or state.file_fingerprint(str(dest / CLEANED))
        data.update(stage=stage, lyrics={"imported_against": fp, "warnings": [w]})
        (dest / ".song.json").write_text(json.dumps(data))
        r = api.post(f"/api/songs/{slug}/lyrics/warnings/dismiss",
                     json={"id": lyric_txt.warning_id(w)})
        assert r.status_code == 200
        assert _saved(dest)["stage"] == stage
        assert _saved(dest)["lyrics"]["warnings"] == []


def test_a_corrected_paste_with_one_block_fewer_keeps_the_shifted_blocks_warnings(song):
    """Null bars take their system's first bar by position, so leaving a block out
    shifts the later ones: their old warnings stay, their new ones are raised."""
    api, slug, dest = song
    _two_tenor_song(dest)
    first = _post_blocks(api, slug, [
        {"measure_start": None, "lyrics": [{"parts": ["T1"], "text": "ou-ou"}]},
        {"measure_start": None, "lyrics": [{"parts": ["T2"], "text": "a b"}]},
        {"measure_start": None, "lyrics": [{"parts": ["T1"], "text": "aa-aa"}]},
    ])
    [rests] = first["mismatches"]
    assert rests["kind"] == "part_rests" and rests["measure_start"] == 2
    second = _post_blocks(api, slug, [
        {"measure_start": None, "lyrics": [{"parts": ["T2"], "text": "a b"}]},
        {"measure_start": None, "lyrics": [{"parts": ["T2"], "text": "c d"}]},
    ])
    kinds = sorted((w["kind"], w["measure_start"]) for w in second["mismatches"])
    assert ("part_rests", 2) in kinds
    assert rests["id"] in [w["id"] for w in second["mismatches"]]


def test_a_wrong_name_is_not_answered_by_a_line_for_a_part_the_paste_already_had(song):
    """Sending again only the line the paste placed keeps the misspelt one's warning."""
    api, slug, dest = song
    _two_tenor_song(dest)
    first = _post_blocks(api, slug, [
        {"measure_start": 2, "lyrics": [{"parts": ["T1"], "text": "a b"},
                                        {"parts": ["Typo"], "text": "x"}]},
    ])
    assert [w["kind"] for w in first["mismatches"]] == ["unknown_part"]
    again = _post_blocks(api, slug, [{"measure_start": 2, "lyrics": [{"parts": ["T1"], "text": "a b"}]}])
    assert [w["kind"] for w in again["mismatches"]] == ["unknown_part"]
    assert [w["kind"] for w in _saved(dest)["lyrics"]["warnings"]] == ["unknown_part"]
