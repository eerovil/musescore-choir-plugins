"""Song ↔ AgentDeck session mapping and recovery behavior."""

import time

import httpx
import pytest

from src.song_app import agentdeck, state

TOKEN = "0123456789abcdef0123456789abcdef"
TOKEN2 = "fedcba9876543210fedcba9876543210"
SESSION = "claude_code:main:session-123"


@pytest.fixture
def song(tmp_path, monkeypatch):
    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    return state.create("My Song", per_system=False)


@pytest.fixture(autouse=True)
def agentdeck_env(monkeypatch):
    monkeypatch.setenv("AGENTDECK_URL", "https://deck.example")
    monkeypatch.setenv("AGENTDECK_API_URL", "http://127.0.0.1:9090")
    monkeypatch.setenv("AGENTDECK_ACCOUNT_KEY", "claude_code:main")


def response(code, *, headers=None, data=None):
    return httpx.Response(code, headers=headers or {}, json=data) if data is not None \
        else httpx.Response(code, headers=headers or {})


@pytest.mark.asyncio
async def test_create_persists_start_token_and_reuses_mapping(song, monkeypatch):
    calls = []

    async def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "POST":
            return response(202, headers={"HX-Redirect": f"/sessions/starting/{TOKEN}"})
        return response(404)

    monkeypatch.setattr(agentdeck, "_request", fake_request)

    created = await agentdeck.create(song)
    assert created["state"] == "starting"
    assert created["url"] == f"https://deck.example/sessions/starting/{TOKEN}"

    saved = state.load(song.slug)
    assert saved.data["agentdeck"]["token"] == TOKEN
    assert saved.data["agentdeck"]["session_key"] is None
    assert saved.data["agentdeck"]["base_url"] == "https://deck.example"
    post = calls[0]
    assert post[0:2] == ("POST", "http://127.0.0.1:9090/sessions/new")
    assert post[2]["data"]["account_key"] == "claude_code:main"
    assert post[2]["data"]["cwd"] == song.dir
    assert "My Song" in post[2]["data"]["message"]

    # Pressing create again is idempotent: it checks the saved start rather than
    # creating a second AgentDeck chat.
    again = await agentdeck.create(saved)
    assert again["state"] == "starting"
    assert sum(method == "POST" for method, _url, _kwargs in calls) == 1


@pytest.mark.asyncio
async def test_start_token_resolves_to_session_and_is_persisted(song, monkeypatch):
    song.data["agentdeck"] = {
        "base_url": "https://deck.example",
        "token": TOKEN,
        "session_key": None,
        "created_at": time.time(),
    }
    song.save()
    calls = []

    async def fake_request(method, url, **kwargs):
        calls.append(url)
        if "/by-token/" in url:
            return response(200, data={"session_key": SESSION})
        return response(200, data={"title": "My Song"})

    monkeypatch.setattr(agentdeck, "_request", fake_request)
    status = await agentdeck.status(song)

    assert status == {
        "state": "ready",
        "url": "https://deck.example/sessions/claude_code%3Amain%3Asession-123",
        "session_key": SESSION,
        "detail": "",
    }
    assert state.load(song.slug).data["agentdeck"]["session_key"] == SESSION
    assert calls == [
        f"http://127.0.0.1:9090/api/sessions/by-token/{TOKEN}",
        "http://127.0.0.1:9090/api/sessions/claude_code%3Amain%3Asession-123/title",
    ]


@pytest.mark.asyncio
async def test_deleted_session_is_stale_and_can_be_recreated(song, monkeypatch):
    song.data["agentdeck"] = {
        "base_url": "https://deck.example",
        "token": TOKEN,
        "session_key": SESSION,
        "created_at": time.time() - 1000,
    }
    song.save()
    calls = []

    async def fake_request(method, url, **kwargs):
        calls.append((method, url))
        if method == "POST":
            return response(202, headers={"HX-Redirect": f"/sessions/starting/{TOKEN2}"})
        return response(404)

    monkeypatch.setattr(agentdeck, "_request", fake_request)

    stale = await agentdeck.status(song)
    assert stale["state"] == "stale"
    assert stale["url"].endswith("/sessions/claude_code%3Amain%3Asession-123")

    recreated = await agentdeck.create(song, replace=True)
    assert recreated["state"] == "starting"
    assert recreated["url"].endswith(f"/sessions/starting/{TOKEN2}")
    saved = state.load(song.slug).data["agentdeck"]
    assert saved["token"] == TOKEN2
    assert saved["session_key"] is None
    assert [method for method, _url in calls].count("POST") == 1


@pytest.mark.asyncio
async def test_temporary_agentdeck_failure_does_not_destroy_mapping(song, monkeypatch):
    mapping = {
        "base_url": "https://deck.example",
        "token": TOKEN,
        "session_key": SESSION,
        "created_at": time.time() - 1000,
    }
    song.data["agentdeck"] = dict(mapping)
    song.save()

    async def unavailable(*_args, **_kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(agentdeck, "_request", unavailable)
    result = await agentdeck.status(song)

    assert result["state"] == "unavailable"
    assert result["url"].endswith("/sessions/claude_code%3Amain%3Asession-123")
    assert state.load(song.slug).data["agentdeck"] == mapping


@pytest.mark.asyncio
async def test_failed_recreate_keeps_previous_mapping(song, monkeypatch):
    mapping = {
        "base_url": "https://deck.example",
        "token": TOKEN,
        "session_key": SESSION,
        "created_at": time.time() - 1000,
    }
    song.data["agentdeck"] = dict(mapping)
    song.save()

    async def refused(*_args, **_kwargs):
        return response(422)

    monkeypatch.setattr(agentdeck, "_request", refused)
    with pytest.raises(Exception) as exc:
        await agentdeck.create(song, replace=True)
    assert getattr(exc.value, "status_code", None) == 502
    assert state.load(song.slug).data["agentdeck"] == mapping


@pytest.mark.asyncio
async def test_create_requires_configuration(song, monkeypatch):
    monkeypatch.delenv("AGENTDECK_URL")
    monkeypatch.delenv("AGENTDECK_API_URL")
    monkeypatch.delenv("AGENTDECK_ACCOUNT_KEY")

    status = await agentdeck.status(song)
    assert status["state"] == "unconfigured"
    with pytest.raises(Exception) as exc:
        await agentdeck.create(song)
    assert getattr(exc.value, "status_code", None) == 503


def test_workspace_shell_contains_agentdeck_action():
    html = (agentdeck.state.SCRIPT_DIR and __import__("pathlib").Path(
        agentdeck.state.SCRIPT_DIR, "src", "song_app", "static", "index.html"
    ).read_text())
    assert 'id="agentdeck-action"' in html
    assert "/agentdeck`" in html
    assert "Recreate AgentDeck" in html
