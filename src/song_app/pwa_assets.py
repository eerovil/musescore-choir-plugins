"""PWA shell asset list and deterministic cache generation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

STATIC_DIR = Path(__file__).parent / "static"

# Keep live state out of this tuple. These are the only URLs the service worker
# may write to Cache Storage.
SHELL_ASSETS = (
    "/style.css",
    "/pwa.css",
    "/app.js",
    "/rendering_state.js",
    "/pwa.js",
    "/offline.html",
    "/favicon.svg",
    "/apple-touch-icon.png",
    "/icon-192.png",
    "/icon-512.png",
    "/icon-maskable-512.png",
    "/manifest.webmanifest",
)


def git_blob_sha(data: bytes) -> str:
    """The same content digest Git uses for a regular file blob."""
    header = f"blob {len(data)}\0".encode()
    return hashlib.sha1(header + data).hexdigest()


def cache_stamp(static_dir: Path = STATIC_DIR, assets: Iterable[str] = SHELL_ASSETS) -> str:
    """Content-derived cache generation for the service worker.

    Hash Git-style blob IDs rather than raw bytes so the same value can be
    calculated from a repository tree while preparing a GitHub-only change.
    """
    digest = hashlib.sha256()
    for url in sorted(assets):
        data = (static_dir / url.removeprefix("/")).read_bytes()
        digest.update(url.encode())
        digest.update(b"\0")
        digest.update(git_blob_sha(data).encode())
        digest.update(b"\n")
    return digest.hexdigest()[:12]


def rendered_config(static_dir: Path = STATIC_DIR) -> str:
    """Exact generated contents of ``static/pwa-assets.js``."""
    payload = {
        "cache": f"song-static-{cache_stamp(static_dir)}",
        "assets": list(SHELL_ASSETS),
    }
    return (
        "self.SONG_PWA = Object.freeze("
        + json.dumps(payload, separators=(",", ":"))
        + ");\n"
    )
