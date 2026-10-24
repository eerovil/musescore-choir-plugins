"""The song list's re-sing status in a real browser (#453).

The site's list of sung songs is the saved read (`songs/.site-songs.json`, fresh, so the
app does not ask Cloudflare) and each run is a `.sing-run.json` as sing_song.py writes it.
What is pinned is what a person reads on the list: which songs still need a re-sing and in
which choirs, a run's step and part, a failure's reason, the filter, and that a run going
on is read again without reloading. Screenshots go to `EVIDENCE_DIR` when the run names one.
"""
import json
import os
import time

import pytest

from src.song_app.tests.test_publish_ui import _NEEDS, _free_port, _shot  # noqa: F401 - skips without a browser

import threading  # noqa: E402

import uvicorn  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

from src.sing_song import progress  # noqa: E402
from src.song_app import publish, resing, server, state  # noqa: E402

pytestmark = pytest.mark.browser


def _song(name, sites=None):
    song = state.create(name, per_system=False, voicing="men")
    song.data.update(stage="publish", publish={"sites": sites or {}})
    song.save()
    return song


@pytest.fixture
def live(tmp_path, monkeypatch):
    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    monkeypatch.setenv("MUSESCORE_CLI_PATH", "/no-musescore-here")
    for key in ("AGENTDECK_API_URL", "AGENTDECK_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(publish.Config, "from_env", classmethod(lambda cls: None))
    version = progress.singer_version()

    hanget = _song("Hanget soi")
    karhu = _song("Karhunpyynti")
    vanha = _song("Vanha virsi", {"jm": {"choir": "jm", "prefix": "songs/jm/vanha-virsi/v1/",
                                        "sung": True, "singer_version": version}})
    kesa = _song("Kesäilta")
    rows = []
    for slug, choirs in ((hanget.slug, ("jm", "public")), (karhu.slug, ("jm",)),
                         (vanha.slug, ("jm",)), (kesa.slug, ("joy", "joy-alumnit"))):
        rows += [{"choir": c, "slug": slug, "prefix": f"songs/{c}/{slug}/v1/", "sung": True} for c in choirs]
    (songs / resing.SITE_FILE).write_text(json.dumps(
        {"songs": rows, "staging": ["testi"], "at": time.time() + 3600}))

    progress.write(state.song_dir(hanget.slug), {
        "state": "singing", "pid": os.getpid(), "started": time.time() - 300, "version": version,
        "choirs": ["jm", "public"], "part": 2, "parts": 4, "part_name": "T2"})
    progress.write(state.song_dir(karhu.slug), {
        "state": "failed", "started": time.time() - 900, "finished": time.time() - 600,
        "version": version, "choirs": ["jm"],
        "error": "B1 lasts 61.20 s, the song 60.00 s"})
    progress.mark_waiting(state.song_dir(kesa.slug), ["joy", "joy-alumnit"])

    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not srv.started and time.time() < deadline:
        time.sleep(0.05)
    assert srv.started, "the app did not start"
    try:
        yield f"http://127.0.0.1:{port}", {"hanget": hanget, "karhu": karhu, "vanha": vanha, "kesa": kesa}
    finally:
        srv.should_exit = True
        thread.join(timeout=10)


@pytest.mark.parametrize("size", [(1280, 900), (390, 844)], ids=["desktop", "phone"])
def test_the_list_says_what_needs_a_re_sing_and_how_each_run_goes(live, page, size):
    base, songs = live
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(f"{base}/")

    card = lambda song: page.locator(f'.card[data-slug="{song.slug}"]')  # noqa: E731
    expect(page.locator(".sing-head")).to_contain_text("3 song(s) to re-sing, 2 run(s) waiting or going.")
    expect(card(songs["hanget"]).locator(".sing")).to_contain_text("Needs re-sing: jm, public")
    expect(card(songs["hanget"]).locator(".sing")).to_contain_text("Singing part 2 of 4 (T2)")
    expect(card(songs["karhu"]).locator(".sing")).to_contain_text("Re-sing failed")
    expect(card(songs["karhu"]).locator(".sing")).to_contain_text("B1 lasts 61.20 s, the song 60.00 s")
    expect(card(songs["kesa"]).locator(".sing")).to_contain_text("Re-sing waiting (joy, joy-alumnit)")
    expect(card(songs["vanha"]).locator(".sing")).to_have_text("✓ Sung with the current singer")
    _shot(page, f"resing-list-{size[0]}.png")

    # a run moving on is read again without reloading the page
    progress.write(state.song_dir(songs["hanget"].slug), {
        "state": "publishing", "pid": os.getpid(), "version": progress.singer_version(),
        "choirs": ["jm", "public"], "choir": "public", "choir_index": 2, "choir_count": 2})
    expect(card(songs["hanget"]).locator(".sing")).to_contain_text("Publishing to public (2 of 2)", timeout=15000)

    page.locator(".sing-filter input").check()
    expect(card(songs["vanha"])).to_be_hidden()
    expect(card(songs["hanget"])).to_be_visible()
    _shot(page, f"resing-only-pending-{size[0]}.png")
    assert errors == []


def test_a_run_queued_while_the_idle_list_is_open_shows_up_without_reloading(live, page):
    # #454 review: with nothing running the list still looks again (every minute; 1 s here)
    base, songs = live
    for song in songs.values():
        path = os.path.join(state.song_dir(song.slug), progress.RUN_FILE)
        if os.path.exists(path):
            os.remove(path)
    page.add_init_script("window.RESING_IDLE_MS = 1000; window.RESING_BUSY_MS = 1000;")
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{base}/")
    sing = page.locator(f'.card[data-slug="{songs["karhu"].slug}"] .sing')
    expect(page.locator(".sing-head")).to_contain_text("3 song(s) to re-sing.")
    expect(sing).to_have_text("Needs re-sing: jm")

    progress.mark_waiting(state.song_dir(songs["karhu"].slug), ["jm"])
    expect(sing).to_contain_text("Re-sing waiting (jm)", timeout=10000)
    progress.write(state.song_dir(songs["karhu"].slug), {
        "state": "singing", "pid": os.getpid(), "version": progress.singer_version(),
        "choirs": ["jm"], "part": 1, "parts": 4, "part_name": "T1"})
    expect(sing).to_contain_text("Singing part 1 of 4 (T1)", timeout=10000)


def test_the_list_recovers_from_a_failed_read_and_tells_a_piano_only_run_from_a_re_sing(live, page):
    # #454 review: one failed /api/resing must not stop the list looking again, and a
    # --no-sing run or one that published nowhere is not shown as a re-sing
    base, songs = live
    failures = []

    def fail_once(route):
        if not failures:
            failures.append(route.request.url)
            route.fulfill(status=500, body="restarting")
        else:
            route.continue_()
    page.route("**/api/resing", fail_once)
    page.add_init_script("window.RESING_IDLE_MS = 1000; window.RESING_BUSY_MS = 1000;")
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{base}/")
    expect(page.locator(".sing-head")).to_contain_text("song(s) to re-sing", timeout=10000)
    assert failures

    done = {"state": "done", "pid": os.getpid(), "finished": time.time(), "version": progress.singer_version()}
    progress.write(state.song_dir(songs["karhu"].slug), dict(done, sing=False, choirs=[]))
    progress.write(state.song_dir(songs["kesa"].slug), dict(done, sing=True, choirs=[]))
    progress.write(state.song_dir(songs["hanget"].slug), dict(done, sing=True, choirs=["jm"]))
    sing = lambda song: page.locator(f'.card[data-slug="{songs[song].slug}"] .sing')  # noqa: E731
    expect(sing("karhu")).to_contain_text("Piano-only run done", timeout=10000)
    expect(sing("karhu")).not_to_contain_text("Re-sung")
    expect(sing("kesa")).to_contain_text("Sung, not published")
    expect(sing("hanget")).to_contain_text("Re-sung")
