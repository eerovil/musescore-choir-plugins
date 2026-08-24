"""The health endpoint the deploy watcher reads after a restart.

A 200 is not enough: the watcher parses the body and looks at `status`, so an
answer that merely responds still reads as "not healthy" and parks a merged card
in Blocked. That is what is pinned here.
"""
import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from src.song_app import server


def test_healthz_says_ok():
    with TestClient(server.app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
