"""Taking the cleaned score away to MuseScore and bringing the fixed one back.

The Fix stage's only editing route was `open-score`, which shells out to `open -a`
on the host the app runs on. From a phone — or from any other computer — that is a
button that does nothing you can see, so a score could be read and never repaired.

What these pin is the pair: the file comes down under its own name, and what goes
back up is checked *before* anything is replaced. That order is the whole risk —
this is the one route that overwrites the file every later stage is derived from,
so a refused upload has to leave the score that is there alone.
"""
import io
import os
import zipfile

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from src.song_app import server, state

# Two staves with two bars each — enough for the health check to have something to
# say, and for "did the upload actually land" to be answerable by reading it back.
SCORE = """<museScore><Score>
<Part><trackName>T1</trackName><Staff id="1"/></Part>
<Part><trackName>B1</trackName><Staff id="2"/></Part>
<Staff id="1">
  <Measure><voice>
    <Chord><durationType>whole</durationType><Note><pitch>62</pitch><tpc>16</tpc></Note></Chord>
  </voice></Measure>
  <Measure><voice><Rest><durationType>whole</durationType></Rest></voice></Measure>
</Staff>
<Staff id="2">
  <Measure><voice><Rest><durationType>whole</durationType></Rest></voice></Measure>
  <Measure><voice><Rest><durationType>whole</durationType></Rest></voice></Measure>
</Staff>
</Score></museScore>"""

# The same score with the tenor's whole note written as a half — "the fix made in
# MuseScore", and something we can look for in the file afterwards.
FIXED = SCORE.replace("<durationType>whole</durationType><Note>",
                      "<durationType>half</durationType><Note>", 1)

CLEANED = "talviuni_cleaned.mscx"


@pytest.fixture
def client(tmp_path, monkeypatch):
    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    # The score preview is not under test; keep MuseScore out of it.
    monkeypatch.setenv("MUSESCORE_CLI_PATH", str(tmp_path / "no-such-musescore"))
    song = state.create("Talviuni", per_system=False)
    with open(song.path(CLEANED), "w", encoding="utf-8") as fh:
        fh.write(SCORE)
    song.data["cleaned"] = CLEANED
    song.data["cleaned_fingerprint"] = state.file_fingerprint(song.cleaned_path())
    song.set_stage("fix")
    song.save()
    return TestClient(server.app), song


def _mscz(inner_name: str = "score.mscx", body: str = FIXED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(inner_name, body)
    return buf.getvalue()


def _upload(client, slug, name, body):
    return client.post(f"/api/songs/{slug}/score-file",
                       files={"file": (name, body, "application/octet-stream")})


def _cleaned_text(song):
    with open(song.cleaned_path(), encoding="utf-8") as fh:
        return fh.read()


# ---- down ----------------------------------------------------------------
def test_the_score_comes_down_under_its_own_name(client):
    client, song = client
    r = client.get(f"/api/songs/{song.slug}/score-file")

    assert r.status_code == 200
    assert r.text == SCORE                       # the file itself, not a rendering
    # The name matters: this file goes to a laptop that has other songs on it.
    assert CLEANED in r.headers["content-disposition"]
    assert "attachment" in r.headers["content-disposition"]


def test_a_song_with_no_cleaned_score_has_nothing_to_download(client):
    client, song = client
    del song.data["cleaned"]
    song.save()

    assert client.get(f"/api/songs/{song.slug}/score-file").status_code == 400


# ---- and back ------------------------------------------------------------
def test_an_uploaded_mscx_replaces_the_score_and_is_re_checked(client):
    client, song = client
    before = song.data["cleaned_fingerprint"]

    r = _upload(client, song.slug, "talviuni_cleaned.mscx", FIXED)

    assert r.status_code == 200
    assert _cleaned_text(song) == FIXED
    data = r.json()
    assert data["cleaned_fingerprint"] != before
    # Re-checked against what was just uploaded, not against what it replaced —
    # the same thing the file watcher does for an edit saved on this host.
    assert data["health"]["checked_against"] == data["cleaned_fingerprint"]


def test_an_mscz_is_accepted_because_that_is_what_save_as_writes(client):
    client, song = client

    r = _upload(client, song.slug, "talviuni_cleaned.mscz", _mscz())

    assert r.status_code == 200
    assert _cleaned_text(song) == FIXED          # unzipped, not stored as a zip


def test_an_approval_given_against_the_old_score_lapses(client):
    client, song = client
    song.data["review"] = {"approved_against": song.data["cleaned_fingerprint"]}
    song.save()

    data = _upload(client, song.slug, "talviuni_cleaned.mscx", FIXED).json()

    assert data["review"]["approved_against"] != data["cleaned_fingerprint"]


# ---- what must not be replaced -------------------------------------------
@pytest.mark.parametrize("name,body", [
    ("talviuni.pdf", b"%PDF-1.4 not a score at all"),      # the wrong file entirely
    ("talviuni_cleaned.mscx", b"<museScore><Score>"),       # truncated by the transfer
    ("talviuni_cleaned.mscx", b"<score-partwise/>"),        # MusicXML, not MuseScore
    ("talviuni_cleaned.mscx", b"<museScore><Score/></museScore>"),   # a score with no music
    ("talviuni_cleaned.mscz", b"PK\x03\x04 not really a zip"),
])
def test_a_refused_upload_leaves_the_score_that_is_there_alone(client, name, body):
    client, song = client

    r = _upload(client, song.slug, name, body)

    assert r.status_code == 400
    assert r.json()["detail"]                    # and it says why
    assert _cleaned_text(song) == SCORE


def test_an_mscz_with_no_score_inside_it_is_refused(client):
    client, song = client
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("META-INF/container.xml", "<container/>")

    assert _upload(client, song.slug, "x.mscz", buf.getvalue()).status_code == 400
    assert _cleaned_text(song) == SCORE


def test_nothing_is_replaced_underneath_a_running_job(client):
    client, song = client
    from src.song_app import job_state
    job_state.start(song.dir, "render")

    r = _upload(client, song.slug, "talviuni_cleaned.mscx", FIXED)

    assert r.status_code == 409
    assert _cleaned_text(song) == SCORE


def test_an_upload_to_a_song_with_no_cleaned_score_is_refused(client):
    client, song = client
    os.remove(song.cleaned_path())

    assert _upload(client, song.slug, "x.mscx", FIXED).status_code == 400
