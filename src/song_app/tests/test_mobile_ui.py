"""
The phone layout, in a real browser at a phone's size.

The three-pane workspace (stage rail · panel · viewer) does not fit a 390px screen, so
below the breakpoint one pane is shown at a time and a bar at the bottom switches
between them. What is pinned here is that switch, that the page itself never scrolls
(the panes scroll inside themselves — a page that scrolls carries the bar off-screen),
and that the desktop layout still shows all three panes at a desktop size.

Same two-step install as `test_ui_flow.py`; the module skips without it.
"""

import os
import socket
import threading
import time

import pytest

_NEEDS = "pip install pytest-playwright && playwright install chromium"
pytest.importorskip("playwright.sync_api", reason=_NEEDS)
pytest.importorskip("pytest_playwright", reason=_NEEDS)


def _browser_installed() -> bool:
    """See test_ui_flow: launching is the only honest check, and it has to run at
    import rather than in a fixture."""
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

from src.clean_score.utils.per_system import use_answer_file

pytestmark = pytest.mark.browser

PHONE = {"width": 390, "height": 844}      # iPhone 12/13/14 portrait
DESKTOP = {"width": 1280, "height": 900}

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
    with use_answer_file(str(tmp_path / "answers.json")):
        yield


@pytest.fixture(scope="module")
def live_app(tmp_path_factory):
    """The real server on its own port, with its own songs folder."""
    from src.song_app import server, state

    tmp = tmp_path_factory.mktemp("songapp-mobile")
    songs = tmp / "songs"
    songs.mkdir()
    previous_dir, state.SONGS_DIR = state.SONGS_DIR, str(songs)
    # No score previews: the renderer is not under test here either.
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
        srv.should_exit = True
        thread.join(timeout=10)
        state.SONGS_DIR = previous_dir
        if previous_cli is None:
            os.environ.pop("MUSESCORE_CLI_PATH", None)
        else:
            os.environ["MUSESCORE_CLI_PATH"] = previous_cli


def _new_song(page, base, name):
    page.goto(base)
    page.get_by_role("button", name="+ New song").click()
    page.get_by_placeholder("Song name").fill(name)
    page.locator("input[type=file]").first.set_input_files(FIXTURE)
    page.locator("select").select_option("men")
    page.locator("input[type=checkbox]").check()      # per-system, like the fixture
    page.get_by_role("button", name="Create").click()
    expect(page.locator(".ws")).to_be_visible()


def _page_scrolls(page):
    """Does the document itself scroll? It must not: the panes scroll inside
    themselves, and a scrolling page takes the switcher bar out of reach."""
    return page.evaluate(
        "() => document.documentElement.scrollWidth > window.innerWidth + 1"
        "   || document.documentElement.scrollHeight > window.innerHeight + 1"
    )


def _scrolls_sideways(page, selector):
    """Does this pane scroll sideways inside itself?

    The document staying put is not enough to prove nothing is too wide: `.panel`
    has `overflow-y: auto`, which makes the browser compute `overflow-x: auto` as
    well, so an over-wide table quietly becomes a scrollbar inside the panel and the
    page never notices. Ask the element itself.
    """
    return page.locator(selector).first.evaluate(
        "e => e.scrollWidth > e.clientWidth + 1"
    )


def test_phone_shows_one_pane_at_a_time_and_the_bar_switches_them(live_app, own_answers, page):
    page.set_viewport_size(PHONE)
    _new_song(page, live_app, "Phone song")

    bar = page.locator(".mobilebar")
    expect(bar).to_be_visible()

    # It opens on the stage panel, with the rail and the viewer out of the way.
    expect(page.locator(".panel")).to_be_visible()
    expect(page.locator(".stagebar")).to_be_hidden()
    expect(page.locator(".viewer")).to_be_hidden()
    # The middle button names the stage it shows, so the bar says where you are.
    expect(bar.get_by_role("button", name="Clean")).to_be_visible()

    bar.get_by_role("button", name="Score").click()
    expect(page.locator(".viewer")).to_be_visible()
    expect(page.locator(".panel")).to_be_hidden()

    bar.get_by_role("button", name="Stages").click()
    expect(page.locator(".stagebar")).to_be_visible()
    expect(page.locator(".viewer")).to_be_hidden()

    # Picking a stage means "take me to it" — the panel comes forward by itself.
    page.locator(".stagebar .step", has_text="Lyrics").click()
    expect(page.locator(".panel")).to_be_visible()
    expect(page.locator(".stagebar")).to_be_hidden()
    expect(bar.get_by_role("button", name="Lyrics")).to_be_visible()

    assert not _page_scrolls(page)


def test_phone_library_and_panels_do_not_overflow_the_screen(live_app, own_answers, page):
    """Nothing sticks out sideways: a horizontal scrollbar on a phone means a control
    is off the edge where it cannot be reached."""
    page.set_viewport_size(PHONE)
    _new_song(page, live_app, "Overflow check")

    for pane in ["Stages", "Score"]:
        page.locator(".mobilebar").get_by_role("button", name=pane).click()
        assert not _page_scrolls(page), f"the {pane} pane overflows the screen"

    # The clean panel's per-system grid is a four-column table, stacked into a card
    # per staff on a phone. It did not overflow before that (the table squeezed its
    # columns instead) — the stacking is for reading it; this only pins that nothing
    # regressed into being too wide.
    page.locator(".mobilebar").get_by_role("button", name="Stages").click()
    page.locator(".stagebar .step", has_text="Clean").click()
    expect(page.locator(".sysblock").first).to_be_visible()
    assert not _page_scrolls(page), "the per-system grid overflows the screen"
    assert not _scrolls_sideways(page, ".panel"), "the per-system grid is wider than the panel"

    page.goto(live_app)                                # back to the library
    expect(page.locator(".card").first).to_be_visible()
    assert not _page_scrolls(page), "the library overflows the screen"
    assert not _scrolls_sideways(page, ".lib"), "the library is wider than the screen"


def test_desktop_layout_is_unchanged(live_app, own_answers, page):
    """The phone rules are additive: at a desktop size all three panes share the
    screen and the switcher is not there at all."""
    page.set_viewport_size(DESKTOP)
    _new_song(page, live_app, "Desktop song")

    expect(page.locator(".stagebar")).to_be_visible()
    expect(page.locator(".panel")).to_be_visible()
    expect(page.locator(".viewer")).to_be_visible()
    expect(page.locator(".mobilebar")).to_be_hidden()
    assert not _page_scrolls(page)
