"""The installable shell and offline recovery in a real Chromium browser."""

from __future__ import annotations

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
        with sync_playwright() as playwright:
            playwright.chromium.launch().close()
        return True
    except Exception:
        return False


if not _browser_installed():
    pytest.skip(_NEEDS, allow_module_level=True)

import uvicorn
from playwright.sync_api import expect

from src.song_app import server, state

pytestmark = pytest.mark.browser


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def live_app(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("songapp-pwa")
    songs = tmp / "songs"
    songs.mkdir()
    previous_dir, state.SONGS_DIR = state.SONGS_DIR, str(songs)
    previous_cli = os.environ.get("MUSESCORE_CLI_PATH")
    os.environ["MUSESCORE_CLI_PATH"] = str(tmp / "no-musescore-here")

    port = _free_port()
    running = uvicorn.Server(uvicorn.Config(
        server.app, host="127.0.0.1", port=port, log_level="warning",
    ))
    thread = threading.Thread(target=running.run, daemon=True)
    try:
        thread.start()
        deadline = time.time() + 30
        while not running.started and time.time() < deadline:
            time.sleep(0.05)
        assert running.started, "the app did not start"
        yield f"http://127.0.0.1:{port}"
    finally:
        running.should_exit = True
        thread.join(timeout=10)
        state.SONGS_DIR = previous_dir
        if previous_cli is None:
            os.environ.pop("MUSESCORE_CLI_PATH", None)
        else:
            os.environ["MUSESCORE_CLI_PATH"] = previous_cli


def _warm_pwa(page, base: str) -> None:
    page.goto(base + "/#/")
    registration = page.evaluate("""async () => {
        const registration = await navigator.serviceWorker.ready;
        return {
          active: Boolean(registration.active),
          scope: registration.scope,
        };
    }""")
    assert registration == {"active": True, "scope": base + "/"}
    page.reload()
    page.wait_for_function("() => navigator.serviceWorker.controller !== null")
    page.wait_for_function(
        "() => caches.match('/offline.html').then((hit) => Boolean(hit))"
    )


def test_chromium_parses_the_manifest_and_activates_a_root_worker(live_app, page):
    _warm_pwa(page, live_app)

    manifest = page.evaluate("""async () => {
        const link = document.querySelector('link[rel="manifest"]');
        const response = await fetch(link.href);
        return { href: link.href, data: await response.json() };
    }""")
    assert manifest["href"] == live_app + "/manifest.webmanifest"
    assert manifest["data"]["display"] == "standalone"
    assert manifest["data"]["scope"] == "/"
    assert {icon["sizes"] for icon in manifest["data"]["icons"]} >= {"192x192", "512x512"}

    cdp = page.context.new_cdp_session(page)
    parsed = cdp.send("Page.getAppManifest")
    assert parsed["url"] == live_app + "/manifest.webmanifest"
    assert parsed.get("errors", []) == []


def test_offline_reload_shows_reconnecting_shell_then_returns_to_live_app(live_app, page):
    _warm_pwa(page, live_app)

    cached = page.evaluate("""async () => ({
        offline: Boolean(await caches.match('/offline.html')),
        api: Boolean(await caches.match('/api/songs')),
        health: Boolean(await caches.match('/healthz')),
    })""")
    assert cached == {"offline": True, "api": False, "health": False}

    page.context.set_offline(True)
    try:
        page.reload(wait_until="domcontentloaded")
        expect(page.get_by_role("heading", name="Reconnecting to Choir tracks…")).to_be_visible()

        result = page.evaluate("""async () => {
            try {
              await fetch('/api/songs');
              return 'resolved';
            } catch (_) {
              return 'rejected';
            }
        }""")
        assert result == "rejected", "live API data must not receive a cached fallback"
    finally:
        page.context.set_offline(False)

    page.wait_for_selector(".brand", timeout=15_000)
    expect(page.locator(".brand")).to_have_text("♪ song")
    assert page.url.endswith("/#/"), "the reconnecting shell must return to the requested SPA URL"
