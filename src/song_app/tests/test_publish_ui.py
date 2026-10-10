"""The Upload panel's New site section in a real browser (#382).

The server runs in-process, so Cloudflare is the same `FakeCloudflare` the unit
tests use (R2 a dict, D1 SQLite) and the MuseScore half of the publish is a
stub. What is pinned is the person's path: the choir is offered (guessed from
the song's voicing), Publish runs, and the panel then says where the song went —
and the fake D1 says the same. Screenshots go to `EVIDENCE_DIR` when the run
names one.
"""
import os
import socket
import threading
import time

import pytest

_NEEDS = "pip install pytest-playwright && playwright install chromium"
pytest.importorskip("playwright.sync_api", reason=_NEEDS)
pytest.importorskip("pytest_playwright", reason=_NEEDS)
pytest.importorskip("uvicorn")


def _browser_installed() -> bool:
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


if not _browser_installed():
    pytest.skip(_NEEDS, allow_module_level=True)

import uvicorn  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

from src.song_app import publish, server, state  # noqa: E402
from src.song_app.tests.test_publish import FERMATA, FakeCloudflare, _bundle  # noqa: E402

pytestmark = pytest.mark.browser

ENV = {"CLOUDFLARE_ACCOUNT_ID": "acct", "CLOUDFLARE_API_TOKEN": "tok",
       "STEMMANAUHAT_D1_DATABASE_ID": "db1", "AGENTDECK_API_URL": None,
       "AGENTDECK_URL": None, "MUSESCORE_CLI_PATH": "/no-musescore-here"}


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _shot(page, name):
    where = os.environ.get("EVIDENCE_DIR")
    if where:
        os.makedirs(where, exist_ok=True)
        page.screenshot(path=os.path.join(where, name))


@pytest.fixture
def live(tmp_path, monkeypatch):
    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    for key, value in ENV.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    cf = FakeCloudflare()
    monkeypatch.setattr(publish.urllib.request, "urlopen", cf)
    monkeypatch.setattr(publish, "build_bundle",
                        lambda cleaned, out, initial_bpm=None, log=None: _bundle(
                            tmp_path, parts=("T1", "T2", "B1", "B2")))

    song = state.create("Hanget soi", per_system=False, voicing="men")
    cleaned = song.path("hanget_cleaned.mscx")
    with open(FERMATA, "rb") as src, open(cleaned, "wb") as dst:
        dst.write(src.read())
    song.data.update(cleaned=os.path.basename(cleaned), stage="upload",
                     review={"approved_against": state.file_fingerprint(cleaned)})
    song.save()

    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not srv.started and time.time() < deadline:
        time.sleep(0.05)
    assert srv.started, "the app did not start"
    try:
        yield f"http://127.0.0.1:{port}", song.slug, cf
    finally:
        srv.should_exit = True
        thread.join(timeout=10)


@pytest.mark.parametrize("size", [(1280, 900), (390, 844)], ids=["desktop", "phone"])
def test_publish_lists_the_song_for_the_chosen_choir(live, page, size):
    base, slug, cf = live
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/api/songs/*/playlists", lambda route: route.fulfill(
        json={"total": 0, "playlists": []}))
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(f"{base}/#/song/{slug}")

    section = page.locator(".publish-section")
    expect(section.get_by_role("heading", name="New site (Cloudflare)")).to_be_visible()
    choir = section.locator("select.publish-choir")
    expect(choir).to_have_value("jm")  # a men's choir song
    expect(section.locator(".publish-status")).to_have_text("Not published yet.")
    section.scroll_into_view_if_needed()
    _shot(page, f"publish-before-{size[0]}.png")

    section.locator("button.publish-btn").click()
    status = page.locator(".publish-section .publish-status")
    expect(status).to_contain_text("Published to Joensuun Mieslaulajat (jm)",
                                   timeout=15000)
    expect(status).to_contain_text("4 part(s)")
    expect(page.locator(".publish-section button.publish-btn")).to_have_text("Publish again")
    assert [row[0] for row in cf.listed("jm")] == [slug]
    page.locator(".publish-section").scroll_into_view_if_needed()
    _shot(page, f"publish-after-{size[0]}.png")
    assert errors == []


@pytest.mark.parametrize("size", [(1280, 900), (390, 844)], ids=["desktop", "phone"])
def test_a_song_published_to_public_and_a_choir_shows_both(live, page, size):
    base, slug, cf = live
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/api/songs/*/playlists", lambda route: route.fulfill(
        json={"total": 0, "playlists": []}))
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(f"{base}/#/song/{slug}")

    section = page.locator(".publish-section")
    status = section.locator(".publish-status")
    section.locator("button.publish-btn").click()
    expect(status).to_contain_text("Published to Joensuun Mieslaulajat (jm)", timeout=15000)

    section = page.locator(".publish-section")
    section.locator("select.publish-choir").select_option("public")
    section.locator("button.publish-btn").click()
    status = page.locator(".publish-section .publish-status")
    expect(status).to_contain_text("Published to Public demo", timeout=15000)
    expect(status).to_contain_text("Published to Joensuun Mieslaulajat (jm)")
    assert [row[0] for row in cf.listed("jm")] == [slug]
    assert [row[0] for row in cf.listed("public")] == [slug]
    page.locator(".publish-section").scroll_into_view_if_needed()
    _shot(page, f"publish-public-and-jm-{size[0]}.png")
    assert errors == []
