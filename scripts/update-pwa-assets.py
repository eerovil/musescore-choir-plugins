#!/usr/bin/env python3
"""Regenerate the service worker's content-derived shell generation."""

from __future__ import annotations

import sys
from pathlib import Path

# Running ``python scripts/update-pwa-assets.py`` puts scripts/, not the repository
# root, on sys.path. Add the root explicitly so the documented command works in a
# fresh checkout without requiring PYTHONPATH or an editable package install.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.song_app.pwa_assets import STATIC_DIR, rendered_config


def main() -> None:
    target = STATIC_DIR / "pwa-assets.js"
    content = rendered_config()
    if target.exists() and target.read_text() == content:
        print(f"{target} is current")
        return
    target.write_text(content)
    print(f"updated {target}")


if __name__ == "__main__":
    main()
