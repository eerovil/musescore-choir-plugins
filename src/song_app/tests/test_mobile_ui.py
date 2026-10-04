"""
The phone layout, in a real browser at a phone's size.

The three-pane workspace (stage rail · panel · viewer) does not fit a 390px screen, so
below the breakpoint the panel and the viewer are shown one at a time, a bar at the
bottom switches between them, and the stage rail is a drawer the header's ☰ slides in
(#258). What is pinned here is that switch, the drawer, that the bar stays on the
screen whatever happens to the page (the panes scroll inside themselves — a page that
scrolls or zooms carries the bar off-screen), that the score zooms itself because the
page cannot, and that the desktop layout still shows all three panes at a desktop size.

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
    page.locator("#f-xml").set_input_files(FIXTURE)
    page.locator("select").select_option("men")
    page.get_by_role("button", name="Create").click()
    expect(page.locator(".ws")).to_be_visible()
    _use_per_system(page, base)                       # per-system, like the fixture
    expect(page.locator(".ws")).to_be_visible()


def _use_per_system(page, base):
    """Switch the song just created to per-system mode, as the Clean panel's
    toggle does: the New song form no longer asks."""
    slug = page.url.split("#/song/", 1)[1]
    response = page.request.post(f"{base}/api/songs/{slug}/mode", data={"mode": "per-system"})
    assert response.ok, response.text()
    page.reload()


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


def test_new_issue_link_is_global_and_mobile_safe(live_app, own_answers, page):
    page.set_viewport_size(DESKTOP)
    page.goto(live_app)

    link = page.get_by_role(
        "link", name="Create musescore-choir-plugins GitHub issue",
    )
    expect(link).to_be_visible()
    assert link.get_attribute("href") == (
        "https://github.com/eerovil/musescore-choir-plugins/issues/new"
        "?template=issue-for-agent-to-fix.md"
    )
    assert link.get_attribute("target") == "_blank"
    assert link.get_attribute("rel") == "noopener"

    _new_song(page, live_app, "Issue shortcut")
    expect(link).to_be_visible()
    if evidence := os.getenv("ISSUE_23_EVIDENCE_DIR"):
        os.makedirs(evidence, exist_ok=True)
        page.screenshot(path=os.path.join(evidence, "issue-23-new-issue-button.png"))

    page.set_viewport_size(PHONE)
    expect(link).to_be_visible()
    expect(link.locator("span")).to_be_hidden()
    if evidence := os.getenv("ISSUE_23_EVIDENCE_DIR"):
        page.screenshot(path=os.path.join(evidence, "issue-23-new-issue-phone.png"))
    assert not _page_scrolls(page)


def _bar_on_screen(page):
    """The bar's whole box is inside the window, and nothing else is on top of it."""
    return page.evaluate("""() => {
        const bar = document.querySelector('.mobilebar');
        const r = bar.getBoundingClientRect();
        const inside = r.top >= 0 && r.bottom <= window.innerHeight + 0.5 && r.height > 40;
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return inside && bar.contains(hit);
    }""")


def test_phone_shows_one_pane_at_a_time_and_the_bar_switches_them(live_app, own_answers, page):
    page.set_viewport_size(PHONE)
    _new_song(page, live_app, "Phone song")

    bar = page.locator(".mobilebar")
    expect(bar).to_be_visible()
    # Two tabs: the stage list is no longer one of them, it is the ☰ drawer.
    expect(bar.locator(".mtab")).to_have_count(2)

    # It opens on the stage panel, with the rail and the viewer out of the way.
    expect(page.locator(".panel")).to_be_visible()
    expect(page.locator(".stagebar")).to_be_hidden()
    expect(page.locator(".viewer")).to_be_hidden()
    # The first button names the stage it shows, so the bar says where you are.
    expect(bar.get_by_role("button", name="Clean")).to_be_visible()

    bar.get_by_role("button", name="Score").click()
    expect(page.locator(".viewer")).to_be_visible()
    expect(page.locator(".panel")).to_be_hidden()
    assert _bar_on_screen(page)

    assert not _page_scrolls(page)


def test_phone_stages_are_a_drawer_the_header_opens(live_app, own_answers, page):
    """The stage list is the same left-hand sidebar as on desktop, slid in by ☰."""
    page.set_viewport_size(PHONE)
    _new_song(page, live_app, "Drawer song")
    menu = page.locator("#stagemenu")
    rail = page.locator(".stagebar")

    expect(menu).to_be_visible()
    expect(menu).to_have_attribute("aria-expanded", "false")
    expect(rail).to_be_hidden()

    menu.click()
    expect(rail).to_be_visible()
    expect(menu).to_have_attribute("aria-expanded", "true")
    page.wait_for_function(
        "() => document.querySelector('.stagebar').getBoundingClientRect().left === 0")
    box = rail.bounding_box()
    assert box["x"] == 0 and box["width"] <= 0.8 * PHONE["width"] + 1, box

    # Picking a stage means "take me to it" — the drawer closes and its panel shows.
    page.locator(".stagebar .step", has_text="Lyrics").click()
    expect(rail).to_be_hidden()
    expect(page.locator(".panel")).to_be_visible()
    expect(page.locator(".mobilebar").get_by_role("button", name="Lyrics")).to_be_visible()
    expect(menu).to_have_attribute("aria-expanded", "false")

    # Tapping outside it, Escape, and the phone's Back all close it and change nothing.
    menu.click()
    expect(rail).to_be_visible()
    page.mouse.click(PHONE["width"] - 20, PHONE["height"] // 2)
    expect(rail).to_be_hidden()
    menu.click()
    page.keyboard.press("Escape")
    expect(rail).to_be_hidden()
    menu.click()
    page.go_back()
    expect(rail).to_be_hidden()
    assert "#/song/" in page.url, "Back with the drawer open left the song"
    expect(page.locator(".mobilebar").get_by_role("button", name="Lyrics")).to_be_visible()

    # Held sideways the list is taller than the screen, so it scrolls to the last stage.
    page.set_viewport_size({"width": 844, "height": 390})
    menu.click()
    last = page.locator(".stagebar .step").last
    last.scroll_into_view_if_needed()
    expect(last).to_be_in_viewport()

    # The Library has no stages, so no ☰.
    page.goto(live_app)
    expect(page.locator(".card").first).to_be_visible()
    expect(menu).to_be_hidden()


def test_phone_bar_stays_on_screen(live_app, own_answers, page):
    """Whatever happens to the page, the bar is still there at the bottom (#258).

    The page is never meant to scroll, but a browser scrolling a focused field into
    view can still move it, and the keyboard shrinks the window."""
    page.set_viewport_size(PHONE)
    _new_song(page, live_app, "Bar song")
    assert _bar_on_screen(page)

    # Something scrolls the page itself (a field brought into view, say): put back.
    page.evaluate("() => { document.scrollingElement.scrollTop = 300; window.scrollTo(0, 300); }")
    page.wait_for_function("() => document.scrollingElement.scrollTop === 0")
    assert _bar_on_screen(page)

    # The keyboard opening: the window shrinks, the bar stays in it.
    page.set_viewport_size({"width": PHONE["width"], "height": 420})
    assert _bar_on_screen(page)
    page.set_viewport_size(PHONE)

    # A pane holding something far taller than the screen does not push it out.
    page.locator(".mobilebar").get_by_role("button", name="Score").click()
    page.locator(".pdfview").first.evaluate(
        "e => { const d = document.createElement('div'); d.style.height = '5000px'; e.append(d); }")
    assert _bar_on_screen(page)
    assert not _page_scrolls(page)

    # The page cannot be pinch-zoomed on a phone (that is what carries the bar away);
    # on a desktop it is left alone.
    assert page.evaluate("() => getComputedStyle(document.documentElement).touchAction") == "pan-x pan-y"
    page.set_viewport_size(DESKTOP)
    assert page.evaluate("() => getComputedStyle(document.documentElement).touchAction") == "auto"


def test_phone_score_zooms_itself(live_app, own_answers, page):
    """With page zoom off, the score viewer zooms: − / + / Fit, kept across panes."""
    page.set_viewport_size(PHONE)
    page.route("**/pdf.min.js", lambda route: route.fulfill(
        body=_FAKE_PDFJS, content_type="application/javascript"))
    _new_song(page, live_app, "Zoom song")
    page.locator(".mobilebar").get_by_role("button", name="Score").click()

    view = page.locator(".pdfview").first
    page.wait_for_function("e => Boolean(e._renderedUrl) && e.querySelector('canvas')",
                           arg=view.element_handle())
    zoom = page.locator(".vzoom").first
    expect(zoom).to_be_visible()
    fit_width = view.evaluate("e => e.querySelector('canvas').getBoundingClientRect().width")
    assert view.evaluate("e => e.scrollWidth <= e.clientWidth + 1")

    zoom.get_by_role("button", name="Zoom in").click()
    page.wait_for_function("e => e.scrollWidth > e.clientWidth + 50", arg=view.element_handle())
    wider = view.evaluate("e => e.querySelector('canvas').getBoundingClientRect().width")
    assert wider > fit_width * 1.4, (fit_width, wider)

    # Switching to the panel and back keeps it.
    page.locator(".mobilebar .mtab").first.click()
    page.locator(".mobilebar").get_by_role("button", name="Score").click()
    assert view.evaluate("e => e._zoom") > 1

    zoom.get_by_role("button", name="Fit", exact=True).click()
    page.wait_for_function("e => e.scrollWidth <= e.clientWidth + 1", arg=view.element_handle())

    # Two fingers spreading apart zoom the score, not the page.
    view.evaluate(_PINCH)
    page.wait_for_function("e => (e._zoom || 1) > 1.5", arg=view.element_handle())
    assert page.evaluate("() => window.visualViewport.scale") == 1
    assert _bar_on_screen(page)

    # At a desktop size there is nothing to zoom with: the page zooms there.
    page.set_viewport_size(DESKTOP)
    expect(zoom).to_be_hidden()
    expect(page.locator("#stagemenu")).to_be_hidden()


# pdf.js stood in for: the CDN is not part of this test, and the zoom only needs a
# document whose pages draw at the size they are asked for.
_FAKE_PDFJS = """
window.pdfjsLib = {
  GlobalWorkerOptions: {},
  getDocument: () => ({ promise: Promise.resolve({
    numPages: 2,
    getPage: async () => ({
      getViewport: ({ scale }) => ({ width: 595 * scale, height: 842 * scale }),
      render: () => ({ promise: Promise.resolve() }),
    }),
  }) }),
};
"""

# A pinch, as the touch events a phone sends for one: two fingers down, apart, up.
_PINCH = """e => {
  const r = e.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  const touches = (d) => [
    new Touch({ identifier: 1, target: e, clientX: cx - d, clientY: cy }),
    new Touch({ identifier: 2, target: e, clientX: cx + d, clientY: cy }),
  ];
  const fire = (type, list) => e.dispatchEvent(new TouchEvent(type, {
    touches: list, targetTouches: list, changedTouches: list, bubbles: true, cancelable: true }));
  fire('touchstart', touches(40));
  fire('touchmove', touches(80));
  fire('touchmove', touches(100));
  fire('touchend', []);
}"""


def test_phone_library_and_panels_do_not_overflow_the_screen(live_app, own_answers, page):
    """Nothing sticks out sideways: a horizontal scrollbar on a phone means a control
    is off the edge where it cannot be reached."""
    page.set_viewport_size(PHONE)
    _new_song(page, live_app, "Overflow check")

    page.locator("#stagemenu").click()
    assert not _page_scrolls(page), "the stage drawer overflows the screen"
    page.keyboard.press("Escape")
    page.locator(".mobilebar").get_by_role("button", name="Score").click()
    assert not _page_scrolls(page), "the Score pane overflows the screen"

    # The clean panel's per-system grid is a four-column table, stacked into a card
    # per staff on a phone. It did not overflow before that (the table squeezed its
    # columns instead) — the stacking is for reading it; this only pins that nothing
    # regressed into being too wide.
    page.locator("#stagemenu").click()
    page.locator(".stagebar .step", has_text="Clean").click()
    expect(page.locator(".sysblock").first).to_be_visible()
    assert not _page_scrolls(page), "the per-system grid overflows the screen"
    assert not _scrolls_sideways(page, ".panel"), "the per-system grid is wider than the panel"

    page.goto(live_app)                                # back to the library
    expect(page.locator(".card").first).to_be_visible()
    assert not _page_scrolls(page), "the library overflows the screen"
    assert not _scrolls_sideways(page, ".lib"), "the library is wider than the screen"


def test_phone_new_song_form_leads_with_the_pdf_and_fits(live_app, own_answers, page):
    """The first click of the whole scan route happens on this screen (#127).

    The PDF is asked for first because a PDF alone is now the ordinary way in, and
    the form has to say where each door leads before Create is pressed.
    """
    page.set_viewport_size(PHONE)
    page.goto(live_app)
    page.get_by_role("button", name="+ New song").click()

    pdf_box = page.locator("#f-pdf").bounding_box()
    xml_box = page.locator("#f-xml").bounding_box()
    assert pdf_box["y"] < xml_box["y"], "the score file is asked for before the PDF"
    expect(page.locator(".routehint")).to_contain_text("A PDF alone starts at Scan")

    assert not _page_scrolls(page), "the New song form overflows the screen"
    assert not _scrolls_sideways(page, ".lib"), "the New song form is wider than the screen"


def test_phone_pdf_viewer_scrolls_inside_its_pane(live_app, own_answers, page):
    """A tall PDF must shrink to the viewer and scroll there, above the switcher.

    Use a stand-in for the PDF.js canvases so this layout test does not depend on
    the CDN or MuseScore renderer. The real renderer appends the same kind of tall
    block content to ``.pdfview``.
    """
    page.set_viewport_size(PHONE)
    _new_song(page, live_app, "PDF scroll check")
    page.locator(".mobilebar").get_by_role("button", name="Score").click()

    view = page.locator(".pdfview").first
    expect(view).to_be_visible()
    page.wait_for_function("e => Boolean(e._renderedUrl)", arg=view.element_handle())
    view.evaluate("""e => {
        const pages = document.createElement('div');
        pages.style.height = '1600px';
        pages.dataset.testid = 'tall-pdf';
        e.replaceChildren(pages);
    }""")

    sizes = view.evaluate("e => ({client: e.clientHeight, scroll: e.scrollHeight})")
    assert sizes["scroll"] > sizes["client"] + 500, \
        f"the PDF grew its pane instead of overflowing it: {sizes}"

    view.evaluate("e => { e.scrollTop = e.scrollHeight; }")
    assert view.evaluate("e => e.scrollTop") > 500, "the PDF pane did not scroll"
    assert not _page_scrolls(page), "the document scrolled instead of the PDF pane"


def test_the_library_scrolls_to_its_last_song(live_app, own_answers, page):
    """Sideways is not the only way to put a song out of reach. The page itself never
    scrolls, so the library has to scroll inside itself; when it did not, every card
    below the window's edge was simply unreachable, which is what 43 songs looked
    like."""
    page.set_viewport_size({"width": 390, "height": 320})
    _new_song(page, live_app, "Scroll check")

    page.goto(live_app)
    expect(page.locator(".card").first).to_be_visible()
    lib = page.locator(".lib")
    assert lib.evaluate("e => e.scrollHeight > e.clientHeight + 1"), \
        "the library is not taller than the window — this test proves nothing"

    last = page.locator(".card").last
    last.scroll_into_view_if_needed()
    assert lib.evaluate("e => e.scrollTop") > 0, "the library did not scroll"
    expect(last).to_be_in_viewport()
    assert not _page_scrolls(page)


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


def test_the_switcher_bar_survives_a_plain_browser_tab(live_app, own_answers, page):
    """`viewport-fit=cover` belongs to the installed app, not to a browser tab.

    In a tab it moves the page's bottom edge under the browser's own toolbar, and the
    switcher bar sits exactly there, so it goes under the toolbar with it — the app
    looks like it has lost its bottom nav (#54). The shipped meta therefore leaves it
    off and a head script adds it back only when the app is actually standalone, where
    the safe-area rules pad it and there is no toolbar to hide behind.
    """
    page.set_viewport_size(PHONE)
    _new_song(page, live_app, "Viewport song")

    tab = page.locator("meta#viewport").get_attribute("content")
    assert "viewport-fit=cover" not in tab, tab
    expect(page.locator(".mobilebar")).to_be_visible()
    assert not _page_scrolls(page)
    if evidence := os.getenv("ISSUE_54_EVIDENCE_DIR"):
        os.makedirs(evidence, exist_ok=True)
        page.screenshot(path=os.path.join(evidence, "issue-54-browser-tab.png"))

    # Now the same page as an installed iOS app. `navigator.standalone` is the flag
    # iOS itself sets; the script reads it before the first paint.
    page.add_init_script("Object.defineProperty(navigator, 'standalone', { value: true });")
    page.reload()
    expect(page.locator(".ws")).to_be_visible()

    installed = page.locator("meta#viewport").get_attribute("content")
    assert "viewport-fit=cover" in installed, installed
    expect(page.locator(".mobilebar")).to_be_visible()
    if evidence := os.getenv("ISSUE_54_EVIDENCE_DIR"):
        page.screenshot(path=os.path.join(evidence, "issue-54-installed-app.png"))
