"""The Upload panel's Local videos section in a real browser (#371).

YouTube's playlists are answered with `page.route` and the upload check by a
monkeypatched `confirm_uploads`, so nothing signs in anywhere. Screenshots go to
`EVIDENCE_DIR` when the run names one.
"""
import json
import os
import re
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

from src.song_app import server, state  # noqa: E402

pytestmark = pytest.mark.browser

PARTS = ["T1", "T2", "B1", "B2", "ALL"]


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _shot(page, name):
    where = os.environ.get("EVIDENCE_DIR")
    if where:
        os.makedirs(where, exist_ok=True)
        page.screenshot(path=os.path.join(where, name))


import datetime  # noqa: E402

LATER = (datetime.datetime.now(datetime.timezone.utc)
         + datetime.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make(name, uploaded):
    song = state.create(name, per_system=False)
    vdir = song.media_path("video")
    os.makedirs(vdir)
    uploads = []
    for part in PARTS:
        path = os.path.join(vdir, f"{song.slug} {part}.mp4")
        with open(path, "wb") as f:
            f.truncate(150 * 2**20)  # sparse: the size a 4K voice has
        if part in uploaded:
            st = os.stat(path)
            uploads.append({"title": f"{name} {part}", "part": part,
                            "video_id": f"vid{part}", "url": f"https://youtu.be/vid{part}",
                            "file": os.path.basename(path), "size": st.st_size,
                            "mtime_ns": st.st_mtime_ns})
    song.data["stage"] = "upload"
    song.data["record"] = {"outputs": [f"{song.slug} {p}.mp4" for p in PARTS],
                           "uploads": uploads}
    song.save()
    return song.slug


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    from src.stemmanauha import upload_to_youtube
    tmp = tmp_path_factory.mktemp("freevideos")
    songs = tmp / "songs"
    songs.mkdir()
    previous_dir, state.SONGS_DIR = state.SONGS_DIR, str(songs)
    previous_cli = os.environ.get("MUSESCORE_CLI_PATH")
    os.environ["MUSESCORE_CLI_PATH"] = str(tmp / "no-musescore-here")
    previous_confirm = upload_to_youtube.confirm_uploads
    upload_to_youtube.confirm_uploads = lambda ids, log=None: {
        i: {"processed": True, "published_at": LATER} for i in ids}
    slugs = {"all": _make("Virta venhettä vie", PARTS),
             "some": _make("Annin laulu", ["T1", "T2"])}

    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    try:
        thread.start()
        deadline = time.time() + 30
        while not srv.started and time.time() < deadline:
            time.sleep(0.05)
        assert srv.started, "the app did not start"
        yield f"http://127.0.0.1:{port}", slugs
    finally:
        srv.should_exit = True
        thread.join(timeout=10)
        state.SONGS_DIR = previous_dir
        upload_to_youtube.confirm_uploads = previous_confirm
        if previous_cli is None:
            os.environ.pop("MUSESCORE_CLI_PATH", None)
        else:
            os.environ["MUSESCORE_CLI_PATH"] = previous_cli


def _open(page, base, slug):
    page.route(re.compile(r"/api/songs/[^/]+/playlists$"),
               lambda route: route.fulfill(json={"total": 5, "playlists": []}))
    page.goto(f"{base}/#/song/{slug}")
    expect(page.get_by_role("heading", name="Local videos")).to_be_visible()
    page.locator(".free-note").scroll_into_view_if_needed()


def test_free_space_deletes_the_videos_and_keeps_the_links(live, page):
    base, slugs = live
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": 1280, "height": 900})
    _open(page, base, slugs["all"])
    button = page.locator("button.free-videos")
    expect(button).to_have_text("Free space (0.73 GB)")
    expect(page.locator(".videostate li")).to_have_count(5)
    expect(page.locator(".videostate li").first).to_contain_text("on YouTube · on disk (150 MB)")
    _shot(page, "1-free-space-before.png")

    page.once("dialog", lambda d: d.accept())
    button.click()
    expect(page.locator(".free-note")).to_contain_text("Freed 0.73 GB")
    expect(page.locator("button.free-videos")).to_be_disabled()
    expect(page.locator(".videostate li").first).to_contain_text("not on disk")
    expect(page.locator("ul.uploads a")).to_have_count(5)  # the links stay
    expect(page.get_by_text("record again to make them", exact=False)).to_be_visible()
    assert not os.listdir(state.load(slugs["all"]).media_path("video"))
    page.locator(".free-note").scroll_into_view_if_needed()
    _shot(page, "2-free-space-after.png")
    assert not errors, errors


def test_a_song_not_fully_uploaded_cannot_be_freed(live, page):
    base, slugs = live
    page.set_viewport_size({"width": 390, "height": 844})
    _open(page, base, slugs["some"])
    expect(page.locator("button.free-videos")).to_be_disabled()
    expect(page.locator(".videostate li.not_uploaded")).to_have_count(3)
    expect(page.locator(".free-note")).to_contain_text("Every video has to be on YouTube")
    _shot(page, "3-free-space-partial-phone.png")
    assert len(os.listdir(state.load(slugs["some"]).media_path("video"))) == 5
