"""Map song workspaces to ordinary AgentDeck chats.

The mapping lives in ``.song.json`` because it is part of the song's working
state: once a chat has been created, reopening the song should go back to that
same chat instead of spawning another one.

AgentDeck's human ``POST /sessions/new`` route is intentionally used for creation
rather than ``/api/delegations``.  A song chat is a normal boss-owned working
session, not a bounded delegated worker.  AgentDeck returns a stable
``/sessions/starting/<token>`` URL immediately; its machine API then lets us
resolve that token to the real session key without scraping HTML.
"""

from __future__ import annotations

import os
import re
import time
from typing import Dict, Optional
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, HTTPException

from . import state

router = APIRouter()

_START_GRACE_SECONDS = 120.0
_REQUEST_TIMEOUT_SECONDS = 10.0
_START_PATH = re.compile(r"^/sessions/starting/([0-9a-f]{32})$")


def _base(value: str) -> str:
    return (value or "").strip().rstrip("/")


def _config(mapping: Optional[Dict] = None) -> tuple[str, str, str]:
    """Return (browser base, server/API base, account key).

    ``AGENTDECK_API_URL`` exists for the common reverse-proxy setup: the browser
    opens AgentDeck through HTTPS while Choir talks to the same service over a
    loopback URL.  Existing mappings retain their browser origin as a fallback,
    so moving the songs repo does not erase the link merely because this machine
    has not been configured yet.
    """
    mapping = mapping or {}
    configured_public = _base(os.getenv("AGENTDECK_URL", ""))
    public = configured_public or _base(str(mapping.get("base_url") or ""))
    api = _base(os.getenv("AGENTDECK_API_URL", "")) or configured_public or public
    account = (os.getenv("AGENTDECK_ACCOUNT_KEY", "") or "").strip()
    return public, api, account


async def _request(method: str, url: str, **kwargs) -> httpx.Response:
    """Small seam for tests; never follows AgentDeck's browser redirects."""
    async with httpx.AsyncClient(
        timeout=_REQUEST_TIMEOUT_SECONDS, follow_redirects=False
    ) as client:
        return await client.request(method, url, **kwargs)


def _url(base: str, path: str) -> Optional[str]:
    return f"{base}{path}" if base else None


def _starting_path(mapping: Dict) -> Optional[str]:
    token = str(mapping.get("token") or "")
    return f"/sessions/starting/{token}" if token else None


def _session_path(mapping: Dict) -> Optional[str]:
    key = str(mapping.get("session_key") or "")
    return f"/sessions/{quote(key, safe='')}" if key else None


def _payload(
    status: str,
    mapping: Optional[Dict],
    *,
    public: str,
    detail: str = "",
    prefer_starting: bool = False,
) -> Dict:
    mapping = mapping or {}
    path = (
        _starting_path(mapping)
        if prefer_starting
        else (_session_path(mapping) or _starting_path(mapping))
    )
    return {
        "state": status,
        "url": _url(public, path) if path else None,
        "session_key": mapping.get("session_key"),
        "detail": detail,
    }


def _age(mapping: Dict) -> float:
    try:
        return max(0.0, time.time() - float(mapping.get("created_at") or 0.0))
    except (TypeError, ValueError):
        return _START_GRACE_SECONDS + 1


async def _known_session_status(song: state.Song, mapping: Dict, public: str, api: str) -> Dict:
    key = str(mapping.get("session_key") or "")
    if not key:
        return _payload("stale", mapping, public=public, detail="Mapped session has no key.")
    try:
        response = await _request(
            "GET", f"{api}/api/sessions/{quote(key, safe='')}/title"
        )
    except httpx.HTTPError as exc:
        return _payload(
            "unavailable", mapping, public=public,
            detail=f"Could not verify AgentDeck: {exc}",
        )
    if response.status_code == 200:
        return _payload("ready", mapping, public=public)
    if response.status_code == 404:
        # Token binding can precede the next session scan by a moment.  During
        # the same grace window as the starting page, this is still "starting",
        # not evidence that a just-created chat was deleted.
        if mapping.get("token") and _age(mapping) <= _START_GRACE_SECONDS:
            return _payload("starting", mapping, public=public, prefer_starting=True)
        return _payload(
            "stale", mapping, public=public,
            detail="The mapped AgentDeck session no longer exists.",
        )
    return _payload(
        "unavailable", mapping, public=public,
        detail=f"AgentDeck returned HTTP {response.status_code} while verifying the session.",
    )


async def status(song: state.Song) -> Dict:
    """Resolve and verify a song's AgentDeck mapping without guessing on failures."""
    raw = song.data.get("agentdeck")
    mapping = raw if isinstance(raw, dict) else None
    public, api, _account = _config(mapping)

    if mapping is None:
        if not public:
            return _payload(
                "unconfigured", None, public="",
                detail="Set AGENTDECK_URL and AGENTDECK_ACCOUNT_KEY to create song chats.",
            )
        return _payload("unmapped", None, public=public)

    if not public:
        return _payload(
            "unavailable", mapping, public="",
            detail="This song is mapped, but no AgentDeck browser URL is configured.",
        )
    if not api:
        return _payload(
            "unavailable", mapping, public=public,
            detail="This song is mapped, but no AgentDeck API URL is configured.",
        )

    if mapping.get("session_key"):
        return await _known_session_status(song, mapping, public, api)

    token = str(mapping.get("token") or "")
    if not token:
        return _payload(
            "stale", mapping, public=public,
            detail="The stored AgentDeck mapping is incomplete.",
        )

    try:
        response = await _request("GET", f"{api}/api/sessions/by-token/{quote(token, safe='')}")
    except httpx.HTTPError as exc:
        return _payload(
            "unavailable", mapping, public=public,
            detail=f"Could not reach AgentDeck: {exc}",
            prefer_starting=True,
        )

    if response.status_code == 404:
        if _age(mapping) <= _START_GRACE_SECONDS:
            return _payload("starting", mapping, public=public, prefer_starting=True)
        return _payload(
            "stale", mapping, public=public,
            detail="AgentDeck never resolved this start token; recreate the session.",
            prefer_starting=True,
        )
    if response.status_code != 200:
        return _payload(
            "unavailable", mapping, public=public,
            detail=f"AgentDeck returned HTTP {response.status_code} while resolving the session.",
            prefer_starting=True,
        )

    try:
        session_key = str(response.json().get("session_key") or "")
    except (ValueError, AttributeError):
        session_key = ""
    if not session_key:
        return _payload(
            "unavailable", mapping, public=public,
            detail="AgentDeck resolved the token without a session key.",
            prefer_starting=True,
        )

    mapping["session_key"] = session_key
    song.data["agentdeck"] = mapping
    song.save()
    return await _known_session_status(song, mapping, public, api)


def _first_message(song: state.Song) -> str:
    return (
        f'This AgentDeck chat is mapped to the choir song "{song.name}". '
        "Use this song directory as the working context. Do not make changes yet; "
        "wait for the user's instructions."
    )


async def create(song: state.Song, *, replace: bool = False) -> Dict:
    """Create one normal AgentDeck chat, unless this song already has a mapping."""
    existing = song.data.get("agentdeck")
    if isinstance(existing, dict) and not replace:
        return await status(song)

    public, api, account = _config(existing if isinstance(existing, dict) else None)
    if not public or not api or not account:
        raise HTTPException(
            503,
            "AgentDeck is not configured. Set AGENTDECK_URL and "
            "AGENTDECK_ACCOUNT_KEY (and AGENTDECK_API_URL when its internal URL differs).",
        )

    try:
        response = await _request(
            "POST",
            f"{api}/sessions/new",
            data={
                "account_key": account,
                "cwd": song.dir,
                "message": _first_message(song),
            },
        )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Could not create AgentDeck session: {exc}") from exc

    if response.status_code != 202:
        raise HTTPException(
            502,
            f"AgentDeck refused session creation (HTTP {response.status_code}).",
        )
    redirect = response.headers.get("HX-Redirect", "")
    match = _START_PATH.fullmatch(urlsplit(redirect).path)
    if match is None:
        raise HTTPException(502, "AgentDeck created a session without a usable start URL.")

    mapping = {
        "base_url": public,
        "token": match.group(1),
        "session_key": None,
        "created_at": time.time(),
    }
    # Replace only after AgentDeck accepted the new start.  A failed recreation
    # therefore leaves the old mapping available rather than destroying it first.
    song.data["agentdeck"] = mapping
    song.save()
    return _payload("starting", mapping, public=public, prefer_starting=True)


@router.get("/api/songs/{slug}/agentdeck")
async def api_agentdeck_status(slug: str) -> Dict:
    song = state.load(slug)
    if not song:
        raise HTTPException(404, f"No song '{slug}'")
    return await status(song)


@router.post("/api/songs/{slug}/agentdeck")
async def api_agentdeck_create(slug: str, body: Optional[Dict] = None) -> Dict:
    song = state.load(slug)
    if not song:
        raise HTTPException(404, f"No song '{slug}'")
    return await create(song, replace=bool((body or {}).get("replace")))
