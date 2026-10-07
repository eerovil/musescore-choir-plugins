"""The staff line in each practice video's YouTube description (#323).

The stemmanauhat site zooms a part's video to `stemmanauha-staff: N/M`. These pin
how N and M are counted (as the video draws the staves), what the uploader writes,
and that the backfill keeps the rest of an existing description.
"""

import json
import os
import re

import pytest

from src.stemmanauha import backfill_staff, staff_lines
from src.stemmanauha import upload_to_youtube as yt

# The site's own pattern, copied from the issue.
SITE_RE = re.compile(r"^stemmanauha-staff:\s*(\d+)\s*/\s*(\d+)\s*$", re.MULTILINE)


def _part(name, staff_id):
    return (f"<Part><Staff id=\"{staff_id}\"/><trackName>{name}</trackName>"
            f"<Instrument><longName>{name}</longName></Instrument></Part>")


def _staff(staff_id, sings=True):
    body = ("<Chord><durationType>whole</durationType><Note><pitch>60</pitch></Note></Chord>"
            if sings else "<Rest><durationType>measure</durationType></Rest>")
    return (f"<Staff id=\"{staff_id}\"><Measure><voice>{body}</voice></Measure>"
            f"<Measure><voice>{body}</voice></Measure></Staff>")


def write_score(path, names, silent=()):
    parts = "".join(_part(n, i) for i, n in enumerate(names, 1))
    staves = "".join(_staff(i, n not in silent) for i, n in enumerate(names, 1))
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"<museScore version=\"3.02\"><Score>{parts}{staves}</Score></museScore>")
    return str(path)


def make_song(tmp_path, slug, names, silent=(), record=None):
    song = tmp_path / slug
    song.mkdir()
    write_score(song / "x_cleaned.mscx", names, silent)
    data = {"name": slug.title(), "slug": slug, "cleaned": "x_cleaned.mscx",
            "record": record or {}}
    (song / ".song.json").write_text(json.dumps(data), encoding="utf-8")
    return str(song)


def test_one_staff_per_part_and_the_click_staff_does_not_count(tmp_path):
    score = write_score(tmp_path / "s.mscx", ["S", "A", "Click"], silent=("Click",))
    assert staff_lines.part_staves(score) == {"S": (1, 2), "A": (2, 2)}


def test_parts_sharing_a_staff_get_the_same_number(tmp_path):
    score = write_score(tmp_path / "s.mscx", ["Solo", "S1-1", "S1-2", "A1"])
    assert staff_lines.part_staves(score, [("S1-1", "S1-2")]) == {
        "Solo": (1, 3), "S1-1": (2, 3), "S1-2": (2, 3), "A1": (3, 3)}


def test_the_song_brings_its_own_shared_staves(tmp_path):
    song = make_song(tmp_path, "meri", ["S1", "S1b", "A1"],
                     record={"renderer": "scroll", "staff_groups": "S1+S1b"})
    assert staff_lines.song_part_staves(song) == {
        "S1": (1, 2), "S1b": (1, 2), "A1": (2, 2)}


def test_a_screen_recording_shares_no_staves(tmp_path):
    song = make_song(tmp_path, "old", ["S1", "S1b", "A1"],
                     record={"renderer": "screen", "staff_groups": "S1+S1b"})
    assert staff_lines.song_part_staves(song)["A1"] == (3, 3)


def test_a_screen_recording_counts_the_silent_staff_it_shows(tmp_path):
    # MuseScore on screen shows the click staff, so the parts under it move down.
    for renderer in ("screen", None):
        song = make_song(tmp_path, f"rec-{renderer}", ["Solo", "Click", "S1", "A1"],
                         silent=("Click",), record={"renderer": renderer})
        assert staff_lines.song_part_staves(song) == {
            "Solo": (1, 4), "Click": (2, 4), "S1": (3, 4), "A1": (4, 4)}
    # The same score rendered scrolling leaves the click staff out.
    song = make_song(tmp_path, "scrolled", ["Solo", "Click", "S1", "A1"],
                     silent=("Click",), record={"renderer": "scroll"})
    assert staff_lines.song_part_staves(song) == {
        "Solo": (1, 3), "S1": (2, 3), "A1": (3, 3)}


def test_a_part_hidden_in_musescore_is_not_on_screen(tmp_path):
    score = write_score(tmp_path / "s.mscx", ["S1", "A1"])
    text = open(score, encoding="utf-8").read().replace(
        "<trackName>S1</trackName>", "<trackName>S1</trackName><show>0</show>", 1)
    open(score, "w", encoding="utf-8").write(text)
    assert staff_lines.part_staves(score, renderer="screen") == {"A1": (1, 1)}


def test_no_cleaned_score_is_no_lines(tmp_path):
    (tmp_path / "empty").mkdir()
    assert staff_lines.song_part_staves(str(tmp_path / "empty")) == {}


def test_line_for_a_part():
    staves = {"Solo": (1, 5), "S1": (2, 5)}
    assert staff_lines.line_for("S1", staves) == "stemmanauha-staff: 2/5"
    assert staff_lines.line_for("solo", staves) == "stemmanauha-staff: 1/5"
    assert staff_lines.line_for("ALL", staves) is None
    assert staff_lines.line_for("Kaikki", staves) is None
    assert staff_lines.line_for("T1", staves) is None


def test_the_site_reads_the_line_and_it_is_written_once():
    once = staff_lines.with_line("Practice track", "stemmanauha-staff: 2/5")
    assert once == "Practice track\nstemmanauha-staff: 2/5"
    assert SITE_RE.findall(once) == [("2", "5")]
    again = staff_lines.with_line(once, "stemmanauha-staff: 3/5")
    assert again == "Practice track\nstemmanauha-staff: 3/5"
    assert staff_lines.with_line("", "stemmanauha-staff: 1/1") == "stemmanauha-staff: 1/1"


def test_upload_writes_the_line_and_leaves_it_off_the_all_video(tmp_path, monkeypatch):
    song = make_song(tmp_path, "meri", ["Solo", "S1-1", "S1-2"],
                     record={"renderer": "scroll", "staff_groups": "S1-1+S1-2"})
    sent = {}
    monkeypatch.setattr(yt, "get_authenticated_service", lambda: object())
    monkeypatch.setattr(yt, "create_playlist", lambda youtube, title: None)

    def fake_upload(youtube, path, title, description, **kw):
        sent[title] = description
        return "id-" + title

    monkeypatch.setattr(yt, "upload_video", fake_upload)
    videos = [os.path.join(song, f"meri {p}.mp4") for p in ("Solo", "S1-2", "ALL")]
    yt.upload_to_youtube(song, videos, log=lambda m: None, display_name="Meri")
    assert sent == {"Meri Solo": "Practice track\nstemmanauha-staff: 1/2",
                    "Meri S1-2": "Practice track\nstemmanauha-staff: 2/2",
                    "Meri ALL": "Practice track"}


def _uploaded(tmp_path):
    return make_song(tmp_path, "kaipaava", ["S1", "A1"], record={
        "renderer": "scroll",
        "uploads": [{"title": "Kaipaava S1", "part": "S1", "video_id": "v1"},
                    {"title": "Kaipaava A1", "video_id": "v2"},  # no stored part
                    {"title": "Kaipaava ALL", "part": "ALL", "video_id": "v3"}]})


def test_backfill_dry_run_prints_and_never_logs_in(tmp_path, monkeypatch):
    _uploaded(tmp_path)

    def no_login():
        raise AssertionError("dry run signed in")

    monkeypatch.setattr(yt, "get_authenticated_service", no_login)
    lines = []
    backfill_staff.run(str(tmp_path), dry_run=True, out=lines.append)
    assert lines[:2] == ["Kaipaava S1 → stemmanauha-staff: 1/2",
                         "Kaipaava A1 → stemmanauha-staff: 2/2"]
    assert lines[2].startswith("Kaipaava ALL → (skipped")


class FakeVideos:
    def __init__(self, store):
        self.store = store
        self.updates = []

    def list(self, part, id):
        return _Req(lambda: {"items": [{"snippet": dict(self.store[id])}]})

    def update(self, part, body):
        self.updates.append(body)
        self.store[body["id"]] = body["snippet"]
        return _Req(lambda: body)


class _Req:
    def __init__(self, fn):
        self.execute = fn


def test_backfill_keeps_the_description_and_category(tmp_path, monkeypatch):
    _uploaded(tmp_path)
    store = {
        "v1": {"title": "Kaipaava S1", "description": "Practice track\nhello",
               "categoryId": "10"},
        "v2": {"title": "Kaipaava A1", "categoryId": "10",
               "description": "Practice track\nstemmanauha-staff: 2/2"},
    }
    videos = FakeVideos(store)
    monkeypatch.setattr(yt, "get_authenticated_service",
                        lambda: type("YT", (), {"videos": lambda self: videos})())
    assert backfill_staff.run(str(tmp_path), dry_run=False, out=lambda m: None) == 1
    assert store["v1"] == {"title": "Kaipaava S1", "categoryId": "10",
                           "description": "Practice track\nhello\nstemmanauha-staff: 1/2"}
    assert [u["id"] for u in videos.updates] == ["v1"]  # v2 already said it
