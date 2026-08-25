"""Installability, cache boundaries, and response headers for the mobile PWA."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from PIL import Image

from src.song_app import pwa_assets, server, state

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "SONGS_DIR", str(tmp_path))
    with TestClient(server.app) as test_client:
        yield test_client


def _config(text: str) -> dict:
    prefix = "self.SONG_PWA = Object.freeze("
    suffix = ");\n"
    assert text.startswith(prefix)
    assert text.endswith(suffix)
    return json.loads(text[len(prefix):-len(suffix)])


def test_manifest_is_standalone_and_has_regular_and_maskable_icons(client):
    response = client.get("/manifest.webmanifest")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json")
    manifest = response.json()
    assert manifest["id"] == "./choir-pwa"
    assert manifest["start_url"] == "./#/"
    assert manifest["scope"] == "./"
    assert manifest["display"] == "standalone"
    assert manifest["theme_color"] == "#14151a"
    assert {(icon["sizes"], icon["purpose"]) for icon in manifest["icons"]} == {
        ("192x192", "any"),
        ("512x512", "any"),
        ("512x512", "maskable"),
    }

    for icon in manifest["icons"]:
        image_response = client.get(icon["src"])
        assert image_response.status_code == 200, icon["src"]
        assert image_response.headers["content-type"] == "image/png"
        with Image.open(BytesIO(image_response.content)) as image:
            side = int(icon["sizes"].split("x", 1)[0])
            assert image.size == (side, side)


def test_manifest_identity_and_launch_preserve_non_default_port(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "SONGS_DIR", str(tmp_path))
    origin = "https://bazzite.taile8d16e.ts.net:8443"
    with TestClient(server.app, base_url=origin) as port_client:
        response = port_client.get("/manifest.webmanifest")

    manifest = response.json()
    base = str(response.url)
    assert urljoin(base, manifest["id"]) == origin + "/choir-pwa"
    assert urljoin(base, manifest["start_url"]) == origin + "/#/"
    assert urljoin(base, manifest["scope"]) == origin + "/"
    assert {
        urljoin(base, icon["src"]) for icon in manifest["icons"]
    } == {
        origin + "/icon-192.png",
        origin + "/icon-512.png",
        origin + "/icon-maskable-512.png",
    }


def test_service_worker_is_root_scoped_javascript_and_never_caches_live_data(client):
    response = client.get("/sw.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["cache-control"] == "no-cache"
    source = response.text
    assert 'importScripts("/pwa-assets.js")' in source
    assert 'request.mode === "navigate"' in source
    assert 'pathname.startsWith("/api/")' in source
    assert 'pathname.startsWith("/ws/")' in source
    assert 'pathname.startsWith("/media/")' in source
    assert 'ASSETS.includes(url.pathname)' in source


def test_generated_shell_generation_matches_every_cached_asset(client, tmp_path):
    response = client.get("/pwa-assets.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"

    config = _config(response.text)
    assert response.text == pwa_assets.rendered_config()
    assert config["assets"] == list(pwa_assets.SHELL_ASSETS)
    assert config["cache"] == f"song-static-{pwa_assets.cache_stamp()}"

    live_prefixes = ("/api/", "/ws/", "/songs/", "/media/")
    assert not any(
        asset == "/healthz" or asset.startswith(live_prefixes)
        for asset in config["assets"]
    )

    for asset in config["assets"]:
        shell_response = client.get(asset)
        assert shell_response.status_code == 200, asset
        assert shell_response.headers["cache-control"] == "no-cache", asset

    copied = tmp_path / "static"
    shutil.copytree(pwa_assets.STATIC_DIR, copied)
    before = pwa_assets.cache_stamp(copied)
    with (copied / "pwa.css").open("a", encoding="utf-8") as handle:
        handle.write("\n/* changed */\n")
    assert pwa_assets.cache_stamp(copied) != before


def test_checked_in_regeneration_command_runs_from_repo_root():
    completed = subprocess.run(
        [sys.executable, "scripts/update-pwa-assets.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "is current" in completed.stdout


def test_index_has_mobile_install_metadata_and_safe_area_styles(client):
    html = client.get("/").text

    assert 'viewport-fit=cover' in html
    assert 'rel="manifest" href="/manifest.webmanifest"' in html
    assert 'rel="apple-touch-icon" href="/apple-touch-icon.png"' in html
    assert 'name="apple-mobile-web-app-capable" content="yes"' in html
    assert 'name="mobile-web-app-capable" content="yes"' in html
    assert 'name="theme-color" content="#14151a"' in html
    assert '<script src="/pwa.js"></script>' in html

    css = client.get("/pwa.css").text
    for inset in ("top", "right", "bottom", "left"):
        assert f"safe-area-inset-{inset}" in css


def test_offline_shell_retries_health_without_caching_app_state(client):
    response = client.get("/offline.html")

    assert response.status_code == 200
    assert "Reconnecting to Choir tracks" in response.text
    assert 'fetch(`/healthz?reconnect=${Date.now()}`' in response.text
    assert 'cache: "no-store"' in response.text
    assert "location.reload()" in response.text
