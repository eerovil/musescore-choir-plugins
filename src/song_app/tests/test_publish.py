"""Publishing a song to the Cloudflare stemmanauhat site (#382, #400).

Cloudflare is mocked at the HTTP boundary: `FakeCloudflare` answers the REST
calls a publish makes, keeping R2 as a dict and D1 as a real SQLite database
(D1 *is* SQLite), so "the song is in the jm list" is a SELECT on what the
publish actually wrote. The timing file is pinned against a hand-made MuseScore
play order and MIDI clock, and once more against the real MuseScore CLI.
"""

import io
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse

import mido
import pytest

from src.score_export.tests.conftest import needs_musescore
from src.song_app import publish

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "..", "score_export", "tests",
                        "test_files")
FERMATA = os.path.join(FIXTURES, "fermata.mscx")
CONFIG = publish.Config(account_id="acct", api_token="tok", bucket="stemmanauhat",
                        database_id="db1")


SITE_SONGS = """CREATE TABLE songs (
  choir TEXT NOT NULL, slug TEXT NOT NULL, title TEXT NOT NULL, prefix TEXT NOT NULL,
  parts TEXT NOT NULL, duration REAL NOT NULL, published_at TEXT NOT NULL,
  composer TEXT, lyricist TEXT, arranger TEXT, PRIMARY KEY (choir, slug))"""
# The site's choirs table and the rows its migration 0001 seeds on the live copy.
SITE_CHOIRS = """CREATE TABLE choirs (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, public INTEGER NOT NULL DEFAULT 0)"""
SEEDED = [("jm", "Joensuun Mieslaulajat", 0), ("naiskuoro", "Naiskuoro", 0),
          ("public", "Esittely", 1)]


class _Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeCloudflare:
    """Cloudflare's REST API as a publish uses it: R2 objects and D1 queries."""

    def __init__(self, fail_on=None, choirs=SEEDED):
        self.objects = {}
        self.types = {}
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        # The site's own schema (stemmanauhat-cf migrations 0001 and 0004): the
        # site owns the table, and a publish no longer creates it.
        self.db.execute(SITE_SONGS)
        self.db.execute(SITE_CHOIRS)
        self.db.executemany("INSERT INTO choirs VALUES (?, ?, ?)", choirs)
        self.calls = []
        self.fail_on = fail_on

    def __call__(self, request, timeout=None):
        url = urllib.parse.urlparse(request.full_url)
        method = request.get_method()
        self.calls.append((method, url.path))
        assert request.get_header("Authorization") == "Bearer tok"
        base = "/client/v4/accounts/acct"
        assert url.path.startswith(base), url.path
        path = url.path[len(base):]
        if self.fail_on and self.fail_on in path:
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {},
                                         io.BytesIO(b'{"errors":[{"message":"no"}]}'))
        if path.startswith("/r2/buckets/stemmanauhat/objects/"):
            key = urllib.parse.unquote(path.split("/objects/", 1)[1])
            if method == "PUT":
                self.objects[key] = request.data
                self.types[key] = request.get_header("Content-type")
            elif method == "GET":
                if key not in self.objects:
                    raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {},
                                                 io.BytesIO(b'{"errors":[]}'))
                return _Reply(self.objects[key])
            elif method == "DELETE":
                self.objects.pop(key, None)
            return _Reply(b'{"success":true}')
        if path == "/r2/buckets/stemmanauhat/objects" and method == "GET":
            # Listed two at a time, so the cursor is followed.
            prefix = urllib.parse.parse_qs(url.query)["prefix"][0]
            keys = sorted(k for k in self.objects if k.startswith(prefix))
            start = int(urllib.parse.parse_qs(url.query).get("cursor", ["0"])[0])
            page = keys[start:start + 2]
            more = start + 2 < len(keys)
            return _Reply(json.dumps({
                "success": True,
                "result": [{"key": k, "http_metadata": {"contentType": self.types.get(k)}}
                           for k in page],
                "result_info": {"is_truncated": more, "cursor": str(start + 2) if more else ""},
            }).encode())
        if path == "/d1/database/db1/query" and method == "POST":
            body = json.loads(request.data)
            rows = self.db.execute(body["sql"], body["params"]).fetchall()
            self.db.commit()
            return _Reply(json.dumps({"success": True,
                                      "result": [{"results": rows}]}).encode())
        raise AssertionError(f"unexpected call {method} {path}")

    def listed(self, choir):
        return self.db.execute("SELECT slug, title, prefix, parts FROM songs "
                               "WHERE choir = ?", [choir]).fetchall()

    def credits(self, choir, slug):
        return self.db.execute("SELECT composer, lyricist, arranger FROM songs "
                               "WHERE choir = ? AND slug = ?", [choir, slug]).fetchone()


def _bundle(tmp_path, parts=("S1", "A1")):
    xml = tmp_path / "score.musicxml"
    xml.write_text("<score-partwise/>")
    midi = tmp_path / "score.mid"
    midi.write_bytes(b"MThd-fake")
    timing = {"version": 1, "measures": 2, "duration": 4.5,
              "points": [[0.0, 0, 0.0], [4.5, 1, 4.0]]}
    return publish.Bundle(musicxml=str(xml), timing=timing, parts=list(parts), midi=str(midi))


def test_a_published_song_is_in_its_choirs_list_with_score_midi_timing_and_every_part_name(tmp_path):
    cf = FakeCloudflare()
    record = publish.publish(slug="laulu", title="Laulu", choir="jm",
                             bundle=_bundle(tmp_path), client=publish.Cloudflare(CONFIG, cf))

    [(slug, title, prefix, parts)] = cf.listed("jm")
    assert (slug, title) == ("laulu", "Laulu")
    assert cf.listed("naiskuoro") == []
    assert prefix == record["prefix"] and prefix.startswith("songs/jm/laulu/")
    parts = json.loads(parts)
    # Names only: the site plays the MIDI, so no audio per part goes up (stemmanauhat-cf#36).
    assert parts == [{"name": "S1"}, {"name": "A1"}]
    assert cf.objects[prefix + "score.musicxml"] == b"<score-partwise/>"
    assert json.loads(cf.objects[prefix + "timing.json"])["points"][-1] == [4.5, 1, 4.0]
    # The site plays the score's MIDI (#400).
    assert cf.objects[prefix + "score.mid"] == b"MThd-fake"
    assert cf.types[prefix + "score.mid"] == "audio/midi"
    assert sorted(cf.objects) == sorted(prefix + f for f in (
        "score.musicxml", "score.mid", "timing.json", "manifest.json"))
    manifest = json.loads(cf.objects[prefix + "manifest.json"])
    assert manifest["parts"] == parts and manifest["choir"] == "jm"
    assert manifest["midi"] == "score.mid" and manifest["score"] == "score.musicxml"
    # No credits given: none in the manifest, NULL in the row.
    assert not set(publish.CREDITS) & set(manifest)
    assert cf.credits("jm", "laulu") == (None, None, None)
    # The row goes in last: the site never points at files not yet uploaded.
    assert cf.calls[-1] == ("POST", "/client/v4/accounts/acct/d1/database/db1/query")


def test_the_credits_that_are_set_reach_the_row_and_the_manifest(tmp_path):
    cf = FakeCloudflare()
    record = publish.publish(slug="laulu", title="Laulu", choir="jm",
                             bundle=_bundle(tmp_path), client=publish.Cloudflare(CONFIG, cf),
                             credits={"composer": "Jean Sibelius", "lyricist": " ",
                                      "arranger": "Eero"})
    assert cf.credits("jm", "laulu") == ("Jean Sibelius", None, "Eero")
    manifest = json.loads(cf.objects[record["prefix"] + "manifest.json"])
    assert manifest["composer"] == "Jean Sibelius" and manifest["arranger"] == "Eero"
    assert "lyricist" not in manifest

    # Publishing again with the composer cleared clears it on the site too.
    time.sleep(1.1)
    publish.publish(slug="laulu", title="Laulu", choir="jm", bundle=_bundle(tmp_path),
                    client=publish.Cloudflare(CONFIG, cf), credits={"arranger": "Eero"},
                    published={"jm": record})
    assert cf.credits("jm", "laulu") == (None, None, "Eero")


def test_score_credits_reads_the_metatags_that_are_set(tmp_path):
    score = tmp_path / "s.mscx"
    score.write_text('<museScore><Score><metaTag name="composer">Toivo  Kuula</metaTag>'
                     '<metaTag name="lyricist"></metaTag><metaTag name="arranger">A</metaTag>'
                     '<metaTag name="workTitle">X</metaTag></Score></museScore>')
    assert publish.score_credits(str(score)) == {"composer": "Toivo Kuula", "arranger": "A"}
    assert publish.score_credits(str(tmp_path / "missing.mscx")) == {}


def test_publishing_again_replaces_the_old_version_and_a_new_choir_moves_the_song(tmp_path):
    cf = FakeCloudflare()
    client = publish.Cloudflare(CONFIG, cf)
    first = publish.publish(slug="laulu", title="Laulu", choir="jm",
                            bundle=_bundle(tmp_path), client=client)
    time.sleep(1.1)  # a new version is a new second
    second = publish.publish(slug="laulu", title="Laulu", choir="naiskuoro",
                             bundle=_bundle(tmp_path, parts=("S1", "S2", "A1")),
                             client=client, published={"jm": first})

    assert cf.listed("jm") == []
    [(_slug, _title, prefix, parts)] = cf.listed("naiskuoro")
    assert prefix == second["prefix"] and len(json.loads(parts)) == 3
    assert sorted(cf.objects) == sorted(second["files"])
    assert publish.replaced("naiskuoro", {"jm": first}) == ["jm"]


def test_a_song_can_be_in_the_public_list_and_a_choirs_list_at_once(tmp_path):
    cf = FakeCloudflare()
    client = publish.Cloudflare(CONFIG, cf)
    jm = publish.publish(slug="maamme", title="Maamme", choir="jm",
                         bundle=_bundle(tmp_path), client=client)
    public = publish.publish(slug="maamme", title="Maamme", choir="public",
                             bundle=_bundle(tmp_path), client=client,
                             published={"jm": jm})
    # Publishing to public takes nothing away.
    assert [r[0] for r in cf.listed("jm")] == ["maamme"]
    assert [r[0] for r in cf.listed("public")] == ["maamme"]
    assert sorted(cf.objects) == sorted(jm["files"] + public["files"])

    # And publishing to the choir again keeps the public listing and its files,
    # while the choir's own old version goes.
    time.sleep(1.1)
    jm2 = publish.publish(slug="maamme", title="Maamme", choir="jm",
                          bundle=_bundle(tmp_path), client=client,
                          published={"jm": jm, "public": public})
    assert cf.listed("jm")[0][2] == jm2["prefix"]
    assert cf.listed("public")[0][2] == public["prefix"]
    assert sorted(cf.objects) == sorted(jm2["files"] + public["files"])


def test_a_move_between_choirs_keeps_the_public_listing_and_drops_an_unrecorded_choir(tmp_path):
    cf = FakeCloudflare()
    client = publish.Cloudflare(CONFIG, cf)
    jm = publish.publish(slug="maamme", title="Maamme", choir="jm",
                         bundle=_bundle(tmp_path), client=client)
    public = publish.publish(slug="maamme", title="Maamme", choir="public",
                             bundle=_bundle(tmp_path), client=client,
                             published={"jm": jm})
    log = []
    nais = publish.publish(slug="maamme", title="Maamme", choir="naiskuoro",
                           bundle=_bundle(tmp_path), client=client,
                           published={"public": public}, log=log.append)
    # jm was not in the record handed in (published by hand, say): its row goes
    # anyway, as the site's own script does, and public stays.
    assert cf.listed("jm") == []
    assert [r[0] for r in cf.listed("naiskuoro")] == ["maamme"]
    assert [r[0] for r in cf.listed("public")] == ["maamme"]
    assert "Removed it from the jm list." in log
    assert set(public["files"]) <= set(cf.objects) and set(nais["files"]) <= set(cf.objects)


def test_records_reads_a_song_published_before_there_was_one_per_choir():
    old = {"site": {"choir": "jm", "files": []}, "choir": "jm"}
    assert publish.records(old) == {"jm": old["site"]}
    assert publish.records({"sites": {"public": {"choir": "public"}}}) == {
        "public": {"choir": "public"}}
    assert publish.records(None) == {} and publish.records({"error": "x"}) == {}
    assert publish.replaced("public", {"jm": {}, "naiskuoro": {}}) == []


def test_a_refused_upload_registers_nothing(tmp_path):
    cf = FakeCloudflare(fail_on="/objects/")
    with pytest.raises(publish.PublishError, match="403 no"):
        publish.publish(slug="laulu", title="Laulu", choir="jm",
                        bundle=_bundle(tmp_path), client=publish.Cloudflare(CONFIG, cf))
    assert cf.listed("jm") == []


def test_an_unknown_choir_is_refused(tmp_path):
    with pytest.raises(publish.PublishError, match="Unknown choir"):
        publish.publish(slug="laulu", title="Laulu", choir="kamari",
                        bundle=_bundle(tmp_path),
                        client=publish.Cloudflare(CONFIG, FakeCloudflare()))


def test_config_needs_the_account_token_and_database(monkeypatch):
    for key in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN",
                "STEMMANAUHAT_D1_DATABASE_ID", "STEMMANAUHAT_R2_BUCKET"):
        monkeypatch.delenv(key, raising=False)
    assert publish.Config.from_env() is None
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "a")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")
    monkeypatch.setenv("STEMMANAUHAT_D1_DATABASE_ID", "d")
    assert publish.Config.from_env() == publish.Config("a", "t", "stemmanauhat", "d")


# ---------------------------------------------------------------------------
# The timing file
# ---------------------------------------------------------------------------
def _midi(path, tempos):
    """A MIDI file whose only content is tempo changes at (quarter, bpm)."""
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    now = 0
    for quarter, bpm in tempos:
        tick = int(quarter * 480)
        track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm),
                                      time=tick - now))
        now = tick
    midi.save(path)
    return path


def _mpos(path, count, played):
    events = "".join(f'<event elid="{bar}" position="{ms}"/>' for bar, ms in played)
    elements = "".join(f'<element id="{i}"/>' for i in range(count))
    with open(path, "w") as f:
        f.write(f"<score><elements>{elements}</elements><events>{events}</events></score>")
    return path


def test_timing_follows_the_play_order_and_the_fermata_on_musescores_clock(tmp_path):
    # Two 4/4 bars played 1, 2, 1, 2 (a repeat); beat 4 of bar 1 is a fermata,
    # which MuseScore writes as 120 -> 40 bpm for that beat.
    tempos = [(0, 120), (3, 40), (4, 120), (11, 40), (12, 120)]
    midi = _midi(str(tmp_path / "s.mid"), tempos)
    mpos = _mpos(str(tmp_path / "s.mpos"), 2,
                 [(0, 0), (1, 3000), (0, 5000), (1, 8000)])
    clock = publish.timing(FERMATA, mpos, midi)

    assert clock["measures"] == 2
    points = clock["points"]
    assert points[:6] == [[0.0, 0, 0.0], [0.5, 0, 1.0], [1.0, 0, 2.0], [1.5, 0, 3.0],
                          [3.0, 0, 4.0], [3.0, 1, 0.0]]
    assert [p[1] for p in points if p[2] == 0.0] == [0, 1, 0, 1], "the repeat is followed"
    assert points[-1] == [10.0, 1, 4.0] and clock["duration"] == 10.0
    assert all(a[0] <= b[0] for a, b in zip(points, points[1:]))


def test_timing_refuses_a_play_order_that_disagrees_with_the_clock(tmp_path):
    midi = _midi(str(tmp_path / "s.mid"), [(0, 120)])
    mpos = _mpos(str(tmp_path / "s.mpos"), 2, [(0, 0), (1, 2500)])
    with pytest.raises(publish.PublishError, match="cannot be trusted"):
        publish.timing(FERMATA, mpos, midi)


def test_timing_refuses_a_different_bar_count(tmp_path):
    midi = _midi(str(tmp_path / "s.mid"), [(0, 120)])
    mpos = _mpos(str(tmp_path / "s.mpos"), 3, [(0, 0), (1, 2000), (2, 4000)])
    with pytest.raises(publish.PublishError, match="3 bars"):
        publish.timing(FERMATA, mpos, midi)


@needs_musescore
def test_the_bundle_is_musescores_own_parts_and_clock(tmp_path):
    bundle = publish.build_bundle(os.path.join(FIXTURES, "repeat.mscx"), str(tmp_path))
    assert bundle.parts == ["S1", "A1"]
    assert not [f for f in os.listdir(tmp_path) if f.endswith((".wav", ".mp3"))], "no part audio is rendered"
    assert "<score-partwise" in open(bundle.musicxml).read()
    assert open(bundle.midi, "rb").read(4) == b"MThd"
    starts = [p[1] for p in bundle.timing["points"] if p[2] == 0.0]
    assert starts == [0, 1, 0, 1], "MuseScore plays the repeat, so the timing does"


@needs_musescore
def test_the_names_given_are_the_bundles_parts_and_its_midi_tracks(tmp_path):
    """#402: the names typed on the Publish panel are what the site matches the
    MIDI tracks by, so the MIDI must carry them too."""
    bundle = publish.build_bundle(os.path.join(FIXTURES, "repeat.mscx"), str(tmp_path),
                                  part_names=["Sopraano", "Altto"])
    assert bundle.parts == ["Sopraano", "Altto"]
    # MuseScore ends a track name with a NUL byte.
    tracks = [t.name.rstrip("\x00") for t in mido.MidiFile(bundle.midi).tracks if t.name]
    assert tracks == ["Sopraano", "Altto"]
    assert "<part-name>Sopraano</part-name>" in open(bundle.musicxml).read()


def test_a_score_cleaned_into_another_number_of_parts_is_refused(tmp_path):
    with pytest.raises(publish.PublishError,
                       match="The score now has 2 parts; check the part names"):
        publish.build_bundle(os.path.join(FIXTURES, "repeat.mscx"), str(tmp_path),
                             part_names=["S1", "S2", "A1"])


# ---------------------------------------------------------------------------
# The route: Publish in the app puts the song in the choir's list
# ---------------------------------------------------------------------------
@pytest.fixture
def app_song(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    from src.song_app import state
    for key in ("AGENTDECK_API_URL", "AGENTDECK_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    monkeypatch.setenv("STEMMANAUHAT_D1_DATABASE_ID", "db1")
    monkeypatch.delenv("STEMMANAUHAT_R2_BUCKET", raising=False)
    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    song = state.create("Hanget soi", per_system=False, voicing="men")
    cleaned = song.path("hanget_cleaned.mscx")
    with open(FERMATA, "rb") as src, open(cleaned, "wb") as dst:
        dst.write(src.read())
    song.data["cleaned"] = os.path.basename(cleaned)
    song.data["stage"] = "publish"
    song.data["review"] = {"approved_against": state.file_fingerprint(cleaned)}
    song.save()
    return song


def _publish_client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.song_app import server
    cf = FakeCloudflare()
    monkeypatch.setattr(publish.urllib.request, "urlopen", cf)
    seen = {}

    def fake_bundle(cleaned, out_dir, *, initial_bpm=None, tempo_changes=None,
                    part_names=None, log=lambda m: None):
        seen.update(cleaned=cleaned, initial_bpm=initial_bpm, tempo_changes=tempo_changes)
        return _bundle(tmp_path)
    monkeypatch.setattr(publish, "build_bundle", fake_bundle)
    return TestClient(server.app), cf, seen


def _job_done(client, slug):
    for _ in range(250):
        data = client.get(f"/api/songs/{slug}").json()
        if data["jobs"].get("publish", {}).get("status") != "running":
            return data
        time.sleep(0.02)
    raise AssertionError("the publish never finished")


def test_publishing_from_the_app_lists_the_song_for_its_choir(app_song, monkeypatch, tmp_path):
    client, cf, seen = _publish_client(monkeypatch, tmp_path)
    assert client.get(f"/api/songs/{app_song.slug}").json()["publish_configured"]

    reply = client.post(f"/api/songs/{app_song.slug}/publish", json={"choir": "jm"})
    assert reply.status_code == 200, reply.text
    data = _job_done(client, app_song.slug)

    assert data["jobs"]["publish"]["status"] == "succeeded", data["jobs"]["publish"]
    assert seen["cleaned"] == app_song.cleaned_path()
    [(slug, title, prefix, _parts)] = cf.listed("jm")
    assert (slug, title) == (app_song.slug, "Hanget soi")
    assert data["publish"]["sites"]["jm"]["prefix"] == prefix
    assert data["publish"]["choir"] == "jm" and data["publish"]["error"] is None
    assert prefix + "timing.json" in cf.objects

    # Publishing to public keeps the jm record and row (#384) ...
    assert client.post(f"/api/songs/{app_song.slug}/publish",
                       json={"choir": "public"}).status_code == 200
    data = _job_done(client, app_song.slug)
    assert sorted(data["publish"]["sites"]) == ["jm", "public"]
    assert cf.listed("jm") and cf.listed("public")

    # ... and a move to naiskuoro drops jm and keeps public.
    assert client.post(f"/api/songs/{app_song.slug}/publish",
                       json={"choir": "naiskuoro"}).status_code == 200
    data = _job_done(client, app_song.slug)
    assert sorted(data["publish"]["sites"]) == ["naiskuoro", "public"]
    assert cf.listed("jm") == [] and cf.listed("public")


def test_publish_refuses_an_unapproved_score_an_unknown_choir_and_no_keys(
        app_song, monkeypatch, tmp_path):
    from src.song_app import state
    client, cf, _seen = _publish_client(monkeypatch, tmp_path)
    url = f"/api/songs/{app_song.slug}/publish"
    assert client.post(url, json={"choir": "kamari"}).status_code == 400

    song = state.load(app_song.slug)
    song.data["review"] = {}
    song.save()
    assert client.post(url, json={"choir": "jm"}).status_code == 409

    monkeypatch.delenv("CLOUDFLARE_API_TOKEN")
    refused = client.post(url, json={"choir": "jm"})
    assert refused.status_code == 400 and "CLOUDFLARE_API_TOKEN" in refused.json()["detail"]
    assert not any("/r2/" in path for _m, path in cf.calls)


# --- Tempo marks are hidden on the site (#409) ------------------------------
TEMPO_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN" "http://www.musicxml.org/dtds/partwise.dtd">
<score-partwise version="3.1">
  <part id="P1">
    <measure number="1">
      <direction placement="above">
        <direction-type>
          <metronome parentheses="no"><beat-unit>quarter</beat-unit><per-minute>84</per-minute></metronome>
          </direction-type>
        <sound tempo="84"/>
        </direction>
      <direction placement="above">
        <direction-type><words>Kaikki</words></direction-type>
        </direction>
      </measure>
    <measure number="2">
      <direction placement="above">
        <direction-type><words>Tempo I</words></direction-type>
        <direction-type>
          <metronome parentheses="yes"><beat-unit>quarter</beat-unit><per-minute>72</per-minute></metronome>
          </direction-type>
        <sound tempo="72"/>
        </direction>
      </measure>
    </part>
  </score-partwise>
"""


def test_every_tempo_mark_is_set_not_to_print_and_nothing_else_is():
    hidden, count = publish.hide_tempo_marks(TEMPO_XML)
    assert count == 2
    root = etree_root(hidden)
    assert [m.get("print-object") for m in root.iter("metronome")] == ["no", "no"]
    words = {w.text: w.get("print-object") for w in root.iter("words")}
    assert words == {"Kaikki": None, "Tempo I": "no"}, "staff text without a tempo stays"
    assert [s.get("tempo") for s in root.iter("sound")] == ["84", "72"], "the speed stays"
    assert b"<!DOCTYPE score-partwise" in hidden


def test_a_score_with_nothing_to_hide_comes_back_byte_for_byte():
    hidden, _ = publish.hide_tempo_marks(TEMPO_XML)
    assert publish.hide_tempo_marks(hidden) == (hidden, 0)


def etree_root(data):
    from lxml import etree
    return etree.fromstring(data)


def _published(cf, choir, slug, prefix, score, extra=()):
    cf.objects[prefix + "score.musicxml"] = score
    cf.types[prefix + "score.musicxml"] = "application/vnd.recordare.musicxml+xml"
    cf.objects[prefix + "manifest.json"] = json.dumps(
        {"slug": slug, "choir": choir, "version": prefix.rstrip("/").rsplit("/", 1)[1],
         "score": "score.musicxml"}).encode()
    cf.types[prefix + "manifest.json"] = "application/json"
    for name, data in extra:
        cf.objects[prefix + name] = data
        cf.types[prefix + name] = "audio/midi"
    cf.db.execute("INSERT INTO songs VALUES (?, ?, ?, ?, '[]', 1.0, '2025-01-01T00:00:00Z', "
                  "NULL, NULL, NULL)", [choir, slug, slug.title(), prefix])


def _song_record(songs, slug, choir, prefix, files):
    os.makedirs(songs / slug)
    data = {"name": slug, "publish": {"sites": {choir: {
        "choir": choir, "slug": slug, "version": "v1", "prefix": prefix,
        "files": [prefix + f for f in files]}}}, "other": {"kept": True}}
    (songs / slug / ".song.json").write_text(json.dumps(data))


def test_a_published_song_with_tempo_marks_gets_a_new_version_that_hides_them(tmp_path):
    cf = FakeCloudflare()
    old = "songs/jm/laulu/20250101T000000Z/"
    _published(cf, "jm", "laulu", old, TEMPO_XML,
               extra=[("score.mid", b"MThd-1"), ("timing.json", b'{"t": 1}')])
    songs = tmp_path / "songs"
    _song_record(songs, "laulu", "jm", old, ["score.musicxml", "score.mid"])
    _song_record(songs, "toinen", "jm", "songs/jm/toinen/x/", ["score.musicxml"])
    before = dict(cf.objects)

    changed = publish.hide_published_tempo_marks(publish.Cloudflare(CONFIG, cf),
                                                 songs_dir=str(songs))

    [change] = changed
    new = change["new"]
    assert new.startswith("songs/jm/laulu/") and new != old and change["hidden"] == 2
    [(slug, title, prefix, _parts)] = cf.listed("jm")
    assert prefix == new
    assert cf.db.execute("SELECT published_at FROM songs").fetchone()[0] == "2025-01-01T00:00:00Z"
    assert sorted(k[len(new):] for k in cf.objects if k.startswith(new)) == [
        "manifest.json", "score.mid", "score.musicxml", "timing.json"]
    assert cf.objects[new + "score.mid"] == b"MThd-1" and cf.types[new + "score.mid"] == "audio/midi"
    assert cf.objects[new + "timing.json"] == b'{"t": 1}'
    assert cf.objects[new + "score.musicxml"] == publish.hide_tempo_marks(TEMPO_XML)[0]
    assert json.loads(cf.objects[new + "manifest.json"])["version"] == change["version"]
    assert {k: v for k, v in cf.objects.items() if k.startswith(old)} == before, \
        "the old version stays as it was"

    moved = json.loads((songs / "laulu" / ".song.json").read_text())
    record = moved["publish"]["sites"]["jm"]
    assert record["prefix"] == new and record["version"] == change["version"]
    assert sorted(record["files"]) == sorted(change["files"])
    assert moved["other"] == {"kept": True}
    untouched = json.loads((songs / "toinen" / ".song.json").read_text())
    assert untouched["publish"]["sites"]["jm"]["prefix"] == "songs/jm/toinen/x/"


def test_a_published_song_without_tempo_marks_and_a_dry_run_change_nothing(tmp_path):
    cf = FakeCloudflare()
    _published(cf, "jm", "hiljaa", "songs/jm/hiljaa/v1/", b"<score-partwise/>")
    _published(cf, "public", "laulu", "songs/public/laulu/v1/", TEMPO_XML)
    before = dict(cf.objects)

    changed = publish.hide_published_tempo_marks(publish.Cloudflare(CONFIG, cf), dry_run=True)

    assert [(c["choir"], c["slug"]) for c in changed] == [("public", "laulu")]
    assert cf.objects == before
    assert [p for _s, _t, p, _ in cf.listed("public")] == ["songs/public/laulu/v1/"]
    assert not [c for c in cf.calls if c[0] in ("PUT", "DELETE")]


@needs_musescore
def test_the_published_score_carries_its_tempo_marks_unprinted(tmp_path):
    """#409: OpenSheetMusicDisplay draws tempo marks onto the lyrics, so they go
    unprinted; the speeds stay in the file and in the MIDI."""
    bundle = publish.build_bundle(FERMATA, str(tmp_path), initial_bpm=84,
                                  tempo_changes=[{"measure": 2, "bpm": 72}])
    root = etree_root(open(bundle.musicxml, "rb").read())
    marks = list(root.iter("metronome"))
    assert all(m.get("print-object") == "no" for m in marks), "no tempo mark is drawn"
    assert [s.get("tempo") for s in root.iter("sound") if s.get("tempo")][:2] == ["84", "72"]
