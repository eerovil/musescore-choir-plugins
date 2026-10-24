"""#453: which songs need a re-sing after the singer changed, and the route the song list reads."""
import json
import os

from src.sing_song import progress
from src.song_app import publish, resing, state


def row(choir, slug="laulu", prefix=None, sung=True):
    return {"choir": choir, "slug": slug, "prefix": prefix or f"songs/{choir}/{slug}/v1/", "sung": sung}


def record(choir, version=None, prefix=None, slug="laulu"):
    r = {"choir": choir, "prefix": prefix or f"songs/{choir}/{slug}/v1/", "sung": True}
    if version is not None:
        r["singer_version"] = version
    return r


def test_a_sung_listing_with_no_version_is_pending():
    assert resing.song_status([row("jm")], {}, 1) == {"pending": ["jm"], "current": []}
    assert resing.song_status([row("jm")], {"jm": record("jm")}, 1)["pending"] == ["jm"]


def test_the_current_version_on_the_same_upload_is_up_to_date():
    assert resing.song_status([row("jm")], {"jm": record("jm", 2)}, 2) == {"pending": [], "current": ["jm"]}
    assert resing.song_status([row("jm")], {"jm": record("jm", 1)}, 2)["pending"] == ["jm"]   # older


def test_a_listing_uploaded_from_elsewhere_is_pending():
    rows = [row("jm", prefix="songs/jm/laulu/v2/")]
    assert resing.song_status(rows, {"jm": record("jm", 5)}, 1)["pending"] == ["jm"]


def test_each_choir_is_judged_on_its_own_and_piano_only_listings_are_not_sung():
    rows = [row("jm"), row("public"), row("joy", sung=False)]
    status = resing.song_status(rows, {"jm": record("jm", 1)}, 1)
    assert status == {"pending": ["public"], "current": ["jm"]}


def test_site_rows_say_sung_off_their_parts():
    assert resing.sung_parts('[{"name":"T1","sung":true}]')
    assert not resing.sung_parts('[{"name":"T1"}]')
    assert not resing.sung_parts("not json")


class FakeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def query(self, sql, params=()):
        self.calls += 1
        if self.fail:
            raise publish.PublishError("no network")
        if "FROM choirs" in sql:
            return [{"results": [{"id": "jm", "name": "JM", "public": 0, "staging": 0},
                                 {"id": "testi", "name": "Testi", "public": 0, "staging": 1}]}]
        return [{"results": [{"choir": "jm", "slug": "laulu", "prefix": "songs/jm/laulu/v1/",
                              "parts": json.dumps([{"name": "T1", "sung": True}])}]}]


def test_the_site_is_read_saved_and_reused_then_used_when_out_of_reach(tmp_path):
    config = object()
    client = FakeClient()
    listed = resing.site(config, str(tmp_path), client=client, now=1000)
    assert listed["error"] is None and listed["staging"] == ["testi"]
    assert listed["songs"] == [{"choir": "jm", "slug": "laulu", "prefix": "songs/jm/laulu/v1/", "sung": True}]
    calls = client.calls
    assert resing.site(config, str(tmp_path), client=client, now=1050)["songs"] == listed["songs"]
    assert client.calls == calls                                    # fresh enough: not read again
    offline = resing.site(config, str(tmp_path), client=FakeClient(fail=True), now=5000)
    assert offline["error"] == "no network" and offline["songs"] == listed["songs"] and offline["at"] == 1000
    nothing = resing.site(config, str(tmp_path / "empty"), client=FakeClient(fail=True), now=5000)
    assert nothing["songs"] == [] and nothing["error"] == "no network"


def make_song(tmp_path, monkeypatch, slug, sites=None):
    monkeypatch.setattr(state, "SONGS_DIR", str(tmp_path))
    folder = tmp_path / slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / state.STATE_FILE).write_text(json.dumps(
        {"name": slug, "stage": "publish", "publish": {"sites": sites or {}}}))
    return state.load(slug), folder


def test_the_overview_counts_pending_songs_and_runs_and_leaves_out_the_test_list(tmp_path, monkeypatch):
    a, _ = make_song(tmp_path, monkeypatch, "a")
    b, b_dir = make_song(tmp_path, monkeypatch, "b", {"jm": record("jm", 1, slug="b")})
    c, c_dir = make_song(tmp_path, monkeypatch, "c")
    progress.write(str(c_dir), {"state": "singing", "pid": os.getpid(), "part": 2, "parts": 4})
    listed = {"songs": [row("jm", "a"), row("testi", "a"), row("jm", "b"), row("testi", "c")],
              "staging": ["testi"], "error": None, "at": 1}
    o = resing.overview([a, b, c], listed, 1)
    assert o["pending"] == 1 and o["running"] == 1 and o["version"] == 1
    assert o["songs"]["a"]["pending"] == ["jm"]
    assert o["songs"]["b"] == {"pending": [], "current": ["jm"], "run": None}
    assert o["songs"]["c"]["pending"] == [] and o["songs"]["c"]["run"]["state"] == "singing"


def test_the_route_answers_without_a_site(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.song_app import server
    make_song(tmp_path, monkeypatch, "a")
    monkeypatch.setattr(publish.Config, "from_env", classmethod(lambda cls: None))
    reply = TestClient(server.app).get("/api/resing").json()
    assert reply["version"] == progress.singer_version()
    assert reply["site"]["error"] == publish.MISSING_CONFIG and reply["pending"] == 0


def test_a_song_published_after_the_saved_read_counts_as_its_new_upload():
    # #454 review: the saved read still has the old upload for up to MAX_AGE after a re-sing
    rows = [row("jm"), row("public")]
    records = {"jm": dict(record("jm", 1, prefix="songs/jm/laulu/v2/"), at=2000)}
    assert resing.song_status(rows, records, 1, read_at=1000) == {"pending": ["public"], "current": ["jm"]}
    assert resing.song_status(rows, records, 1, read_at=3000)["pending"] == ["jm", "public"]   # site moved on
    new_choir = {"joy": dict(record("joy", 1), at=2000)}
    assert resing.song_status([], new_choir, 1, read_at=1000)["current"] == ["joy"]


def test_a_finished_re_sing_shows_up_to_date_on_the_list_before_the_site_is_read_again(tmp_path, monkeypatch):
    song, folder = make_song(tmp_path, monkeypatch, "a", {"testi": dict(record("testi", 1, slug="a"), at=2000)})
    listed = {"songs": [row("jm", "a")], "staging": ["testi"], "error": None, "at": 1000}
    assert resing.overview([song], listed, 1)["songs"]["a"]["pending"] == ["jm"]   # the test list never counts
    song.data["publish"]["sites"]["jm"] = dict(record("jm", 1, prefix="songs/jm/a/v2/", slug="a"), at=2000)
    song.save()
    progress.write(str(folder), {"state": "done", "finished": 2001, "pid": os.getpid()})
    o = resing.overview([state.load("a")], listed, 1)
    assert o["songs"]["a"]["pending"] == [] and o["songs"]["a"]["current"] == ["jm"]
    assert o["pending"] == 0 and o["running"] == 0
