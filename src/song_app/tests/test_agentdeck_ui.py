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


def test_unconfigured_agentdeck_action_says_why_instead_of_nothing(live_app, page):
    """A tap with nothing to open must hand over the reason (#52).

    The old button was disabled and carried its reason in a title tooltip, which
    a phone never shows, so the whole state read as a dead button.
    """
    detail = "Set AGENTDECK_URL and AGENTDECK_ACCOUNT_KEY to create song chats."
    posted = []

    def agentdeck_route(route, request):
        if request.method == "POST":
            posted.append(request.post_data_json)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=(
                '{"state":"unconfigured","url":null,"session_key":null,'
                f'"detail":"{detail}"}}'
            ),
        )

    page.route("**/api/songs/*/agentdeck", agentdeck_route)
    _new_song(page, live_app, "Unconfigured Song", per_system=False)

    action = page.locator("#agentdeck-action")
    expect(action).to_have_text("AgentDeck ⚠")
    expect(action).to_be_enabled()

    note = page.locator("#agentdeck-note")
    expect(note).to_be_hidden()
    action.click()
    expect(note).to_be_visible()
    expect(note).to_have_text(detail)
    expect(action).to_have_text("AgentDeck ⚠")
    # Nothing to create in this state, so the tap must not have posted either.
    assert posted == []
