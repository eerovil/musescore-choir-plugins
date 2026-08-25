#!/usr/bin/env python3
"""Regenerate the service worker's content-derived shell generation."""

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
