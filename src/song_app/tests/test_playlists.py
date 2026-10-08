"""Putting an uploaded song into a playlist afterwards, or taking it out (#338).

A fake YouTube stands in for the API, small enough to page: the helpers in
upload_to_youtube are run for real against it, so paging and the item ids a
removal needs are exercised, not stubbed.
"""

import json
import os

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from src.song_app import playlists, server, site_refresh, state  # noqa: E402
from src.stemmanauha import upload_to_youtube as yt  # noqa: E402

CHOIR = "PLchoir"
WOMEN = "PLwomen"
OWN = "PLsongown"
HOBBY = "PLkazoo"


class _Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeYouTube:
    """Playlists and their items, paged two at a time so paging is exercised."""

    PAGE = 2

    def __init__(self):
        self.titles = {CHOIR: "Stemmanauhat", WOMEN: "Naiskuoron stemmanauhat",
                       OWN: "Laulu Stemmanauhat - 2026-10-08 20:50", HOBBY: "kazoo"}
        self.items = {pid: [] for pid in self.titles}  # [(item id, video id)]
        self.writes = []
        self._next = 0

    def _page(self, rows, token):
        start = int(token or 0)
        page = rows[start:start + self.PAGE]
        out = {"items": page}
        if start + self.PAGE < len(rows):
            out["nextPageToken"] = str(start + self.PAGE)
        return out

    def put(self, pid, vid):
        self._next += 1
        self.items[pid].append((f"item{self._next}", vid))

    # --- the API surface the app uses -------------------------------------
    def playlists(self):
        fake = self

        class P:
            def list(self, part, mine, maxResults, pageToken=None):
                rows = [{"id": k, "snippet": {"title": v}} for k, v in fake.titles.items()]
                return _Call(lambda: fake._page(rows, pageToken))
        return P()

    def playlistItems(self):
        fake = self

        class I:
            def list(self, part, playlistId, maxResults, pageToken=None):
                rows = [{"id": i, "contentDetails": {"videoId": v}}
                        for i, v in fake.items[playlistId]]
                return _Call(lambda: fake._page(rows, pageToken))

            def insert(self, part, body):
                snip = body["snippet"]

                def go():
                    fake.writes.append(("add", snip["playlistId"], snip["resourceId"]["videoId"]))
                    fake.put(snip["playlistId"], snip["resourceId"]["videoId"])
                    return {}
                return _Call(go)

            def delete(self, id):
                def go():
                    for pid, rows in fake.items.items():
                        for row in rows:
                            if row[0] == id:
                                rows.remove(row)
                                fake.writes.append(("remove", pid, row[1]))
                                return {}
                    raise AssertionError(f"no item {id}")
                return _Call(go)
        return I()


VIDEOS = ["v1", "v2", "v3", "v4", "v5"]


@pytest.fixture
def fake(tmp_path, monkeypatch):
    youtube = FakeYouTube()
    monkeypatch.setattr(playlists, "_service", lambda: youtube)
    store = tmp_path / ".playlists.json"
    store.write_text(json.dumps({
        CHOIR: CHOIR,                                           # a bare id, as today
        WOMEN: WOMEN,
        "PLold": "Vanha Stemmanauhat - 2026-08-30 06:03",      # a song's own list
    }))
    monkeypatch.setattr(state, "PLAYLISTS_FILE", str(store))
    refreshed = []
    monkeypatch.setattr(site_refresh, "refresh_stemmanauhat",
                        lambda log: refreshed.append(True) or True)
    youtube.refreshed = refreshed
    return youtube


@pytest.fixture
def song(tmp_path, monkeypatch, fake):
    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    s = state.create("Laulu", per_system=False)
    s.data["stage"] = "upload"
    s.data["record"] = {"uploads": [
        {"title": f"Laulu {v}", "video_id": v, "playlist_id": OWN,
         "playlist_title": fake.titles[OWN]} for v in VIDEOS]}
    s.save()
    for v in VIDEOS:
        fake.put(OWN, v)
    return s


@pytest.fixture
def client(song):
    return TestClient(server.app)


def _rows(client, song):
    response = client.get(f"/api/songs/{song.slug}/playlists")
    assert response.status_code == 200, response.text
    return {r["id"]: r for r in response.json()["playlists"]}


def test_the_ticks_are_counted_from_youtube(client, song, fake):
    for v in VIDEOS[:3]:
        fake.put(WOMEN, v)
    fake.put(WOMEN, "somebody-else")

    rows = _rows(client, song)

    assert set(rows) == {CHOIR, WOMEN}, "the song's own playlists are not offered"
    assert (rows[WOMEN]["count"], rows[WOMEN]["total"]) == (3, 5)
    assert (rows[CHOIR]["count"], rows[CHOIR]["total"]) == (0, 5)
    assert rows[CHOIR]["title"] == "Stemmanauhat", "a bare id gets its YouTube name"
    stored = json.loads(open(state.PLAYLISTS_FILE).read())
    assert stored[CHOIR] == "Stemmanauhat"


def test_ticking_adds_only_the_missing_videos_in_part_order(client, song, fake):
    fake.put(WOMEN, "v2")

    response = client.post(f"/api/songs/{song.slug}/playlists",
                           json={"playlist_id": WOMEN, "member": True})

    assert response.status_code == 200, response.text
    assert response.json()["count"] == 5
    assert fake.writes == [("add", WOMEN, v) for v in ["v1", "v3", "v4", "v5"]]
    assert fake.refreshed, "the site is asked to refresh"


def test_unticking_removes_only_this_songs_videos(client, song, fake):
    for v in VIDEOS + ["other-song"]:
        fake.put(WOMEN, v)

    response = client.post(f"/api/songs/{song.slug}/playlists",
                           json={"playlist_id": WOMEN, "member": False})

    assert response.status_code == 200, response.text
    assert response.json()["count"] == 0
    assert [v for _, v in fake.items[WOMEN]] == ["other-song"]
    assert len(fake.items[OWN]) == 5, "the song's own playlist is untouched"


def test_the_songs_own_playlist_is_refused(client, song, fake):
    response = client.post(f"/api/songs/{song.slug}/playlists",
                           json={"playlist_id": OWN, "member": False})

    assert response.status_code == 400
    assert len(fake.items[OWN]) == 5
    assert not fake.writes


def test_nothing_uploaded_is_refused(client, song, fake):
    song.data["record"]["uploads"] = []
    song.save()

    assert client.get(f"/api/songs/{song.slug}/playlists").status_code == 400
    response = client.post(f"/api/songs/{song.slug}/playlists",
                           json={"playlist_id": WOMEN, "member": True})
    assert response.status_code == 400
    assert not fake.writes


def test_a_running_upload_is_not_raced(client, song, fake, monkeypatch):
    monkeypatch.setattr(server, "is_recording", lambda s: True)

    response = client.post(f"/api/songs/{song.slug}/playlists",
                           json={"playlist_id": WOMEN, "member": True})

    assert response.status_code == 409
    assert not fake.writes


def test_the_account_list_leaves_out_per_song_playlists(client, song, fake):
    response = client.get("/api/youtube-playlists")

    assert response.status_code == 200
    got = {p["id"]: p["chosen"] for p in response.json()}
    assert got == {CHOIR: True, WOMEN: True, HOBBY: False}


def test_the_upload_picker_no_longer_offers_per_song_playlists(client, song):
    ids = [p["id"] for p in client.get("/api/playlists").json()]
    assert ids == [CHOIR, WOMEN]


def test_adding_and_hiding_only_change_what_is_offered(client, song, fake):
    client.post("/api/playlists", json={"playlist_id": HOBBY, "title": "kazoo"})
    assert HOBBY in _rows(client, song)

    response = client.delete(f"/api/playlists/{HOBBY}")

    assert response.status_code == 200
    assert HOBBY not in _rows(client, song)
    assert HOBBY in fake.titles and not fake.writes, "nothing changed on YouTube"


def test_an_upload_no_longer_remembers_the_songs_own_playlist(monkeypatch, tmp_path):
    store = tmp_path / ".playlists.json"
    monkeypatch.setattr(state, "PLAYLISTS_FILE", str(store))
    state.save_playlist("PLx", "Uusi Stemmanauhat - 2026-10-09 10:00")
    assert state.load_playlists() == []
    assert state.is_song_playlist("Uusi Stemmanauhat - 2026-10-09 10:00")
    assert not state.is_song_playlist("Naiskuoron stemmanauhat")
    assert not state.is_song_playlist("Stemmanauhat")


def test_a_used_up_quota_is_said_in_words(client, song, monkeypatch):
    def quota(*a, **k):
        raise yt.QuotaExceeded("YouTube daily quota exceeded — try again after it resets.")
    monkeypatch.setattr(yt, "playlist_video_items", quota)

    response = client.post(f"/api/songs/{song.slug}/playlists",
                           json={"playlist_id": WOMEN, "member": True})

    assert response.status_code == 503
    assert "quota" in response.json()["detail"]


def test_a_playlist_that_cannot_be_read_costs_its_own_row(client, song, fake):
    state.save_playlist("PLgone", "Poistettu")

    rows = _rows(client, song)

    assert "error" in rows["PLgone"]
    assert rows[WOMEN]["total"] == 5 and "error" not in rows[WOMEN]
