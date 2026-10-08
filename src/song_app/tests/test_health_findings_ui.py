"""Health findings in a real browser, on a score with findings on most of its bars.

#170 put a "this parse looks unusable" banner on Review and Fix; #356 removed it,
because on scanned songs homr's `⚠` questions set it off on scores whose notes were
right. What is pinned here is that neither screen shows it any more, that the rows
and the count are still there, that the approve button works, and that the Scan
panel still names the systems the findings fell in.
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

import uvicorn

from src.song_app import pdf_systems, scan, server, state

pytestmark = pytest.mark.browser



def _shot(page, name):
    """Screenshots go to `EVIDENCE_DIR` when the run names one, never into the repo."""
    where = os.environ.get("EVIDENCE_DIR")
    if where:
        os.makedirs(where, exist_ok=True)
        page.screenshot(path=os.path.join(where, name), full_page=True)

BARS = 52
# The walk's own shape: 60 findings over 28 of 52 bars, concentrated in the lower
# voices. Written out rather than parsed from a score, because what is on trial here
# is the panel and not the check.
ROUGH = [
    {"id": f"malformed-m{m}-s{s}-v0", "kind": "malformed-measure", "measure": m,
     "staff": staff, "status": "open", "detail": "voice 1 fills 7/8 of 1"}
    for m in range(1, 29)
    for s, staff in ((2, "T2"),)
] + [
    {"id": f"unprinted-meter-m{m}-s4", "kind": "unprinted-meter", "measure": m,
     "staff": "B2", "status": "open", "detail": "bar is 1/2 but the engraving says 3/4"}
    for m in range(1, 33, 2)
]

CALM = [{"id": "malformed-m11-s1-v0", "kind": "malformed-measure", "measure": 11,
         "staff": "T1", "status": "open", "detail": "voice 1 fills 7/8 of 1"}]


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _score(bars: int) -> str:
    return ("<museScore><Score>"
            "<Part><trackName>T1</trackName><Staff id=\"1\"/></Part>"
            "<Staff id=\"1\">" + "<Measure></Measure>" * bars
            + "</Staff></Score></museScore>")


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("findings")
    songs = tmp / "songs"
    songs.mkdir()
    previous_dir, state.SONGS_DIR = state.SONGS_DIR, str(songs)
    previous_cli = os.environ.get("MUSESCORE_CLI_PATH")
    # No previews: the renderer is not under test and a real MuseScore run would make
    # this slow and host-dependent.
    os.environ["MUSESCORE_CLI_PATH"] = str(tmp / "no-musescore-here")

    song = state.create("Rough Reading", per_system=True)
    with open(song.path("scan.pdf"), "wb") as fh:
        fh.write(b"%PDF-1.4 not really a pdf\n")
    song.data["sources"]["pdf"] = "scan.pdf"
    with open(song.path("rough_cleaned.mscx"), "w") as fh:
        fh.write(_score(BARS))
    song.data["cleaned"] = "rough_cleaned.mscx"
    song.data["cleaned_fingerprint"] = state.file_fingerprint(song.cleaned_path())
    song.save()

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
        yield f"http://127.0.0.1:{port}", song
    finally:
        srv.should_exit = True
        thread.join(timeout=10)
        state.SONGS_DIR = previous_dir
        if previous_cli is None:
            os.environ.pop("MUSESCORE_CLI_PATH", None)
        else:
            os.environ["MUSESCORE_CLI_PATH"] = previous_cli


def _health(song, issues, stage):
    song.data["health"] = {"checked_against": song.data["cleaned_fingerprint"],
                           "issues": issues}
    song.data["stage"] = stage
    song.save()


def _open(page, base, song, stage_label):
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/#/song/{song.slug}")
    page.wait_for_selector(".stagebar")
    page.locator(".stagebar .step", has_text=stage_label).first.click()
    return errors


def test_the_review_stage_shows_the_count_and_no_verdict(live, page):
    base, song = live
    _health(song, ROUGH, "review")
    errors = _open(page, base, song, "Review")
    page.wait_for_selector(".verify")

    assert page.locator(".verify .check", has_text="Health").first.is_visible()
    assert "44 open issue(s)" in page.locator(".verify .check", has_text="Health").first.inner_text()
    assert page.locator(".verdict").count() == 0
    assert page.get_by_text("not worth repairing").count() == 0
    assert page.locator(".review-state").first.inner_text() != "Read this against the page"
    _shot(page, "issue-356-review.png")
    assert not errors, f"the panel raised: {errors}"


def test_findings_do_not_gate(live, page):
    base, song = live
    _health(song, ROUGH, "review")
    _open(page, base, song, "Review")
    page.wait_for_selector(".verify")

    approve = page.locator(".review-approve")
    assert approve.is_visible() and approve.is_enabled()
    approve.click()
    page.wait_for_selector(".panel h2:text('Record')")
    fresh = state.load(song.slug)
    assert fresh.stage == "record"
    assert fresh.data.get("review", {}).get("approved_against")


def test_a_handful_of_findings_is_left_alone(live, page):
    base, song = live
    _health(song, CALM, "review")
    errors = _open(page, base, song, "Review")
    page.wait_for_selector(".verify")

    assert page.locator(".verify .check", has_text="Health").first.is_visible()
    assert page.locator(".verdict").count() == 0
    assert not errors, f"the panel raised: {errors}"


def test_the_fix_panel_lists_the_rows_with_no_banner(live, page):
    base, song = live
    _health(song, ROUGH, "fix")
    errors = _open(page, base, song, "Fix")
    page.wait_for_selector("text=Open in MuseScore")
    page.wait_for_selector(".problems[data-loaded]", state="attached")

    assert page.locator(".issue").count() > 20
    assert page.locator(".verdict").count() == 0
    assert page.get_by_text("not worth repairing").count() == 0
    _shot(page, "issue-356-fix.png")
    assert not errors, f"the panel raised: {errors}"


def test_the_scan_panel_says_which_systems_to_read_again(live, page):
    """Where the findings fell, on the one screen where acting on them costs a
    single button."""
    base, song = live
    _health(song, ROUGH, "scan")
    pdf_systems.save_bounds(song.dir, [
        pdf_systems.SystemBounds(index=i, page=1, top=0.1 * i, bottom=0.1 * i + 0.08)
        for i in range(1, 5)
    ])
    os.makedirs(os.path.join(song.dir, "scan"), exist_ok=True)
    fragments = {}
    # Real band stamps, or the app discards every fragment on the next read — which
    # is the invalidation rule working rather than a test detail.
    source = pdf_systems.file_version(song.source_path("pdf"))
    for band in pdf_systems.load_bounds(song.dir):
        i = band.index
        name = os.path.join("scan", f"system-{i:02d}.musicxml")
        with open(song.path(name), "w") as fh:
            fh.write("<score-partwise/>")
        fragments[str(i)] = {"index": i, "musicxml": name, "bars": 13,
                             "content": f"c{i}", "staves": 1,
                             "band": scan.band_stamp(band, source), "page": 1}
    song.data["scan"] = {"systems": fragments, "assembled": scan.ASSEMBLED_NAME}
    song.save()
    song = state.load(song.slug)
    song.data["scan"]["assembled_revision"] = scan.revision(song)
    song.save()

    errors = _open(page, base, song, "Scan")
    page.wait_for_selector(".scandone")

    hint = page.locator(".scanfindings").first
    hint.scroll_into_view_if_needed()
    assert hint.is_visible()
    # 44 findings over 52 bars in four 13-bar systems: systems 1 and 2 carry them.
    assert "44 health finding(s)" in hint.inner_text()
    assert "system(s) 1 (20), 2 (19), 3 (5)" in hint.inner_text()
    _shot(page, "issue-170-scan-findings.png")
    assert not errors, f"the panel raised: {errors}"
