"""Singing from the app (#510) in a real browser: Sing and publish on the Publish panel, and Try
in the Pronunciation list. The routes and sing_run are pinned in `test_sing_api.py`; here
sing_run is a fake that answers like a unit going through its states, so what is pinned is the
person's path. Screenshots go to `EVIDENCE_DIR` when the run names one."""
import os

import pytest

from src.song_app.tests import test_publish_ui as pub   # skips the module without a browser

from playwright.sync_api import expect  # noqa: E402

from src.song_app import server  # noqa: E402

pytestmark = pytest.mark.browser
live = pub.live
_shot = pub._shot

PLACES = [{"bar": 2, "beat": 0.0, "word": "wind", "n": 1, "parts": ["Solo"], "staves": ["2"],
           "line": "the wind blows", "at": 1}]
# two parts called alike: the second one's Try must sing the second one (#510 review)
SUNG = [{"n": 1, "name": "Solo", "staves": ["1"]}, {"n": 2, "name": "Solo", "staves": ["2"]}]
WIND = [{"sounds": "W AY1 N D", "say": "wynd", "as_in": "find"}, {"sounds": "W IH1 N D", "say": "wind", "as_in": "sit"}]


class FakeSing:
    """sing_run as the routes see it; `calls` lists what was started, in order."""

    def __init__(self, tmp):
        self.calls, self.run, self.trial, self.mp3 = [], {"state": "none"}, {"state": "none"}, tmp / "t.mp3"
        self.mp3.write_bytes(b"ID3")

    def install(self, monkeypatch):
        m = server.sing_run
        monkeypatch.setattr(m, "busy_reason", lambda slug: None)
        monkeypatch.setattr(m, "start", self.start)
        monkeypatch.setattr(m, "status", lambda slug, d: dict(self.run))
        monkeypatch.setattr(m, "stop", self.stop)
        monkeypatch.setattr(m, "active", lambda name: False)
        monkeypatch.setattr(m, "start_try", self.start_try)
        monkeypatch.setattr(m, "try_status", lambda slug: dict(self.trial))
        monkeypatch.setattr(m, "try_mp3", lambda slug, name: str(self.mp3) if name == "1-T1-bars-2-2.mp3" else None)

    def start(self, slug, choir):
        self.calls.append(("sing", choir))
        self.run = {"state": "singing", "choir": choir,
                    "run": {"state": "singing", "part": 2, "parts": 4, "part_name": "T2", "choirs": [choir]}}
        return {"run_id": "r1", "choir": choir}

    def stop(self, name):
        self.calls.append(("stop", name))
        self.run = {"state": "failed", "choir": "jm",
                    "run": {"state": "failed", "error": "stopped (SIGTERM)", "finished": 1}}

    def start_try(self, slug, part, first, last, name=None):
        self.calls.append(("try", part, first, last))
        self.trial = {"state": "waiting", "id": "1", "part": part, "name": name, "first": first, "last": last}
        return self.trial


@pytest.fixture
def sing(live, tmp_path, monkeypatch):
    fake = FakeSing(tmp_path)
    fake.install(monkeypatch)
    monkeypatch.setattr(server.song_places, "places", lambda score: [dict(p) for p in PLACES])
    monkeypatch.setattr(server, "_readings", lambda words: {"wind": WIND})
    monkeypatch.setattr(server, "_sung_parts", lambda song, cleaned: [dict(q) for q in SUNG])
    return live, fake


@pytest.mark.parametrize("size", [(1280, 900), (390, 844)], ids=["desktop", "phone"])
def test_sing_and_publish_shows_progress_and_stops(sing, page, size):
    (base, slug, cf), fake = sing
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script("window.SING_POLL_MS = 200;")
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(f"{base}/#/song/{slug}")
    section = page.locator(".publish-section")
    sing_btn = section.locator(".sing-btn")
    expect(sing_btn).to_have_text("Sing and publish")
    expect(section.locator(".sing-stop")).to_be_hidden()
    sing_btn.click()
    expect(section.locator(".publish-status")).to_have_text("Choose a choir first.")
    assert fake.calls == []
    section.locator("select.publish-choir").select_option("jm")
    sing_btn.click()
    status = section.locator(".sing-status")
    expect(status).to_contain_text("T2")
    expect(sing_btn).to_be_disabled()
    stop = section.locator(".sing-stop")
    expect(stop).to_be_visible()
    assert fake.calls == [("sing", "jm")]
    box = sing_btn.bounding_box()
    assert box and box["x"] + box["width"] <= size[0], "the button runs off the screen"
    status.scroll_into_view_if_needed()
    _shot(page, f"sing-running-{size[0]}.png")
    stop.click()
    expect(stop).to_be_hidden()
    expect(sing_btn).to_be_enabled()
    expect(status).to_contain_text("Singing failed")
    expect(status).to_contain_text("stopped")
    assert fake.calls[-1] == ("stop", f"sing-{slug}")
    assert not errors, errors


@pytest.mark.parametrize("size", [(1280, 900), (390, 844)], ids=["desktop", "phone"])
def test_try_saves_unsaved_readings_first_and_plays_the_mp3(sing, page, size):
    (base, slug, cf), fake = sing
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script("window.TRY_POLL_MS = 200;")
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(f"{base}/#/song/{slug}")
    page.locator("[data-pronunciation] summary").click()
    page.wait_for_selector(".pron-row")
    page.locator('[data-pron-place="2:0:wind"]').select_option("W IH1 N D")
    order = []
    page.on("request", lambda r: order.append(r.url.rsplit("/api/songs/", 1)[-1])
            if r.method == "POST" else None)
    page.locator('[data-pron-try="2:wind"]').click()
    out = page.locator("[data-pron-try-out]")
    expect(out).to_contain_text("Waiting for the graphics card… (Solo, bar 2)")
    assert order == [f"{slug}/pronunciation", f"{slug}/sing/try"], order
    assert fake.calls == [("try", "#2", 2, 2)]
    out.scroll_into_view_if_needed()
    _shot(page, f"try-waiting-{size[0]}.png")
    fake.trial = dict(fake.trial, state="done", mp3="1-T1-bars-2-2.mp3", music=2.5, took=9.0)
    expect(out).to_contain_text("Solo, bar 2: music 2.5 s, sung in 9 s")
    audio = out.locator("audio")
    src = audio.get_attribute("src")
    assert src.endswith("/sing/try/1-T1-bars-2-2.mp3?id=1")
    reply = page.request.get(base + src)
    assert reply.ok and reply.headers["content-type"] == "audio/mpeg"
    audio.scroll_into_view_if_needed()
    _shot(page, f"try-done-{size[0]}.png")
    # Try bars: any part, any bars; nothing unsaved now, so straight to the singer
    part = page.locator("[data-pron-try-part]")
    assert part.locator("option").all_inner_texts() == ["1. Solo", "2. Solo"]
    part.select_option(index=0)
    page.locator("[data-pron-try-first]").fill("3")
    page.locator("[data-pron-try-last]").fill("5")
    page.locator("[data-pron-try-bars]").click()
    expect(out).to_contain_text("bars 3-5")
    assert fake.calls[-1] == ("try", "#1", 3, 5)
    assert order.count(f"{slug}/pronunciation") == 1
    fake.trial = dict(fake.trial, state="failed", log=["no part named X"])
    expect(out).to_contain_text("no part named X")
    for loc in (page.locator("[data-pron-try-bars]"), audio if audio.count() else out):
        box = loc.bounding_box()
        assert box is None or box["x"] + box["width"] <= size[0] + 1
    assert not errors, errors
