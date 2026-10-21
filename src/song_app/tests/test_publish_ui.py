"""The Publish panel in a real browser (#382, #400).

The server runs in-process, so Cloudflare is the same `FakeCloudflare` the unit
tests use (R2 a dict, D1 SQLite) and the MuseScore half of the publish is a
stub. What is pinned is the person's path: the site's choirs are offered (read
from its database, #408), one is chosen, Publish runs, and the panel then says where the song went —
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
                        lambda cleaned, out, initial_bpm=None, tempo_changes=None, part_names=None, staff_groups=None, log=None: _bundle(
                            tmp_path, parts=("T1", "T2", "B1", "B2")))

    song = state.create("Hanget soi", per_system=False, voicing="men")
    cleaned = song.path("hanget_cleaned.mscx")
    with open(FERMATA, "rb") as src, open(cleaned, "wb") as dst:
        dst.write(src.read())
    song.data.update(cleaned=os.path.basename(cleaned), stage="publish",
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
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(f"{base}/#/song/{slug}")

    section = page.locator(".publish-section")
    expect(section.get_by_role("heading", name="Stemmanauhat site")).to_be_visible()
    choir = section.locator("select.publish-choir")
    # The site's own choirs, read from its database (#408); nothing is guessed.
    expect(choir.locator("option")).to_have_text(
        ["— choir —", "Joensuun Mieslaulajat (jm)", "Naiskuoro (naiskuoro)",
         "Esittely (public, open to all)"])
    expect(choir).to_have_value("")
    choir.select_option("jm")
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
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(f"{base}/#/song/{slug}")

    section = page.locator(".publish-section")
    status = section.locator(".publish-status")
    section.locator("select.publish-choir").select_option("jm")
    section.locator("button.publish-btn").click()
    expect(status).to_contain_text("Published to Joensuun Mieslaulajat (jm)", timeout=15000)

    section = page.locator(".publish-section")
    section.locator("select.publish-choir").select_option("public")
    section.locator("button.publish-btn").click()
    status = page.locator(".publish-section .publish-status")
    expect(status).to_contain_text("Published to Esittely (public)", timeout=15000)
    expect(status).to_contain_text("Published to Joensuun Mieslaulajat (jm)")
    assert [row[0] for row in cf.listed("jm")] == [slug]
    assert [row[0] for row in cf.listed("public")] == [slug]
    page.locator(".publish-section").scroll_into_view_if_needed()
    _shot(page, f"publish-public-and-jm-{size[0]}.png")
    assert errors == []


@pytest.mark.parametrize("size", [(1280, 900), (390, 844)], ids=["desktop", "phone"])
def test_the_credits_typed_on_the_panel_reach_the_site(live, page, size):
    base, slug, cf = live
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(f"{base}/#/song/{slug}")

    page.locator("[data-credit=composer]").fill("Toivo Kuula")
    page.locator("[data-credit=lyricist]").fill("Eino Leino")
    expect(page.locator("[data-save-note]")).to_have_text("Unsaved changes")
    page.locator("button.publish-save").click()
    expect(page.locator("[data-save-note]")).to_have_text("Saved")
    assert state.load(slug).data["publish"]["credits"] == {
        "composer": "Toivo Kuula", "lyricist": "Eino Leino", "arranger": ""}

    page.reload()
    expect(page.locator("[data-credit=composer]")).to_have_value("Toivo Kuula")
    page.locator(".publish-section select.publish-choir").select_option("jm")
    page.locator(".publish-section button.publish-btn").click()
    expect(page.locator(".publish-section .publish-status")).to_contain_text(
        "Published to Joensuun Mieslaulajat (jm)", timeout=15000)
    assert cf.credits("jm", slug) == ("Toivo Kuula", "Eino Leino", None)
    # The finished publish redraws the panel, which can swap the field out under
    # the scroll; wait for the redrawn one.
    expect(page.locator(".publish-section button.publish-btn")).to_have_text("Publish again")
    expect(page.locator("[data-credit=composer]")).to_have_value("Toivo Kuula")
    page.locator("[data-credit=composer]").scroll_into_view_if_needed()
    _shot(page, f"publish-credits-{size[0]}.png")
    assert errors == []


@pytest.mark.parametrize("size", [(1280, 900), (390, 844)], ids=["desktop", "phone"])
def test_the_part_names_typed_on_the_panel_are_published(live, page, size, monkeypatch,
                                                          tmp_path):
    """#402: cleaning guesses part names from pitch; the panel shows the score's
    names, refuses two the same, and publishes what was typed."""
    base, slug, _cf = live
    handed = []

    def fake_bundle(cleaned, out, initial_bpm=None, tempo_changes=None, part_names=None, staff_groups=None,
                    log=None):
        handed.append(part_names)
        return _bundle(tmp_path, parts=tuple(part_names))
    monkeypatch.setattr(publish, "build_bundle", fake_bundle)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(f"{base}/#/song/{slug}")

    names = page.locator("[data-part-names] input")
    expect(names).to_have_count(2)
    expect(names.nth(0)).to_have_value("S1")
    expect(names.nth(1)).to_have_value("A1")
    names.nth(1).fill("S1")
    page.locator("button.publish-save").click()
    expect(page.locator("[data-save-note]")).to_contain_text("Two parts are called S1")
    assert "part_names" not in state.load(slug).data.get("publish", {})

    names.nth(0).fill("Sopraano")
    names.nth(1).fill("Altto")
    page.locator("button.publish-save").click()
    expect(page.locator("[data-save-note]")).to_have_text("Saved")
    page.locator("[data-part-names]").scroll_into_view_if_needed()
    _shot(page, f"publish-part-names-{size[0]}.png")

    page.locator(".publish-section select.publish-choir").select_option("jm")
    page.locator(".publish-section button.publish-btn").click()
    expect(page.locator(".publish-section .publish-status")).to_contain_text(
        "2 part(s)", timeout=15000)
    assert handed == [["Sopraano", "Altto"]]
    expect(page.locator("[data-part-names] input").nth(0)).to_have_value("Sopraano")
    assert errors == []


def test_stale_part_names_stop_publish_until_saved(live, page, monkeypatch, tmp_path):
    """Review of #402: after a re-clean changed the number of parts, Publish is
    refused until the names on screen are saved."""
    base, slug, cf = live
    handed = []

    def fake_bundle(cleaned, out, initial_bpm=None, tempo_changes=None, part_names=None, staff_groups=None,
                    log=None):
        handed.append(part_names)
        return _bundle(tmp_path, parts=tuple(part_names))
    monkeypatch.setattr(publish, "build_bundle", fake_bundle)
    song = state.load(slug)
    song.data["publish"] = {"part_names": ["S1", "S2", "A1"]}
    song.save()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/#/song/{slug}")

    expect(page.locator("[data-part-names] input")).to_have_count(2)
    expect(page.locator(".part-names-note")).to_contain_text("The score now has 2 parts")
    page.locator(".publish-section select.publish-choir").select_option("jm")
    page.locator(".publish-section button.publish-btn").click()
    status = page.locator(".publish-section .publish-status")
    expect(status).to_contain_text("Save the part names first")
    page.locator("[data-part-names]").scroll_into_view_if_needed()
    _shot(page, "publish-part-names-stale.png")
    assert handed == [] and cf.listed("jm") == []

    page.locator("button.publish-save").click()
    expect(page.locator("[data-save-note]")).to_have_text("Saved")
    page.locator(".publish-section select.publish-choir").select_option("jm")
    page.locator(".publish-section button.publish-btn").click()
    expect(page.locator(".publish-section .publish-status")).to_contain_text(
        "Published to Joensuun Mieslaulajat (jm)", timeout=15000)
    assert handed == [["S1", "A1"]]
    assert errors == []


def test_a_song_published_before_one_record_per_choir_still_shows_as_published(live, page):
    """A song published before #384 kept one record under `site`. The library's badge
    and rail, and the song's own stage rail, still count it as published."""
    base, slug, _cf = live
    song = state.load(slug)
    song.data["publish"] = {"choir": "jm", "site": {
        "choir": "jm", "slug": slug, "prefix": f"songs/jm/{slug}/old/", "at": time.time(),
        "files": [], "parts": ["T1", "T2"], "duration": 4.5}}
    song.save()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto(f"{base}/#/")
    card = page.locator("a.card", has_text="Hanget soi")
    expect(card.locator(".badge")).to_have_text("✓ Published")
    expect(card.locator(".rail .dot").last).to_have_class("dot done")
    _shot(page, "publish-legacy-library.png")

    page.goto(f"{base}/#/song/{slug}")
    expect(page.locator(".stagebar .step", has_text="Publish")).to_have_class(
        "step active done")
    expect(page.locator(".publish-section .publish-status")).to_contain_text(
        "Published to Joensuun Mieslaulajat (jm)")
    assert errors == []


@pytest.mark.parametrize("size", [(1280, 900), (390, 844)], ids=["desktop", "phone"])
def test_another_sites_own_choirs_are_offered_and_published_to(live, page, size):
    """#408: someone else's copy of the site has its own choirs. The panel offers
    those, read from that site's database, and keeps offering the last list read
    while the site cannot be reached."""
    base, slug, cf = live
    cf.db.execute("DELETE FROM choirs")
    cf.db.executemany("INSERT INTO choirs (id, name, public) VALUES (?, ?, ?)", [
        ("aurora", "Kamarikuoro Aurora", 0), ("kirkko", "Kirkkokuoro", 0),
        ("public", "Demo", 1)])
    cf.db.commit()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(f"{base}/#/song/{slug}")

    section = page.locator(".publish-section")
    choir = section.locator("select.publish-choir")
    expect(choir.locator("option")).to_have_text(
        ["— choir —", "Kamarikuoro Aurora (aurora)", "Kirkkokuoro (kirkko)",
         "Demo (public, open to all)"])
    section.scroll_into_view_if_needed()
    # A native drop-down cannot be photographed open; show the list as a box.
    choir.evaluate("s => s.size = s.options.length")
    section.locator(".publish-status").scroll_into_view_if_needed()
    _shot(page, f"publish-site-choirs-{size[0]}.png")
    choir.evaluate("s => s.size = 0")

    choir.select_option("aurora")
    section.locator("button.publish-btn").click()
    status = page.locator(".publish-section .publish-status")
    expect(status).to_contain_text("Published to Kamarikuoro Aurora (aurora)", timeout=15000)
    assert [row[0] for row in cf.listed("aurora")] == [slug]

    # The site goes out of reach: the saved list is still offered, and says so.
    cf.fail_on = "/d1/"
    page.reload()
    section = page.locator(".publish-section")
    expect(section.locator(".publish-choirs")).to_contain_text("Could not read the site's choirs")
    expect(section.locator("select.publish-choir")).to_have_value("aurora")
    section.scroll_into_view_if_needed()
    _shot(page, f"publish-site-offline-{size[0]}.png")
    assert errors == []


@pytest.mark.parametrize("size", [(1280, 900), (390, 844)], ids=["desktop", "phone"])
def test_shared_staves_are_offered_above_five_parts_and_published_as_set(
        live, page, size, monkeypatch, tmp_path):
    """#420: a song of six parts shows the pairing the page prints, can be set by
    hand and back, and Publish is handed what the panel shows."""
    base, slug, _cf = live
    from src.song_app.tests.test_publish import SIX, _six_parts
    song = state.load(slug)
    with open(_six_parts(tmp_path), "rb") as src, open(song.cleaned_path(), "wb") as dst:
        dst.write(src.read())
    # Recorded as already checked, so the file watcher does not redraw the panel
    # (and drop what was typed in it) when it notices the new score.
    fingerprint = state.file_fingerprint(song.cleaned_path())
    song.data.update(review={"approved_against": fingerprint}, cleaned_fingerprint=fingerprint)
    song.save()
    handed = []

    def fake_bundle(cleaned, out, initial_bpm=None, tempo_changes=None, part_names=None,
                    staff_groups=None, log=None):
        handed.append(staff_groups)
        return _bundle(tmp_path, parts=tuple(SIX))
    monkeypatch.setattr(publish, "build_bundle", fake_bundle)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(f"{base}/#/song/{slug}")

    shared = page.locator("[data-staff-groups]")
    expect(shared).to_have_value("S1+S1b, S2+S2b, A1+A2")
    expect(page.locator(".staff-groups-note")).to_contain_text("guessed from the part names")
    expect(page.locator("[data-staff-groups-auto]")).to_be_hidden()
    shared.scroll_into_view_if_needed()
    _shot(page, f"publish-shared-staves-{size[0]}.png")

    shared.fill("S1+S1b, S2+T1")
    expect(page.locator(".staff-groups-note")).to_have_text("set by hand")
    page.locator("button.publish-save").click()
    expect(page.locator("[data-save-note]")).to_contain_text("No such part: T1")
    shared.fill("S1+S1b")
    expect(page.locator(".staff-groups-note")).to_have_text("set by hand")
    page.locator("button.publish-save").click()
    expect(page.locator("[data-save-note]")).to_have_text("Saved")
    assert state.load(slug).data["publish"]["staff_groups"] == [["S1", "S1b"]]

    page.locator(".publish-section select.publish-choir").select_option("jm")
    page.locator(".publish-section button.publish-btn").click()
    expect(page.locator(".publish-section .publish-status")).to_contain_text(
        "6 part(s)", timeout=15000)

    page.locator("[data-staff-groups-auto]").click()
    expect(page.locator("[data-staff-groups]")).to_have_value("S1+S1b, S2+S2b, A1+A2")
    page.locator("button.publish-save").click()
    expect(page.locator("[data-save-note]")).to_have_text("Saved")
    assert state.load(slug).data["publish"]["staff_groups"] is None
    assert handed == [[["S1", "S1b"]]]
    assert errors == []


def test_the_test_list_is_marked_and_publishing_there_keeps_the_choirs_listing(live, page):
    """#422: the site's test list (stemmanauhat-cf#73) says so, and a song tried out
    there stays on the choir's list it was already on."""
    base, slug, cf = live
    cf.db.execute("INSERT INTO choirs VALUES ('testi', 'Testilista', 0, 1)")
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{base}/#/song/{slug}")

    section = page.locator(".publish-section")
    choir = section.locator("select.publish-choir")
    expect(choir.locator("option")).to_have_text(
        ["— choir —", "Joensuun Mieslaulajat (jm)", "Naiskuoro (naiskuoro)",
         "Testilista (testi, test)", "Esittely (public, open to all)"])
    status = section.locator(".publish-status")
    choir.select_option("jm")
    section.locator("button.publish-btn").click()
    expect(status).to_contain_text("Published to Joensuun Mieslaulajat (jm)", timeout=15000)

    page.locator(".publish-section select.publish-choir").select_option("testi")
    page.locator(".publish-section button.publish-btn").click()
    expect(page.locator(".publish-section .publish-status")).to_contain_text(
        "Testilista (testi)", timeout=15000)
    assert [row[0] for row in cf.listed("jm")] == [slug]
    assert [row[0] for row in cf.listed("testi")] == [slug]
    page.locator(".publish-section").scroll_into_view_if_needed()
    _shot(page, "publish-test-list.png")


@pytest.mark.parametrize("size", [(1280, 900), (390, 844)], ids=["desktop", "phone"])
def test_a_new_part_name_is_placed_in_the_voice_tree_from_the_panel(live, page, size,
                                                                     monkeypatch, tmp_path):
    """#430: a part name the site's voice tree does not know is asked about, and
    the publish puts it where the panel says."""
    from src.song_app.tests.test_voice_tree import _with_tree
    base, slug, cf = live
    _with_tree(cf)
    monkeypatch.setattr(publish, "build_bundle",
                        lambda cleaned, out, part_names=None, **_kw: _bundle(
                            tmp_path, parts=tuple(part_names or ("S1", "A1"))))
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(f"{base}/#/song/{slug}")

    # The score's parts are S1 and A1, and this site's tree has only men's voices.
    asks = page.locator("[data-voice-places] select")
    expect(asks).to_have_count(2)
    expect(page.locator("[data-voice-places] strong")).to_have_text(["S1", "A1"])
    page.locator(".publish-section select.publish-choir").select_option("jm")
    page.locator(".publish-section button.publish-btn").click()
    expect(page.locator(".publish-section .publish-status")).to_contain_text(
        "Say where each new part name goes on the site: S1, A1")
    assert cf.listed("jm") == []

    # Renaming a part to a name the tree knows takes its question away.
    page.locator("[data-part-names] input").nth(0).fill("basso 1")
    expect(asks).to_have_count(1)
    page.locator("[data-voice-place='A1']").select_option("under:men")
    page.locator("[data-voice-places]").scroll_into_view_if_needed()
    _shot(page, f"publish-voice-place-{size[0]}.png")
    page.locator(".publish-section button.publish-btn").click()
    expect(page.locator(".publish-section .publish-status")).to_contain_text(
        "2 part(s)", timeout=15000)
    assert cf.db.execute("SELECT parent, names FROM voices WHERE id = 'A1'").fetchone() == (
        "men", '["A1"]')
    expect(asks).to_have_count(0)
    assert errors == []


def test_a_slow_answer_for_older_part_names_does_not_replace_the_newer_one(live, page):
    """Review of #430: only the reply for the names on screen now draws the questions."""
    from src.song_app.tests.test_voice_tree import _with_tree
    base, slug, cf = live
    _with_tree(cf)
    held = []

    def hold_first(route):
        if "Kertoja" in (route.request.post_data or "") or held:
            route.continue_()
        else:
            held.append(route)  # the lookup for the names as first rendered, answered last
    page.route("**/api/voice-tree", hold_first)
    page.goto(f"{base}/#/song/{slug}")
    expect(page.locator("[data-part-names] input")).to_have_count(2)
    page.locator("[data-part-names] input").nth(0).fill("Kertoja")
    asks = page.locator("[data-voice-places] strong")
    expect(asks).to_have_text(["Kertoja", "A1"])
    held[0].continue_()
    page.wait_for_timeout(500)
    expect(asks).to_have_text(["Kertoja", "A1"])
