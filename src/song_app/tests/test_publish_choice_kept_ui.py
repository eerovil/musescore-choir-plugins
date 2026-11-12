"""The Publish panel keeps the chosen choir when the page draws it again.

A saved score makes the server send the page a "state" message, and the panel
is drawn again. A choir chosen on screen and not yet published to has to
survive that, or Publish answers "Choose a choir first." In a long run the
health re-check of the cleaned score this suite writes could land between
choosing and pressing Publish, which is how test_publish_ui.py failed now and
then in a full run and never alone.
"""
import time

import pytest

from src.song_app import server
from src.song_app.tests.test_publish_ui import live  # noqa: F401  (the same live app and fake Cloudflare)

from playwright.sync_api import expect  # noqa: E402

pytestmark = pytest.mark.browser


def _wait_for_page_connection(slug, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server.hub.conns.get(slug) and server.hub.loop is not None:
            return
        time.sleep(0.05)
    raise AssertionError("the page never opened its live connection to the app")


@pytest.mark.parametrize("size", [(1280, 900), (390, 844)], ids=["desktop", "phone"])
def test_the_chosen_choir_survives_the_panel_being_drawn_again(live, page, size):
    base, slug, cf = live
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(f"{base}/#/song/{slug}")

    choir = page.locator(".publish-section select.publish-choir")
    expect(choir.locator("option")).to_have_count(4)
    choir.select_option("jm")
    expect(choir).to_have_value("jm")

    choir.evaluate("el => { el.dataset.beforeRedraw = '1'; }")
    _wait_for_page_connection(slug)
    server.hub.emit(slug, {"type": "state"})
    page.wait_for_function(
        "() => document.querySelector('select.publish-choir')"
        " && !document.querySelector('select.publish-choir[data-before-redraw]')",
        timeout=10000)

    expect(choir.locator("option")).to_have_count(4)
    expect(choir).to_have_value("jm")

    page.locator(".publish-section button.publish-btn").click()
    expect(page.locator(".publish-section .publish-status")).to_contain_text(
        "Published to Joensuun Mieslaulajat (jm)", timeout=15000)
    assert [row[0] for row in cf.listed("jm")] == [slug]
    assert errors == []
