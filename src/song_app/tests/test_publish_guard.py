"""A piano-only publish does not replace a sung version on the site unless asked (#483).

The app's Publish and ``sing_song.py --no-sing --publish`` send piano only. Before
this, either one quietly replaced the sung MP3s a singing run had published. The
check is made against the site's own row, right before it is written, so a sung
version published from anywhere counts; the app's own record lets the route refuse
before any work is done.
"""

import json

import pytest

from src.song_app import publish
from src.song_app.tests.test_publish import (CONFIG, FakeCloudflare, _bundle, _job_done,
                                             _publish_client, app_song)  # noqa: F401


@pytest.fixture(autouse=True)
def _a_second_per_version(monkeypatch):
    """Each publish a version of its own: the version is the second it was made in."""
    import itertools
    import time
    seconds = itertools.count(1_800_000_000)
    real = time.gmtime
    monkeypatch.setattr(publish.time, "gmtime", lambda *a: real(next(seconds)))


def _sung(tmp_path, bundle):
    for name in bundle.parts:
        (tmp_path / f"{name}.mp3").write_bytes(b"ID3-" + name.encode())
    bundle.sung = {name: {"mp3": str(tmp_path / f"{name}.mp3"), "voice": "LYSE"}
                   for name in bundle.parts}
    return bundle


def _publish_sung(tmp_path, cf, slug="laulu", choir="jm"):
    return publish.publish(slug=slug, title="Laulu", choir=choir, bundle=_sung(tmp_path, _bundle(tmp_path)),
                           client=publish.Cloudflare(CONFIG, cf))


def test_a_piano_publish_over_a_sung_version_is_refused_and_leaves_nothing_behind(tmp_path):
    cf = FakeCloudflare()
    sung = _publish_sung(tmp_path, cf)
    before = dict(cf.objects)
    with pytest.raises(publish.SungOnSite) as refused:
        publish.publish(slug="laulu", title="Laulu", choir="jm", bundle=_bundle(tmp_path),
                        client=publish.Cloudflare(CONFIG, cf), published={"jm": sung})
    assert refused.value.choir == "jm" and refused.value.kind == "sung_on_site"
    [(_slug, _title, prefix, parts)] = cf.listed("jm")
    assert prefix == sung["prefix"]
    assert all(p["sung"] for p in json.loads(parts))
    # its own uploads went again, and nothing of the sung version did
    assert cf.objects == before


def test_confirmed_a_piano_publish_replaces_the_sung_version(tmp_path):
    cf = FakeCloudflare()
    sung = _publish_sung(tmp_path, cf)
    record = publish.publish(slug="laulu", title="Laulu", choir="jm", bundle=_bundle(tmp_path),
                             client=publish.Cloudflare(CONFIG, cf), published={"jm": sung},
                             piano_over_sung=True)
    [(_slug, _title, prefix, parts)] = cf.listed("jm")
    assert prefix == record["prefix"] and record["live"] and not record["sung"]
    assert not any(p.get("sung") for p in json.loads(parts))
    assert not [k for k in cf.objects if k.startswith(sung["prefix"])]


def test_a_piano_version_and_another_choirs_sung_one_are_not_in_the_way(tmp_path):
    cf = FakeCloudflare()
    publish.publish(slug="laulu", title="Laulu", choir="jm", bundle=_bundle(tmp_path),
                    client=publish.Cloudflare(CONFIG, cf))
    _publish_sung(tmp_path, cf, choir="naiskuoro")
    record = publish.publish(slug="laulu", title="Laulu", choir="jm", bundle=_bundle(tmp_path),
                             client=publish.Cloudflare(CONFIG, cf))
    assert record["live"]
    assert publish.sung_on_site(publish.Cloudflare(CONFIG, cf), "naiskuoro", "laulu")
    assert not publish.sung_on_site(publish.Cloudflare(CONFIG, cf), "jm", "laulu")
    assert not publish.sung_on_site(publish.Cloudflare(CONFIG, cf), "public", "laulu")


def test_a_sung_publish_replaces_a_sung_version(tmp_path):
    cf = FakeCloudflare()
    first = _publish_sung(tmp_path, cf)
    second = _publish_sung(tmp_path, cf)
    assert second["live"] and second["prefix"] != first["prefix"]


def test_clean_up_never_takes_the_live_versions_files(tmp_path):
    """A publish that landed meanwhile is the live one: the stale records' files under
    its prefix stay, whichever record names them."""
    cf = FakeCloudflare()
    client = publish.Cloudflare(CONFIG, cf)
    old = publish.publish(slug="laulu", title="Laulu", choir="jm", bundle=_bundle(tmp_path), client=client)
    mine = publish.publish(slug="laulu", title="Laulu", choir="jm", bundle=_bundle(tmp_path),
                           client=client, published={"jm": old})
    # Another publish writes the row after this one's UPSERT and before its record is saved.
    other = publish.publish(slug="laulu", title="Laulu", choir="jm", bundle=_bundle(tmp_path),
                            client=client, published={"jm": mine})
    assert other["live"]
    # The late clean-up of a record naming the live files keeps them.
    publish._clean_up({"jm": other}, mine, client, lambda m: None, [])
    assert all(k in cf.objects for k in other["files"])


def test_a_publish_overtaken_before_its_record_is_saved_says_so(tmp_path, monkeypatch):
    cf = FakeCloudflare()
    client = publish.Cloudflare(CONFIG, cf)
    real = publish._clean_up

    def overtaken(published, current, client_, log, staging):
        real(published, current, client_, log, staging)
        cf.db.execute("UPDATE songs SET prefix = 'songs/jm/laulu/other/' WHERE choir = 'jm'")
    monkeypatch.setattr(publish, "_clean_up", overtaken)
    record = publish.publish(slug="laulu", title="Laulu", choir="jm", bundle=_bundle(tmp_path), client=client)
    assert record["live"] is False


def test_records_now_is_read_after_the_row_is_written(tmp_path):
    """A publish that recorded itself while this one built is cleaned up too."""
    cf = FakeCloudflare()
    client = publish.Cloudflare(CONFIG, cf)
    meanwhile = publish.publish(slug="laulu", title="Laulu", choir="jm", bundle=_bundle(tmp_path),
                                client=client)
    record = publish.publish(slug="laulu", title="Laulu", choir="jm", bundle=_bundle(tmp_path),
                             client=client, published={}, records_now=lambda: {"jm": meanwhile})
    assert record["live"]
    assert not [k for k in cf.objects if k.startswith(meanwhile["prefix"])]


# ---------------------------------------------------------------------------
# The app: refused with a kind the panel can offer to confirm
# ---------------------------------------------------------------------------
def _mark_sung(song, choir):
    from src.song_app import state
    with state.song_lock(song.slug):
        song = state.load(song.slug)
        song.data.setdefault("publish", {}).setdefault("sites", {})[choir] = {
            "choir": choir, "prefix": f"songs/{choir}/x/1/", "files": [], "sung": True}
        song.save()


def test_the_app_refuses_before_starting_when_its_record_says_sung(app_song, monkeypatch, tmp_path):
    client, cf, _seen = _publish_client(monkeypatch, tmp_path)
    _mark_sung(app_song, "jm")
    reply = client.post(f"/api/songs/{app_song.slug}/publish", json={"choir": "jm"})
    assert reply.status_code == 409
    detail = reply.json()["detail"]
    assert detail["kind"] == "sung_on_site" and detail["choir"] == "jm"
    assert "piano only" in detail["message"]
    assert not cf.listed("jm")

    reply = client.post(f"/api/songs/{app_song.slug}/publish", json={"choir": "jm", "piano_only": True})
    assert reply.status_code == 200, reply.text
    data = _job_done(client, app_song.slug)
    assert data["jobs"]["publish"]["status"] == "succeeded", data["jobs"]["publish"]
    assert data["publish"]["sites"]["jm"]["sung"] is False


def test_a_sung_version_the_app_never_recorded_is_refused_by_the_job(app_song, monkeypatch, tmp_path):
    client, cf, _seen = _publish_client(monkeypatch, tmp_path)
    _publish_sung(tmp_path, cf, slug=app_song.slug)
    reply = client.post(f"/api/songs/{app_song.slug}/publish", json={"choir": "jm"})
    assert reply.status_code == 200, reply.text
    data = _job_done(client, app_song.slug)
    assert data["jobs"]["publish"]["status"] == "failed"
    error = data["publish"]["error"]
    assert error["kind"] == "sung_on_site" and error["choir"] == "jm"
    [(_slug, _title, _prefix, parts)] = cf.listed("jm")
    assert all(p["sung"] for p in json.loads(parts))
    assert "jm" not in (data["publish"].get("sites") or {})
