"""What the Publish panel saves: the tempo, the tempo changes and the credits (#400).

Cleaning keeps no tempo marks and a scan never had any, so a song that changes
speed on the page played at one speed. The panel keeps a list of changes beside
the opening BPM (#387, moved from the Record panel by #400), and the composer,
lyricist and arranger the site shows. Here: how each is checked and kept, that a
publish is handed them, and that a song saved before #400 reads its old Record
settings. The marks themselves and MuseScore's clock are pinned in
`src/score_export/tests`.
"""

import json
import os
import time

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from src.song_app import publish, server, state  # noqa: E402
from src.song_app.tests.test_publish import FakeCloudflare, _bundle  # noqa: E402

CHANGES = [{"measure": 13, "bpm": 112}, {"measure": 23, "bpm": "start"},
           {"measure": 34, "bpm": 132}]


@pytest.fixture(autouse=True)
def _no_real_deck(monkeypatch):
    monkeypatch.delenv("AGENTDECK_API_URL", raising=False)
    monkeypatch.delenv("AGENTDECK_URL", raising=False)


@pytest.fixture
def song(tmp_path, monkeypatch):
    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    s = state.create("My Song", per_system=False)
    cleaned = s.path("mysong_cleaned.mscx")
    bar = "<Measure><voice><Chord><durationType>whole</durationType>" \
          "<Note><pitch>60</pitch></Note></Chord></voice></Measure>"
    with open(cleaned, "w") as fh:
        fh.write('<museScore><Score><metaTag name="composer">Toivo Kuula</metaTag>'
                 '<Part><Staff id="1"/><trackName>T1</trackName>'
                 f'</Part><Staff id="1">{bar * 40}</Staff></Score></museScore>')
    s.data["cleaned"] = os.path.basename(cleaned)
    s.data["stage"] = "publish"
    s.data["review"] = {"approved_against": state.file_fingerprint(cleaned)}
    s.save()
    return s


@pytest.fixture
def client(song):
    return TestClient(server.app)


def _saved(song):
    return state.load(song.slug).data.get("publish", {})


def test_the_changes_are_kept_sorted_by_bar(client, song):
    response = client.post(f"/api/songs/{song.slug}/publish-settings",
                           json={"tempo_changes": list(reversed(CHANGES))})

    assert response.status_code == 200
    assert _saved(song)["tempo_changes"] == CHANGES
    assert response.json()["publish"]["tempo_changes"] == CHANGES


def test_the_same_list_as_text_is_read_the_same(client, song):
    client.post(f"/api/songs/{song.slug}/publish-settings",
                json={"tempo_changes": json.dumps(CHANGES)})
    assert _saved(song)["tempo_changes"] == CHANGES


def test_whole_numbers_written_as_floats_or_text_are_read(client, song):
    client.post(f"/api/songs/{song.slug}/publish-settings",
                json={"tempo_changes": [{"measure": 13.0, "bpm": "112"}]})
    assert _saved(song)["tempo_changes"] == [{"measure": 13, "bpm": 112}]


def test_saving_other_settings_keeps_the_changes(client, song):
    client.post(f"/api/songs/{song.slug}/publish-settings", json={"tempo_changes": CHANGES})
    client.post(f"/api/songs/{song.slug}/publish-settings", json={"bpm": 96})
    assert _saved(song)["tempo_changes"] == CHANGES


@pytest.mark.parametrize("bad, message", [
    ([{"measure": 1, "bpm": 100}], "bar 1"),
    ([{"measure": 41, "bpm": 100}], "to 40"),
    ([{"measure": 5, "bpm": 10}], "between 20 and 300"),
    ([{"measure": 5, "bpm": "fast"}], "whole number"),
    ([{"bpm": 100}], "whole bar number"),
    ([{"measure": 13.5, "bpm": 100}], "whole bar number"),
    ([{"measure": "13.5", "bpm": 100}], "whole bar number"),
    ([{"measure": True, "bpm": 100}], "whole bar number"),
    ([{"measure": 13, "bpm": 112.5}], "whole number"),
    ([{"measure": 13, "bpm": "112.5"}], "whole number"),
    ([{"measure": 5, "bpm": 90}, {"measure": 5, "bpm": 100}], "two tempo changes"),
    ("not json", "not readable"),
])
def test_a_change_musescore_could_not_play_is_refused(client, song, bad, message):
    response = client.post(f"/api/songs/{song.slug}/publish-settings",
                           json={"tempo_changes": bad})
    assert response.status_code == 400
    assert message in response.json()["detail"]
    assert _saved(song) == {}


def test_a_score_without_its_own_tempo_keeps_a_bpm(client, song):
    client.post(f"/api/songs/{song.slug}/publish-settings", json={"bpm": 96})
    assert _saved(song)["bpm"] == 96
    refused = client.post(f"/api/songs/{song.slug}/publish-settings", json={"bpm": 500})
    assert refused.status_code == 400 and "between 20 and 300" in refused.json()["detail"]


def test_the_credits_start_from_the_score_and_are_kept_once_edited(client, song):
    data = client.get(f"/api/songs/{song.slug}").json()
    assert data["score_credits"] == {"composer": "Toivo Kuula"}
    assert "credits" not in _saved(song)

    client.post(f"/api/songs/{song.slug}/publish-settings",
                json={"credits": {"composer": "  Toivo   Kuula ", "lyricist": "Eino Leino",
                                  "arranger": ""}})
    assert _saved(song)["credits"] == {"composer": "Toivo Kuula", "lyricist": "Eino Leino",
                                       "arranger": ""}
    # Saving the tempo alone keeps them.
    client.post(f"/api/songs/{song.slug}/publish-settings", json={"tempo_changes": CHANGES})
    assert _saved(song)["credits"]["lyricist"] == "Eino Leino"


@pytest.mark.parametrize("bad, message", [
    ("Kuula", "composer, lyricist and arranger"),
    ({"composer": 5}, "must be text"),
    ({"arranger": "x" * 201}, "longer than 200"),
])
def test_credits_that_cannot_be_shown_are_refused(client, song, bad, message):
    response = client.post(f"/api/songs/{song.slug}/publish-settings", json={"credits": bad})
    assert response.status_code == 400 and message in response.json()["detail"]
    assert "credits" not in _saved(song)


def _publish(client, song, monkeypatch, tmp_path, body):
    for key, value in (("CLOUDFLARE_ACCOUNT_ID", "acct"), ("CLOUDFLARE_API_TOKEN", "tok"),
                       ("STEMMANAUHAT_D1_DATABASE_ID", "db1")):
        monkeypatch.setenv(key, value)
    cf = FakeCloudflare()
    monkeypatch.setattr(publish.urllib.request, "urlopen", cf)
    seen = {}

    def fake_bundle(cleaned, out_dir, *, initial_bpm=None, tempo_changes=None,
                    part_names=None, staff_groups=None, log=None):
        seen.update(initial_bpm=initial_bpm, tempo_changes=tempo_changes)
        if staff_groups is not None or "staff_groups" in seen:
            seen["staff_groups"] = staff_groups
        if part_names is not None:
            seen["part_names"] = part_names
        return _bundle(tmp_path)
    monkeypatch.setattr(publish, "build_bundle", fake_bundle)
    assert client.post(f"/api/songs/{song.slug}/publish", json=body).status_code == 200
    for _ in range(250):
        data = client.get(f"/api/songs/{song.slug}").json()
        if data["jobs"].get("publish", {}).get("status") != "running":
            break
        time.sleep(0.02)
    assert data["jobs"]["publish"]["status"] == "succeeded", data["jobs"]["publish"]
    return cf, seen, data


def test_a_publish_is_handed_the_tempo_and_the_credits_it_saved(client, song, monkeypatch,
                                                                tmp_path):
    cf, seen, data = _publish(client, song, monkeypatch, tmp_path, {
        "choir": "jm", "settings": {"bpm": 72, "tempo_changes": CHANGES,
                                    "credits": {"composer": "Kuula", "lyricist": "Leino"}}})
    assert seen == {"initial_bpm": 72, "tempo_changes": CHANGES}
    assert cf.credits("jm", song.slug) == ("Kuula", "Leino", None)
    # What was published is what the panel now shows.
    assert data["publish"]["bpm"] == 72 and data["publish"]["tempo_changes"] == CHANGES
    assert data["publish"]["sites"]["jm"]["credits"] == {"composer": "Kuula", "lyricist": "Leino"}


def test_a_publish_with_no_saved_credits_uses_the_scores(client, song, monkeypatch, tmp_path):
    cf, seen, _data = _publish(client, song, monkeypatch, tmp_path, {"choir": "jm"})
    assert seen["initial_bpm"] == publish.DEFAULT_BPM
    assert cf.credits("jm", song.slug) == ("Toivo Kuula", None, None)


def test_a_refused_setting_publishes_nothing(client, song, monkeypatch, tmp_path):
    for key, value in (("CLOUDFLARE_ACCOUNT_ID", "acct"), ("CLOUDFLARE_API_TOKEN", "tok"),
                       ("STEMMANAUHAT_D1_DATABASE_ID", "db1")):
        monkeypatch.setenv(key, value)
    reply = client.post(f"/api/songs/{song.slug}/publish",
                        json={"choir": "jm", "settings": {"bpm": 5}})
    assert reply.status_code == 400
    assert "publish" not in client.get(f"/api/songs/{song.slug}").json()["jobs"]


def test_a_song_saved_on_the_old_record_or_upload_stage_reads_as_publish(song):
    data = state.load(song.slug).data
    data["stage"] = "upload"
    data["record"] = {"bpm": 66, "tempo_changes": CHANGES, "outputs": ["My Song T1.mp4"],
                      "uploads": [{"video_id": "x"}]}
    data.pop("publish", None)
    state.Song(song.slug, data).save()

    old = state.load(song.slug)
    assert old.stage == "publish"
    assert old.data["publish"]["bpm"] == 66
    assert old.data["publish"]["tempo_changes"] == CHANGES
    # The rest of the old record stays in the file, untouched.
    assert old.data["record"]["uploads"] == [{"video_id": "x"}]

    data["stage"] = "record"
    data["publish"] = {"bpm": 90}
    state.Song(song.slug, data).save()
    newer = state.load(song.slug)
    assert newer.stage == "publish" and newer.data["publish"]["bpm"] == 90


def test_settings_are_not_saved_while_a_publish_runs(client, song):
    from src.song_app import job_state
    job_state.start(song.dir, "publish")
    reply = client.post(f"/api/songs/{song.slug}/publish-settings", json={"bpm": 90})
    assert reply.status_code == 409
    assert "bpm" not in _saved(song)


def test_a_song_published_before_one_record_per_choir_still_counts_as_published(client, song):
    """#384 moved to one record per choir; a song published before kept `site`."""
    data = state.load(song.slug).data
    data["publish"] = {"choir": "jm", "site": {"choir": "jm", "files": [], "parts": ["T1"]}}
    state.Song(song.slug, data).save()

    assert state.load(song.slug).to_summary()["published"] == ["jm"]
    assert client.get(f"/api/songs/{song.slug}").json()["published"] == ["jm"]
    [row] = [s for s in client.get("/api/songs").json() if s["slug"] == song.slug]
    assert row["published"] == ["jm"]


def test_a_song_never_published_counts_as_published_nowhere(client, song):
    assert state.load(song.slug).to_summary()["published"] == []
    assert client.get(f"/api/songs/{song.slug}").json()["published"] == []


# ---------------------------------------------------------------------------
# Part names (#402): cleaning names parts by guessing from pitch
# ---------------------------------------------------------------------------
def test_the_panel_is_offered_the_scores_part_names_until_some_are_saved(client, song):
    assert client.get(f"/api/songs/{song.slug}").json()["publish_parts"] == \
        {"names": ["T1"], "saved": False, "stale": False}
    reply = client.post(f"/api/songs/{song.slug}/publish-settings",
                        json={"part_names": ["  Sopraano  "]})
    assert reply.status_code == 200
    assert reply.json()["publish_parts"] == {"names": ["Sopraano"], "saved": True,
                                             "stale": False}
    assert _saved(song)["part_names"] == ["Sopraano"]


@pytest.mark.parametrize("names, message", [
    ([""], "Part 1 has no name"),
    (["S1", "S1"], "Two parts are called S1"),
    (["S1", "A1"], "The score now has 1 parts"),
    ("S1", "must be a list"),
    ([3], "must be text"),
])
def test_bad_part_names_are_refused(client, song, names, message):
    reply = client.post(f"/api/songs/{song.slug}/publish-settings", json={"part_names": names})
    assert reply.status_code == 400 and message in reply.json()["detail"]
    assert "part_names" not in _saved(song)


def test_saved_names_for_another_number_of_parts_are_said_and_refused(client, song):
    data = state.load(song.slug)
    data.data["publish"] = {"part_names": ["S1", "A1"]}
    data.save()
    parts = client.get(f"/api/songs/{song.slug}").json()["publish_parts"]
    assert parts["stale"] and parts["names"] == ["T1"]
    assert parts["message"].startswith("The score now has 1 parts")
    reply = client.post(f"/api/songs/{song.slug}/publish-settings", json={})
    assert reply.status_code == 400 and "check the part names" in reply.json()["detail"]


def test_a_publish_is_handed_the_part_names(client, song, monkeypatch, tmp_path):
    _cf, seen, _data = _publish(client, song, monkeypatch, tmp_path, {
        "choir": "naiskuoro", "settings": {"part_names": ["A2"]}})
    assert seen["part_names"] == ["A2"]


def test_a_publish_with_stale_part_names_is_refused_until_they_are_saved(
        client, song, monkeypatch, tmp_path):
    """Review of #402: the panel offers the score's own names when the saved ones
    no longer fit, and a Publish must not take those as checked."""
    data = state.load(song.slug)
    data.data["publish"] = {"part_names": ["S1", "A1"]}
    data.save()
    for key, value in (("CLOUDFLARE_ACCOUNT_ID", "acct"), ("CLOUDFLARE_API_TOKEN", "tok"),
                       ("STEMMANAUHAT_D1_DATABASE_ID", "db1")):
        monkeypatch.setenv(key, value)
    reply = client.post(f"/api/songs/{song.slug}/publish",
                        json={"choir": "naiskuoro", "settings": {"part_names": ["T1"]}})
    assert reply.status_code == 409
    assert "check the part names" in reply.json()["detail"]
    assert _saved(song)["part_names"] == ["S1", "A1"]
    assert "publish" not in client.get(f"/api/songs/{song.slug}").json()["jobs"]

    assert client.post(f"/api/songs/{song.slug}/publish-settings",
                       json={"part_names": ["A2"]}).status_code == 200
    _cf, seen, _data = _publish(client, song, monkeypatch, tmp_path, {"choir": "naiskuoro"})
    assert seen["part_names"] == ["A2"]


# ---------------------------------------------------------------------------
# Shared staves (#420): above five parts the site draws them as the page prints them
# ---------------------------------------------------------------------------
@pytest.fixture
def six(song, tmp_path):
    """The song's cleaned score with six parts: S1, S1b, S2, S2b, A1, A2."""
    from src.song_app.tests.test_publish import _six_parts
    with open(_six_parts(tmp_path), "rb") as src, open(song.cleaned_path(), "wb") as dst:
        dst.write(src.read())
    data = state.load(song.slug)
    data.data["review"] = {"approved_against": state.file_fingerprint(song.cleaned_path())}
    data.save()
    return song


def test_a_song_of_five_parts_or_fewer_is_not_offered_shared_staves(client, song):
    assert not client.get(f"/api/songs/{song.slug}").json()["publish_staff_groups"]["shown"]


def test_the_panel_is_offered_the_pages_pairing_until_one_is_saved(client, six):
    shared = client.get(f"/api/songs/{six.slug}").json()["publish_staff_groups"]
    assert shared["shown"] and not shared["saved"]
    assert shared["groups"] == shared["proposal"] == [["S1", "S1b"], ["S2", "S2b"], ["A1", "A2"]]
    assert shared["source"] == "part names"

    reply = client.post(f"/api/songs/{six.slug}/publish-settings",
                        json={"staff_groups": "S1+S2, A1+A2"})
    assert reply.status_code == 200, reply.json()
    shared = reply.json()["publish_staff_groups"]
    assert shared["saved"] and shared["groups"] == [["S1", "S2"], ["A1", "A2"]]
    assert _saved(six)["staff_groups"] == [["S1", "S2"], ["A1", "A2"]]

    # Saving something else keeps the pairing; null goes back to the automatic one.
    client.post(f"/api/songs/{six.slug}/publish-settings", json={"credits": {}})
    assert _saved(six)["staff_groups"] == [["S1", "S2"], ["A1", "A2"]]
    client.post(f"/api/songs/{six.slug}/publish-settings", json={"staff_groups": None})
    assert _saved(six)["staff_groups"] is None
    assert not client.get(f"/api/songs/{six.slug}").json()["publish_staff_groups"]["saved"]

    # An empty pairing is saved as none: every part keeps its own staff.
    client.post(f"/api/songs/{six.slug}/publish-settings", json={"staff_groups": ""})
    assert _saved(six)["staff_groups"] == []


@pytest.mark.parametrize("text, message", [
    ("S1+T1", "No such part: T1"),
    ("S1+S2+S3", "at most two parts"),
    ("S1+S2, S2+A1", "S2 is in more than one"),
])
def test_a_pairing_the_score_cannot_draw_is_refused(client, six, text, message):
    reply = client.post(f"/api/songs/{six.slug}/publish-settings", json={"staff_groups": text})
    assert reply.status_code == 400 and message in reply.json()["detail"]
    assert "staff_groups" not in _saved(six)


def test_a_publish_is_handed_the_saved_pairing_or_none_for_the_automatic_one(
        client, six, monkeypatch, tmp_path):
    _cf, seen, _data = _publish(client, six, monkeypatch, tmp_path, {"choir": "naiskuoro"})
    assert "staff_groups" not in seen, "None: worked out from the page"
    _cf, seen, _data = _publish(client, six, monkeypatch, tmp_path, {
        "choir": "naiskuoro", "settings": {"staff_groups": "S1+S1b"}})
    assert seen["staff_groups"] == [["S1", "S1b"]]
