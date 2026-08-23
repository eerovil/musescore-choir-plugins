"""
Browser test of the song app: the clean → lyrics journey, driven through the real UI.

This is the layer the Python tests can't reach — the vanilla-JS SPA in
`src/song_app/static/app.js`, where the per-system grid's answers are typed and where
import mismatches are attached to the cell that caused them.

Needs Playwright, which is not part of the default install:

    .venv/bin/pip install pytest-playwright
    .venv/bin/playwright install chromium

Without it the whole module skips, so the normal test command is unaffected.
"""

import os
import re
import socket
import threading
import time

import pytest

_NEEDS = "pip install pytest-playwright && playwright install chromium"
pytest.importorskip("playwright.sync_api", reason=_NEEDS)
pytest.importorskip("pytest_playwright", reason=_NEEDS)  # supplies the `page` fixture

import uvicorn
from playwright.sync_api import expect

from src.clean_score.tests.test_per_system import ANSWERS
from src.clean_score.utils.per_system import use_answer_file

FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "clean_score", "tests", "test_files", "laulun_aika.mscx",
)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_app(tmp_path_factory):
    """The real server, on its own port, with its own songs folder and answer file."""
    from src.song_app import server, state

    tmp = tmp_path_factory.mktemp("songapp")
    songs = tmp / "songs"
    songs.mkdir()
    previous_dir, state.SONGS_DIR = state.SONGS_DIR, str(songs)
    # Point the renderer at nothing: the score previews are not under test, and a real
    # MuseScore run would add seconds per page and a dependency on the host's install.
    previous_cli = os.environ.get("MUSESCORE_CLI_PATH")
    os.environ["MUSESCORE_CLI_PATH"] = str(tmp / "no-musescore-here")

    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(
        server.app, host="127.0.0.1", port=port, log_level="warning",
    ))
    thread = threading.Thread(target=srv.run, daemon=True)
    with use_answer_file(str(tmp / "answers.json")):
        thread.start()
        deadline = time.time() + 30
        while not srv.started and time.time() < deadline:
            time.sleep(0.05)
        assert srv.started, "the app did not start"
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            srv.should_exit = True
            thread.join(timeout=10)
            state.SONGS_DIR = previous_dir
            if previous_cli is None:
                os.environ.pop("MUSESCORE_CLI_PATH", None)
            else:
                os.environ["MUSESCORE_CLI_PATH"] = previous_cli


def _new_song(page, base, name, per_system=True):
    """Walk the New song form and land in the workspace."""
    page.goto(base)
    page.get_by_role("button", name="+ New song").click()
    page.get_by_placeholder("Song name").fill(name)
    page.locator("input[type=file]").first.set_input_files(FIXTURE)
    if per_system:
        page.locator("input[type=checkbox]").check()
    page.get_by_role("button", name="Create").click()
    expect(page.locator(".stagebar")).to_be_visible()


def test_per_system_answers_clean_the_score_and_lyrics_land_on_their_cell(live_app, page):
    """The whole journey: create → answer the grid → clean → type lyrics → see the mismatch."""
    _new_song(page, live_app, "Laulun aika")

    # --- the per-system grid: one block per printed system, staves that sound in it ---
    expect(page.locator(".sysblock")).to_have_count(7)
    first = page.locator(".sysblock").first
    expect(first.locator("h4")).to_contain_text("System 1 — measures 1–6")
    expect(first.locator("input[data-sys]")).to_have_count(2)

    # Answer every staff of every system exactly as the fixture reads.
    for system, staves in ANSWERS.items():
        for staff_id, answer in staves.items():
            page.locator(f'input[data-sys="{system}"][data-staff="{staff_id}"]').fill(answer)

    page.get_by_role("button", name="Save assignments").click()
    expect(page.get_by_role("button", name="Saved ✓")).to_be_visible()

    # --- clean: the server works in the background and pings the page when done ---
    page.get_by_role("button", name="Run clean").click()
    # The panel re-renders on the state ping, so wait for what that leaves behind:
    # the button now offers a re-clean, and the Clean step is marked done.
    expect(page.get_by_role("button", name="Re-clean (discards manual edits)")).to_be_visible(
        timeout=60_000
    )
    expect(page.locator(".stagebar .step", has_text="Clean")).to_have_class(re.compile(r"\bdone\b"))

    # --- lyrics: type one short line into the first system's top part ---
    page.locator(".stagebar .step", has_text="Lyrics").click()
    page.get_by_role("button", name="Type by system").click()
    cell = page.locator('textarea[data-sys="0"][data-part="T1"]')
    expect(cell).to_be_visible()
    cell.fill("yk")  # one syllable for a whole system: too few
    page.get_by_role("button", name="Import lyrics").click()

    # The mismatch is attached to the cell that caused it, and says what is wrong.
    warning = page.locator(".lyrow", has=page.locator('textarea[data-part="T1"]')).locator(".lyerr")
    expect(warning.first).to_be_visible(timeout=30_000)
    expect(warning.first).to_contain_text("too few tokens")
    expect(warning.first).to_contain_text("m1–")
    # ...and only there: the parts nobody typed into carry no warning.
    other = page.locator(".lyrow", has=page.locator('textarea[data-part="T2"]')).first
    expect(other.locator(".lyerr")).to_have_count(0)

    # What was typed survives the re-render, read back out of the score.
    expect(page.locator('textarea[data-sys="0"][data-part="T1"]')).to_have_value("yk")


def test_grid_marks_cleared_and_inherited_staves(live_app, page):
    """A blank cell inherits the staff's previous answer; '-' says it is silent."""
    _new_song(page, live_app, "Grid rules")

    staff1 = lambda system: page.locator(f'input[data-sys="{system}"][data-staff="1"]')
    staff1(0).fill("T1,T2")
    staff1(1).fill("")           # blank: inherits
    staff1(2).fill("-")          # cleared from here on
    staff1(3).fill("")           # still cleared, not inherited from system 1
    staff1(0).blur()

    # The inherited cell shows what it will inherit and is not flagged.
    expect(staff1(1)).to_have_attribute("placeholder", "T1,T2")
    expect(staff1(1)).not_to_have_class(re.compile(r"\bunset\b"))
    # The cleared cell and the blank one after it are both flagged as dropped.
    expect(staff1(2)).to_have_class(re.compile(r"\bunset\b"))
    expect(staff1(3)).to_have_class(re.compile(r"\bunset\b"))

    # Cleaning warns about exactly those dropped slots before it runs.
    dropped = []
    page.on("dialog", lambda d: (dropped.append(d.message), d.dismiss()))
    page.get_by_role("button", name="Run clean").click()
    deadline = time.time() + 10
    while not dropped and time.time() < deadline:
        page.wait_for_timeout(100)
    assert dropped, "cleaning with unnamed staves must confirm first"
    assert "staff 1 · system 3" in dropped[0], dropped[0]
    assert "staff 1 · system 4" in dropped[0], dropped[0]
