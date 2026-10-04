"""The Scan panel's homr box, in a real browser (#249, moved off the Library by #261).

The rules are pinned in test_homr_install.py. What only exists here is whether a
person on a phone can see that a newer homr is on GitHub, press one button, watch
the install's log, and see what it ended on. A stub stands in for the installer.
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
    """Launching is the only honest check; see test_ui_flow for why it runs here."""
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


if not _browser_installed():
    pytest.skip(_NEEDS, allow_module_level=True)

import uvicorn

from src.song_app import homr_install, omr, server, state

pytestmark = pytest.mark.browser

OLD, NEW = "d5cd49a" + "0" * 33, "1a2b3c4" + "0" * 33


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live(tmp_path, monkeypatch):
    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    monkeypatch.setenv("MUSESCORE_CLI_PATH", str(tmp_path / "no-musescore-here"))
    monkeypatch.delenv("AGENTDECK_API_URL", raising=False)
    monkeypatch.delenv("AGENTDECK_URL", raising=False)
    song = state.create("Talviuni", per_system=False)
    with open(song.path("scan.pdf"), "wb") as fh:
        fh.write(b"%PDF-1.4 not really a pdf\n")
    song.data["sources"]["pdf"] = "scan.pdf"
    song.set_stage("scan")
    song.save()

    installed = {"commit": OLD}
    monkeypatch.setattr(omr, "default_engine", lambda: installed["commit"] and omr.Engine(
        key="default", label=f"installed: main @ {installed['commit'][:7]}",
        command=["homr"], default=True, commit=installed["commit"]))
    # This host's own homr checkouts are not what is under test; without this the
    # Scan panel offers them in its "Read with" picker.
    monkeypatch.setattr(omr, "engines", lambda: [e for e in [omr.default_engine()] if e])
    monkeypatch.setattr(homr_install, "_ls_remote", lambda _url: NEW)
    monkeypatch.setitem(homr_install._latest, "commit", None)
    monkeypatch.setitem(homr_install._latest, "at", 0.0)
    homr_install._log.clear()
    homr_install._result.clear()
    go = tmp_path / "go"
    script = tmp_path / "install-homr.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'echo "Creating $HOMR_VENV (python 3.12)"\n'
        'echo "Installing homr[cpu] @ git+https://github.com/eerovil/homr.git@main"\n'
        f"while [ ! -e {go} ]; do sleep 0.05; done\n"
        'echo "Downloading model weights (once; ~150 MB)"\n'
        'echo "homr installed"\n', encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setattr(homr_install, "SCRIPT", str(script))

    def finish():
        installed["commit"] = NEW   # what the real script leaves behind
        go.touch()

    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(
        server.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    try:
        thread.start()
        deadline = time.time() + 30
        while not srv.started and time.time() < deadline:
            time.sleep(0.05)
        assert srv.started, "the app did not start"
        yield f"http://127.0.0.1:{port}/#/song/{song.slug}", finish, installed
    finally:
        go.touch()
        srv.should_exit = True
        thread.join(timeout=10)


def _evidence(page, name):
    """Screenshot only where the run was told to put one; none is committed here."""
    where = os.environ.get("EVIDENCE_DIR")
    if where:
        os.makedirs(where, exist_ok=True)
        page.screenshot(path=os.path.join(where, name), full_page=True)


def _update_walk(page, base, finish, prefix):
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base)
    box = page.locator(".homr-box")
    box.locator("summary").click()
    page.wait_for_selector("text=newer on GitHub: main @ 1a2b3c4")
    assert "main @ d5cd49a" in box.inner_text()
    _evidence(page, f"{prefix}-1-newer-available.png")

    box.get_by_role("button", name="Update homr").click()
    page.wait_for_selector(".homr-log >> text=Installing homr[cpu]")
    assert box.get_by_role("button", name="Installing…").is_disabled()
    _evidence(page, f"{prefix}-2-installing.png")

    finish()
    page.wait_for_selector("text=Installed main @ 1a2b3c4 — up to date", timeout=15000)
    assert box.get_by_role("button", name="Update homr").count() == 0
    assert "homr installed" in box.locator(".homr-log").inner_text()
    _evidence(page, f"{prefix}-3-installed.png")
    assert not errors, f"the page raised: {errors}"


def test_a_newer_homr_is_installed_from_the_scan_panel(live, page):
    base, finish, _ = live
    page.set_viewport_size({"width": 1280, "height": 800})
    _update_walk(page, base, finish, "issue-261-desktop")


def test_the_library_no_longer_carries_it(live, page):
    base, _, _ = live
    page.goto(base.split("#")[0])
    page.wait_for_selector(".lib .card")
    assert page.locator(".homr-box").count() == 0


def test_not_installed_is_said_open_with_the_button(live, page):
    base, _, installed = live
    installed["commit"] = None
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(base)
    page.wait_for_selector(".homr-box >> text=homr: not installed")
    box = page.locator(".homr-box")
    assert box.evaluate("b => b.open"), "a missing homr is not hidden in a closed box"
    assert box.get_by_role("button", name="Install homr").is_visible()
    _evidence(page, "issue-261-not-installed.png")


def test_it_fits_a_phone(live, page):
    base, finish, _ = live
    page.set_viewport_size({"width": 390, "height": 844})
    _update_walk(page, base, finish, "issue-261-phone")
    width = page.evaluate("document.querySelector('.homr-box').getBoundingClientRect().right")
    assert width <= 390
