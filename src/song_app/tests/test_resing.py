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


FINNISH = "Virta venhettä vie, aallot laulaa, ilta saapuu rantaan hiljaa"
ENGLISH = "For unto us a child is born, unto us a son is given, wonderful counsellor"


def score(words):
    """A cleaned score whose one staff sings `words`, a word a note."""
    chords = "".join(f"<Chord><Lyrics><text>{w}</text></Lyrics></Chord>" for w in words.split())
    return f"<museScore><Score><Staff id='1'><Measure><voice>{chords}</voice></Measure></Staff></Score></museScore>"


def make_song(tmp_path, monkeypatch, slug, sites=None, words=FINNISH):
    monkeypatch.setattr(state, "SONGS_DIR", str(tmp_path))
    folder = tmp_path / slug
    folder.mkdir(parents=True, exist_ok=True)
    data = {"name": slug, "stage": "publish", "publish": {"sites": sites or {}}}
    if words is not None:
        (folder / "s_cleaned.mscx").write_text(score(words))
        data["cleaned"] = "s_cleaned.mscx"
    (folder / state.STATE_FILE).write_text(json.dumps(data))
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


# ---- #484: a singer change that touched some languages, and the song's language ----
CHANGES = {1: "*", 2: "*", 3: ["en"]}


def test_a_change_to_english_leaves_finnish_songs_up_to_date():
    rows = [row("jm")]
    fi = {"jm": dict(record("jm", 2), language="fi")}
    en = {"jm": dict(record("jm", 2), language="en")}
    assert resing.song_status(rows, fi, 3, changes=CHANGES)["current"] == ["jm"]
    assert resing.song_status(rows, en, 3, changes=CHANGES)["pending"] == ["jm"]
    # a change to every song still re-sings it, and without the table every version is one
    assert resing.song_status(rows, fi, 2, changes=CHANGES)["current"] == ["jm"]
    assert resing.song_status(rows, {"jm": dict(record("jm", 1), language="fi")}, 3,
                              changes=CHANGES)["pending"] == ["jm"]
    assert resing.song_status(rows, fi, 3)["pending"] == ["jm"]


def test_a_record_with_no_language_was_sung_as_finnish():
    rows = [row("jm")]
    old = {"jm": record("jm", 2)}
    assert resing.song_status(rows, old, 3, changes=CHANGES,
                              words_look_finnish=lambda: True)["current"] == ["jm"]
    # the words say otherwise: sung before languages were kept, maybe in the wrong one
    assert resing.song_status(rows, old, 3, changes=CHANGES,
                              words_look_finnish=lambda: False)["pending"] == ["jm"]
    # saved English, sung as Finnish (no language on the record): pending, words not read
    asked = []
    status = resing.song_status(rows, old, 3, changes=CHANGES, saved_language="en",
                                words_look_finnish=lambda: asked.append(1) or True)
    assert status["pending"] == ["jm"] and asked == []


def test_a_saved_language_that_differs_from_the_one_sung_is_pending():
    rows = [row("jm")]
    sung_fi = {"jm": dict(record("jm", 3), language="fi")}
    assert resing.song_status(rows, sung_fi, 3, changes=CHANGES, saved_language="en")["pending"] == ["jm"]
    assert resing.song_status(rows, sung_fi, 3, changes=CHANGES, saved_language="fi")["current"] == ["jm"]
    # a record that says its language is not second-guessed by the words
    assert resing.song_status(rows, sung_fi, 3, changes=CHANGES,
                              words_look_finnish=lambda: False)["current"] == ["jm"]


def test_the_overview_reads_the_words_once_per_score(tmp_path, monkeypatch):
    fi, _ = make_song(tmp_path, monkeypatch, "fi", {"jm": record("jm", 2, slug="fi")})
    en, en_dir = make_song(tmp_path, monkeypatch, "en", {"jm": record("jm", 2, slug="en")}, ENGLISH)
    none, _ = make_song(tmp_path, monkeypatch, "none", {"jm": record("jm", 2, slug="none")}, None)
    listed = {"songs": [row("jm", s) for s in ("fi", "en", "none")], "staging": [], "error": None, "at": 1}
    o = resing.overview([fi, en, none], listed, 3, CHANGES, str(tmp_path))
    assert o["songs"]["fi"]["current"] == ["jm"]
    assert o["songs"]["en"]["pending"] == ["jm"]       # English words, sung as Finnish
    assert o["songs"]["none"]["pending"] == ["jm"]     # no score to read: not known to be Finnish
    cache = json.loads((tmp_path / resing.LANGUAGE_FILE).read_text())
    assert cache["fi"]["finnish"] is True and cache["en"]["finnish"] is False
    # kept: a second overview does not read the scores again
    monkeypatch.setattr(resing.language_check, "score_words", lambda p: 1 / 0)
    assert resing.overview([fi, en, none], listed, 3, CHANGES, str(tmp_path))["songs"]["en"]["pending"] == ["jm"]
    # the song's language saved as English: pending until sung in English
    resing.language_check.save(str(en_dir), "en", "app")
    assert resing.overview([state.load("en")], listed, 3, CHANGES, str(tmp_path))["songs"]["en"]["pending"] == ["jm"]


def test_a_cache_that_cannot_be_read_is_started_afresh_and_old_temp_files_go(tmp_path, monkeypatch):
    fi, _ = make_song(tmp_path, monkeypatch, "fi", {"jm": record("jm", 2, slug="fi")})
    (tmp_path / resing.LANGUAGE_FILE).write_text("{not json")
    stale = tmp_path / (resing.LANGUAGE_FILE + ".abc.tmp")
    fresh = tmp_path / (resing.LANGUAGE_FILE + ".def.tmp")
    stale.write_text("{}")
    fresh.write_text("{}")
    os.utime(stale, (1, 1))
    listed = {"songs": [row("jm", "fi")], "staging": [], "error": None, "at": 1}
    assert resing.overview([fi], listed, 3, CHANGES, str(tmp_path))["songs"]["fi"]["current"] == ["jm"]
    assert json.loads((tmp_path / resing.LANGUAGE_FILE).read_text())["fi"]["finnish"] is True
    assert not stale.exists() and fresh.exists()


def test_the_cache_is_read_again_once_the_score_changes(tmp_path, monkeypatch):
    song, folder = make_song(tmp_path, monkeypatch, "s", {"jm": record("jm", 2, slug="s")})
    listed = {"songs": [row("jm", "s")], "staging": [], "error": None, "at": 1}
    assert resing.overview([song], listed, 3, CHANGES, str(tmp_path))["songs"]["s"]["current"] == ["jm"]
    (folder / "s_cleaned.mscx").write_text(score(ENGLISH))
    assert resing.overview([song], listed, 3, CHANGES, str(tmp_path))["songs"]["s"]["pending"] == ["jm"]


def test_two_writers_at_once_leave_one_whole_cache(tmp_path):
    import threading
    def write(n):
        for i in range(30):
            resing._write_json(str(tmp_path), resing.LANGUAGE_FILE, {"writer": n, "i": i, "pad": "x" * 5000})
    threads = [threading.Thread(target=write, args=(n,)) for n in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert json.loads((tmp_path / resing.LANGUAGE_FILE).read_text())["i"] == 29
    assert not list(tmp_path.glob("*.tmp"))


def test_singer_changes_covers_every_version_and_a_bad_line_means_every_song(tmp_path):
    ok = tmp_path / "ok.py"
    ok.write_text('SINGER_VERSION = 4\nSINGER_CHANGES = {1: "*", 3: "en", 4: ["la", "en"]}\n')
    assert progress.singer_changes(str(ok)) == {1: "*", 2: "*", 3: ["en"], 4: ["en", "la"]}
    bad = tmp_path / "bad.py"
    bad.write_text("SINGER_VERSION = 2\nSINGER_CHANGES = {1: open('x')}\n")
    assert progress.singer_changes(str(bad)) == {1: "*", 2: "*"}
    real = progress.singer_changes()
    assert sorted(real) == list(range(1, progress.singer_version() + 1))


def test_a_change_to_a_language_re_sings_its_variants_and_not_the_other_way():
    rows = [row("jm")]
    la_de = {"jm": dict(record("jm", 3), language="la-de")}
    la = {"jm": dict(record("jm", 3), language="la")}
    assert resing.song_status(rows, la_de, 4, changes={**CHANGES, 4: "la"})["pending"] == ["jm"]
    assert resing.song_status(rows, la, 4, changes={**CHANGES, 4: "la-de"})["current"] == ["jm"]
    assert resing.song_status(rows, la_de, 4, changes={**CHANGES, 4: ["en", "et"]})["current"] == ["jm"]
    # a code is matched whole, never as a piece of another ("e" is no change to "en")
    assert resing.song_status(rows, {"jm": dict(record("jm", 3), language="e")}, 4,
                              changes={**CHANGES, 4: "en"})["current"] == ["jm"]
