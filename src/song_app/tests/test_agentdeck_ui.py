"""Browser coverage for the song workspace's AgentDeck action."""

import pytest

pytest.importorskip("playwright.sync_api", reason="browser tests need Playwright")
pytest.importorskip("pytest_playwright", reason="browser tests need pytest-playwright")

from playwright.sync_api import expect

from src.song_app.tests.test_ui_flow import _new_song, live_app

pytestmark = pytest.mark.browser


def test_agentdeck_action_creates_and_recreates_from_workspace(live_app, page):
    fake = {"state": "unmapped", "posts": []}

    def agentdeck_route(route, request):
        if request.method == "POST":
            fake["posts"].append(request.post_data_json)
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"state":"starting","url":null,"session_key":null,"detail":""}',
            )
            return
        states = {
            "unmapped": '{"state":"unmapped","url":null,"session_key":null,"detail":""}',
            "ready": '{"state":"ready","url":null,"session_key":"session-1","detail":""}',
            "stale": '{"state":"stale","url":null,"session_key":"session-1","detail":"gone"}',
        }
        route.fulfill(status=200, content_type="application/json", body=states[fake["state"]])

    page.route("**/api/songs/*/agentdeck", agentdeck_route)
    _new_song(page, live_app, "AgentDeck Song", per_system=False)

    action = page.locator("#agentdeck-action")
    expect(action).to_have_text("+ AgentDeck")
    action.click()
    expect(action).to_have_text("AgentDeck…")
    assert fake["posts"] == [{"replace": False}]

    fake["state"] = "ready"
    page.reload()
    expect(action).to_have_text("AgentDeck")
    expect(action).to_have_attribute("title", "Open this song's AgentDeck chat")

    fake["state"] = "stale"
    page.reload()
    expect(action).to_have_text("Recreate AgentDeck")
    action.click()
    expect(action).to_have_text("AgentDeck…")
    assert fake["posts"][-1] == {"replace": True}
