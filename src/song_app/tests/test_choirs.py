"""The choirs a song can be published to are the site's own (#408).

They used to be a list in `publish.py` and `app.js` naming one choir director's
choirs, so anybody running their own copy of the site had to edit code before
they could publish at all. Now they come from the site's D1 `choirs` table and
are kept in `songs/.choirs.json` for when Cloudflare cannot be reached.
"""

import json
import os

import pytest

from src.song_app import publish
from src.song_app.tests.test_publish import CONFIG, FakeCloudflare, _bundle

pytest.importorskip("httpx")


def _client(cf):
    return publish.Cloudflare(CONFIG, cf)


def test_the_choirs_are_read_from_the_sites_database_private_ones_first(tmp_path):
    cf = FakeCloudflare(choirs=[("public", "Esittely", 1), ("kamari", "Kamarikuoro", 0),
                                ("alto", "Altot", 0)])
    reply = publish.choirs(CONFIG, str(tmp_path), _client(cf))
    assert reply["error"] is None
    assert [(c["id"], c["name"], c["public"]) for c in reply["choirs"]] == [
        ("alto", "Altot", False), ("kamari", "Kamarikuoro", False), ("public", "Esittely", True)]
    saved = json.loads((tmp_path / publish.CHOIRS_FILE).read_text())
    assert saved["choirs"] == reply["choirs"]


def test_the_saved_list_is_offered_while_the_site_cannot_be_reached(tmp_path):
    publish.choirs(CONFIG, str(tmp_path), _client(FakeCloudflare()))
    offline = FakeCloudflare(fail_on="/d1/")
    reply = publish.choirs(CONFIG, str(tmp_path), _client(offline))
    assert [c["id"] for c in reply["choirs"]] == ["jm", "naiskuoro", "public"]
    assert "403" in reply["error"] and reply["saved_at"]


def test_nothing_saved_and_nothing_read_is_an_empty_list_that_says_why(tmp_path):
    reply = publish.choirs(CONFIG, str(tmp_path), _client(FakeCloudflare(fail_on="/d1/")))
    assert reply["choirs"] == [] and "403" in reply["error"]


def test_no_keys_means_no_choirs_and_says_which_keys(tmp_path):
    reply = publish.choirs(None, str(tmp_path))
    assert reply["choirs"] == [] and "CLOUDFLARE_API_TOKEN" in reply["error"]
    assert not os.path.exists(tmp_path / publish.CHOIRS_FILE)


def test_a_choir_the_site_does_not_have_is_refused_and_one_it_has_is_published(tmp_path):
    cf = FakeCloudflare(choirs=[("kamari", "Kamarikuoro", 0), ("public", "Esittely", 1)])
    with pytest.raises(publish.PublishError, match="Unknown choir 'jm'.*kamari, public"):
        publish.publish(slug="laulu", title="Laulu", choir="jm",
                        bundle=_bundle(tmp_path), client=_client(cf))
    publish.publish(slug="laulu", title="Laulu", choir="kamari",
                    bundle=_bundle(tmp_path), client=_client(cf))
    assert [row[0] for row in cf.listed("kamari")] == ["laulu"]


def _app(monkeypatch, tmp_path, cf):
    """The app with publish keys, `cf` for Cloudflare and one approved song."""
    from fastapi.testclient import TestClient
    from src.song_app import server, state
    from src.song_app.tests.test_publish import FERMATA

    for key, value in (("CLOUDFLARE_ACCOUNT_ID", "acct"), ("CLOUDFLARE_API_TOKEN", "tok"),
                       ("STEMMANAUHAT_D1_DATABASE_ID", "db1")):
        monkeypatch.setenv(key, value)
    for key in ("STEMMANAUHAT_R2_BUCKET", "AGENTDECK_API_URL", "AGENTDECK_URL"):
        monkeypatch.delenv(key, raising=False)
    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    monkeypatch.setattr(publish.urllib.request, "urlopen", cf)
    monkeypatch.setattr(publish, "build_bundle", lambda *a, **k: _bundle(tmp_path))
    song = state.create("Laulu", per_system=False)
    cleaned = song.path("laulu_cleaned.mscx")
    with open(FERMATA, "rb") as src, open(cleaned, "wb") as dst:
        dst.write(src.read())
    song.data.update(cleaned=os.path.basename(cleaned), stage="publish",
                     review={"approved_against": state.file_fingerprint(cleaned)})
    song.save()
    return TestClient(server.app), f"/api/songs/{song.slug}/publish"


def test_the_app_offers_the_sites_choirs_and_publishes_to_one_of_its_own(
        monkeypatch, tmp_path):
    cf = FakeCloudflare(choirs=[("kamari", "Kamarikuoro", 0), ("public", "Esittely", 1)])
    client, url = _app(monkeypatch, tmp_path, cf)
    listed = client.get("/api/choirs").json()
    assert [c["id"] for c in listed["choirs"]] == ["kamari", "public"]

    refused = client.post(url, json={"choir": "jm"})
    assert refused.status_code == 400 and "kamari" in refused.json()["detail"]
    assert client.post(url, json={"choir": "kamari"}).status_code == 200


def test_publishing_while_the_site_cannot_be_reached_is_refused_before_anything_starts(
        monkeypatch, tmp_path):
    client, url = _app(monkeypatch, tmp_path, FakeCloudflare(fail_on="/d1/"))
    refused = client.post(url, json={"choir": "jm"})
    assert refused.status_code == 502 and "choirs" in refused.json()["detail"]
    assert client.post(url, json={}).status_code == 400


# The test list (#422, stemmanauhat-cf#73): a choir marked `staging` is where a new
# song, or a fix of one already on a choir's list, is tried out first.
WITH_TEST_LIST = [("jm", "Joensuun Mieslaulajat", 0, 0), ("testi", "Testilista", 0, 1),
                  ("public", "Esittely", 1, 0)]


def test_the_test_list_is_read_as_staging_and_an_older_site_has_none(tmp_path):
    reply = publish.choirs(CONFIG, str(tmp_path), _client(FakeCloudflare(choirs=WITH_TEST_LIST)))
    assert [(c["id"], c["staging"]) for c in reply["choirs"]] == [
        ("jm", False), ("testi", True), ("public", False)]
    older = publish.choirs(CONFIG, str(tmp_path / "old"),
                           _client(FakeCloudflare(staging=False)))
    assert older["error"] is None
    assert [(c["id"], c["staging"]) for c in older["choirs"]] == [
        ("jm", False), ("naiskuoro", False), ("public", False)]


def test_publishing_to_the_test_list_keeps_the_song_on_its_choirs_list(tmp_path):
    import time

    cf = FakeCloudflare(choirs=WITH_TEST_LIST)
    client = _client(cf)
    jm = publish.publish(slug="laulu", title="Laulu", choir="jm",
                         bundle=_bundle(tmp_path), client=client)
    log = []
    testi = publish.publish(slug="laulu", title="Laulu", choir="testi",
                            bundle=_bundle(tmp_path), client=client,
                            published={"jm": jm}, log=log.append)
    assert [r[0] for r in cf.listed("jm")] == ["laulu"]
    assert [r[0] for r in cf.listed("testi")] == ["laulu"]
    assert sorted(cf.objects) == sorted(jm["files"] + testi["files"])
    assert not any(line.startswith("Removed it") for line in log)
    staging = ["testi"]
    assert publish.replaced("testi", {"jm": jm}, staging) == []

    # Publishing to the choir again takes it off the test list: it has moved.
    time.sleep(1.1)
    jm2 = publish.publish(slug="laulu", title="Laulu", choir="jm",
                          bundle=_bundle(tmp_path), client=client,
                          published={"jm": jm, "testi": testi}, log=log.append)
    assert cf.listed("testi") == [] and cf.listed("jm")[0][2] == jm2["prefix"]
    assert sorted(cf.objects) == sorted(jm2["files"])
    assert "Removed it from the testi list." in log
    assert publish.replaced("jm", {"testi": testi}, staging) == ["testi"]


def test_the_app_keeps_the_choirs_record_when_a_song_goes_to_the_test_list(
        monkeypatch, tmp_path):
    from src.song_app.tests.test_publish import _job_done

    cf = FakeCloudflare(choirs=WITH_TEST_LIST)
    client, url = _app(monkeypatch, tmp_path, cf)
    slug = url.split("/")[3]
    assert client.post(url, json={"choir": "jm"}).status_code == 200
    _job_done(client, slug)
    assert client.post(url, json={"choir": "testi"}).status_code == 200
    data = _job_done(client, slug)
    assert data["jobs"]["publish"]["status"] == "succeeded", data["jobs"]["publish"]
    assert sorted(data["publish"]["sites"]) == ["jm", "testi"]
    assert cf.listed("jm") and cf.listed("testi")

    assert client.post(url, json={"choir": "jm"}).status_code == 200
    data = _job_done(client, slug)
    assert sorted(data["publish"]["sites"]) == ["jm"] and cf.listed("testi") == []
