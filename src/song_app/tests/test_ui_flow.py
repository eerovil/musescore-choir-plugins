"""
Browser test of the song app: the clean → lyrics journey, driven through the real UI.

This is the layer the Python tests can't reach — the vanilla-JS SPA in
`src/song_app/static/app.js`, where the per-system grid's answers are typed and where
import mismatches are attached to the cell that caused them.

Needs Playwright, which is not part of the default install:

    .venv/bin/pip install pytest-playwright
    .venv/bin/playwright install chromium

The module skips unless both the packages and a browser are present, so the normal
test command is unaffected either way.
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


def _browser_installed() -> bool:
    """The pip packages are only half the install; the browser is a separate download.

    Launching is the honest check: `chromium.executable_path` names the full Chromium
    build, but a headless run starts `chrome-headless-shell`, which is downloaded
    separately and can be missing on its own — so only a real launch proves the tests
    can run.

    It runs at import, which costs a browser start (~0.5s) on every collection of this
    file, `-m "not browser"` included. Running it from a fixture instead does not work:
    pytest-playwright has its own Playwright session by then, and a second one inside it
    fails — which the guard would read as "no browser" and skip tests that would pass.
    """
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
from playwright.sync_api import expect

from src.clean_score.tests.test_per_system import ANSWERS
from src.clean_score.utils.per_system import use_answer_file

pytestmark = pytest.mark.browser

FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "clean_score", "tests", "test_files", "laulun_aika.mscx",
)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def own_answers(tmp_path):
    """Answers are keyed by the score's file name, and both tests upload the same score,
    so give each test its own file — otherwise the second one opens the first's grid."""
    with use_answer_file(str(tmp_path / "answers.json")):
        yield


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
    try:
        thread.start()
        deadline = time.time() + 30
        while not srv.started and time.time() < deadline:
            time.sleep(0.05)
        assert srv.started, "the app did not start"
        yield f"http://127.0.0.1:{port}"
    finally:
        # Everything below is global to the process, so it is restored even when the
        # app never came up — otherwise the rest of the session runs against a tmp dir.
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


def test_per_system_answers_clean_the_score_and_lyrics_land_on_their_cell(live_app, own_answers, page):
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


def test_grid_marks_cleared_and_inherited_staves(live_app, own_answers, page):
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
    # The handler has to be registered before the click: the dialog blocks the page,
    # so a wrapper that waits around the click would deadlock with it.
    dropped = []
    page.once("dialog", lambda d: (dropped.append(d.message), d.dismiss()))
    page.get_by_role("button", name="Run clean").click()
    assert dropped, "cleaning with unnamed staves must confirm first"
    assert "staff 1 · system 3" in dropped[0], dropped[0]
    assert "staff 1 · system 4" in dropped[0], dropped[0]


BOUNDS_FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "fixtures", "virta-venhetta-vie",
)


@pytest.fixture
def bounds_song():
    """Drop the Virta fixture into the live app's songs folder, cleaned stage."""
    import json
    import shutil

    from src.song_app import state

    slug = "virta-venhetta-vie"
    dest = os.path.join(state.SONGS_DIR, slug)
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest)
    for stage in ("00-registered", "10-cleaned"):
        src = os.path.join(BOUNDS_FIXTURE, stage)
        for name in os.listdir(src):
            shutil.copyfile(os.path.join(src, name), os.path.join(dest, name))
    yield slug, dest, lambda: json.load(open(os.path.join(dest, ".systems.json")))["systems"]
    shutil.rmtree(dest, ignore_errors=True)


def _open_systems_tab(page, base, slug):
    """Open the Systems tab and wait for a page image to actually be laid out.

    The bands are positioned as percentages of the image, and rasterising a page
    takes a second or two, so until it has loaded there is no geometry to drag
    against — the editor refuses the drag, exactly as it should.
    """
    page.goto(f"{base}/#/song/{slug}")
    page.get_by_role("button", name="Systems").click()
    expect(page.locator(".sysband").first).to_be_visible(timeout=30_000)
    # Every page, not just the first: each image's load fires a redraw of the
    # bands, and one arriving mid-drag replaces the element being dragged.
    page.wait_for_function(
        "() => { const i = [...document.querySelectorAll('.syspage img')];"
        "        return i.length > 0 && i.every(x => x.complete"
        "               && x.getBoundingClientRect().height > 50); }",
        timeout=90_000,
    )


def test_system_boundaries_can_be_dragged_and_saved(page, live_app, bounds_song):
    """The correction path: drag an edge, save, and it is what the song now holds."""
    slug, _, stored = bounds_song
    _open_systems_tab(page, live_app, slug)

    assert page.locator(".sysband").count() == 15
    expect(page.locator(".sysstatus")).to_contain_text("matches the score")
    before = stored()[0]["top"]

    grip = page.locator(".sysband").first.locator(".sysgrip.top")
    box = grip.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] - 40, steps=8)
    page.mouse.up()

    page.get_by_role("button", name="Save boundaries").click()
    expect(page.locator(".sysstatus")).not_to_contain_text("unsaved")

    after = stored()[0]["top"]
    assert after < before, "dragging the top edge upward should lower the fraction"
    assert stored()[0]["measure_start"] == 1, "still labelled against the score"


def test_removing_a_system_stops_the_measure_labelling(page, live_app, bounds_song):
    """14 bands cannot be aligned to 15 systems, so the app must not pretend."""
    slug, _, stored = bounds_song
    _open_systems_tab(page, live_app, slug)

    page.locator(".sysband").last.locator(".sysdel").click()
    assert page.locator(".sysband").count() == 14
    expect(page.locator(".sysstatus")).to_contain_text("the score declares 15")

    page.get_by_role("button", name="Save boundaries").click()
    expect(page.locator(".sysstatus")).not_to_contain_text("unsaved")

    saved = stored()
    assert len(saved) == 14
    assert all(b["measure_start"] == 0 for b in saved)


def test_the_lyrics_grid_shows_the_printed_system_it_is_asking_about(page, live_app, bounds_song):
    """Typing lyrics against a whole page is unreadable; each block gets its crop."""
    slug, _, _ = bounds_song
    page.goto(f"{live_app}/#/song/{slug}")
    page.locator(".step", has_text="Lyrics").first.click()
    page.get_by_role("button", name="Type by system").click()

    first = page.locator(".sysblock").first
    expect(page.locator(".syspeek")).to_have_count(0)      # off by default now
    page.get_by_role("button", name="Show the score").click()
    expect(first.locator(".syspeek img")).to_be_visible(timeout=60_000)
    page.wait_for_function(
        "() => { const i = document.querySelector('.syspeek img');"
        "        return i && i.complete && i.naturalWidth > 100; }", timeout=60_000)

    # The crop shown is the one whose measures the block is asking about.
    heading = first.locator("h4").inner_text()
    src = first.locator(".syspeek img").get_attribute("src")
    assert "measures 1–3" in heading
    assert "/system/1?" in src, f"expected system 1 for {heading}, got {src}"

    page.get_by_role("button", name="Hide the score").click()
    expect(page.locator(".syspeek")).to_have_count(0)


def test_focusing_a_lyric_cell_shows_that_system_in_the_viewer(page, live_app, bounds_song):
    """The sidebar is too narrow to read a system in; the viewer is the space."""
    slug, _, _ = bounds_song
    page.goto(f"{live_app}/#/song/{slug}")
    page.locator(".step", has_text="Lyrics").first.click()
    page.get_by_role("button", name="Type by system").click()
    expect(page.locator(".sysblock").first).to_be_visible(timeout=30_000)

    blocks = page.locator(".sysblock")
    blocks.nth(7).locator("textarea").first.focus()        # system 8, measures 27-30
    expect(page.locator(".onesystem")).to_be_visible(timeout=30_000)
    expect(page.locator(".onesystem .muted")).to_have_text("Printed system 8")
    assert "/system/8?" in page.locator(".onesystem img").get_attribute("src")

    blocks.nth(0).locator("textarea").first.focus()        # and it follows the cursor
    expect(page.locator(".onesystem .muted")).to_have_text("Printed system 1")


def test_clicking_empty_page_adds_a_system(page, live_app, bounds_song):
    """Adding a band by clicking the page, which is how a song with no proposal
    gets its boundaries at all. The overlay spans the page, so it has to let
    clicks through or this silently does nothing."""
    slug, _, stored = bounds_song
    _open_systems_tab(page, live_app, slug)
    assert page.locator(".sysband").count() == 15

    # Empty page above the first system (the title area). Kept near the top of
    # the page on purpose: further down is below the fold, and a click there goes
    # nowhere — which is a fact about the test window, not about the editor.
    img = page.locator(".syspage img").first
    box = img.bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] * 0.05)

    assert page.locator(".sysband").count() == 16
    expect(page.locator(".sysstatus")).to_contain_text("the score declares 15")

    page.get_by_role("button", name="Save boundaries").click()
    expect(page.locator(".sysstatus")).not_to_contain_text("unsaved")
    assert len(stored()) == 16


def test_the_lyrics_view_is_offered_only_once_there_are_lyrics(page, live_app, bounds_song):
    """Before the import that tab renders a score identical to the one beside it.

    Offering it anyway looks like the lyrics failed to appear, which is exactly how
    it was read in use.
    """
    slug, song_dir, _ = bounds_song
    page.goto(f"{live_app}/#/song/{slug}")
    expect(page.get_by_role("button", name="Cleaned MSCX", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Cleaned MSCX with lyrics")).to_have_count(0)

    # Import lyrics through the app, then it appears.
    page.locator(".step", has_text="Lyrics").first.click()
    page.get_by_role("button", name="Type by system").click()
    expect(page.locator(".sysblock").first).to_be_visible(timeout=30_000)
    page.locator(".sysblock").first.locator("textarea").first.fill("Vir-ta ven-het-tä")
    page.get_by_role("button", name="Import lyrics").click()
    expect(page.get_by_role("button", name="Cleaned MSCX with lyrics")).to_be_visible(
        timeout=60_000)


def test_the_panel_can_be_hidden_to_read_the_scores(page, live_app, bounds_song):
    """Reviewing is reading two scores side by side; the rail and panel are in the
    way. Hiding them must actually give the viewer the width, which is where the
    first attempt went wrong: `display:none` drops them as grid items, so the
    viewer slid into a zero-width column."""
    slug, _, _ = bounds_song
    page.goto(f"{live_app}/#/song/{slug}")
    viewer = page.locator(".vbody").first
    expect(page.locator(".panel")).to_be_visible()
    before = viewer.bounding_box()["width"]

    page.get_by_role("button", name="Hide panel").click()
    expect(page.locator(".panel")).not_to_be_visible()
    after = viewer.bounding_box()["width"]
    assert after > before + 300, f"viewer did not gain the space ({before} -> {after})"

    page.get_by_role("button", name="Show panel").click()
    expect(page.locator(".panel")).to_be_visible()

def test_compare_says_so_when_it_cannot_pair(page, live_app, bounds_song):
    """Pairing needs the cleaned score rendered, which needs MuseScore — absent
    here on purpose. It must say the systems do not correspond rather than sit
    empty, which is the same message a real mismatch produces."""
    slug, _, _ = bounds_song
    page.goto(f"{live_app}/#/song/{slug}")
    page.get_by_role("button", name="Compare").first.click()
    expect(page.locator(".compare .warn")).to_contain_text("do not correspond", timeout=60_000)
    assert page.locator(".cmprow").count() == 0


def test_a_long_panel_scrolls_itself_and_leaves_the_viewer_in_place(page, live_app, bounds_song):
    """Typing lyrics for 15 systems makes the panel far taller than the window.

    It has to scroll inside itself and the viewer has to stay put. The layout used
    to size the workspace as `100vh - 49px`, a guess at the header's height: one
    pixel taller -- a longer song name, a different font -- and the workspace
    overflowed the window, the page scrolled, and the viewer went with it. The
    header is made taller here because that is the condition that triggers it.
    """
    slug, _, _ = bounds_song
    page.goto(f"{live_app}/#/song/{slug}")
    page.locator(".step", has_text="Lyrics").first.click()
    page.get_by_role("button", name="Type by system").click()
    expect(page.locator(".sysblock").first).to_be_visible(timeout=30_000)
    assert page.locator(".sysblock").count() > 10          # premise: a long panel

    page.evaluate("document.querySelector('header').style.padding = '40px 18px'")
    page.wait_for_timeout(200)

    m = page.evaluate("""() => {
        const p = document.querySelector('.panel');
        const v = document.querySelector('.vbody').getBoundingClientRect();
        return { page_scrolls: document.documentElement.scrollHeight > window.innerHeight + 2,
                 panel_scrolls: p.scrollHeight > p.clientHeight + 2,
                 viewer_bottom: v.bottom, win: window.innerHeight };
    }""")
    assert not m["page_scrolls"], "the window scrolls instead of the panel"
    assert m["panel_scrolls"], "the panel is not the thing that scrolls"
    assert m["viewer_bottom"] <= m["win"] + 2, (
        f"the viewer runs past the window ({m['viewer_bottom']} > {m['win']})")

    # Scrolling to the last system must not move the viewer.
    before = page.locator(".vbody").first.bounding_box()
    page.locator(".sysblock").last.locator("textarea").first.scroll_into_view_if_needed()
    after = page.locator(".vbody").first.bounding_box()
    assert abs(after["y"] - before["y"]) < 2, "the viewer moved when the panel scrolled"
