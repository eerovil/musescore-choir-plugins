#!/usr/bin/env python3
"""Hide the tempo marks of the songs already on the stemmanauhat site (#409).

Publish hides them from now on; this gives every song published before that a
fresh version with them hidden (see ``publish.hide_published_tempo_marks``).

    .venv/bin/python scripts/hide_published_tempo_marks.py --dry-run
    .venv/bin/python scripts/hide_published_tempo_marks.py

Run from the repo root. Reads the Cloudflare keys from ``.env``, then from the
file the stemmanauhat-cf scripts use (``--cloudflare-env``) for any still unset.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

from src.song_app import publish, state  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="say what would change, write nothing")
    parser.add_argument("--cloudflare-env", default=os.path.expanduser(
        "~/.local/share/stemmanauhat/cloudflare.env"))
    args = parser.parse_args()
    load_dotenv(".env")
    if os.path.isfile(args.cloudflare_env):
        load_dotenv(args.cloudflare_env)
    config = publish.Config.from_env()
    if config is None:
        print(publish.MISSING_CONFIG, file=sys.stderr)
        return 2
    changed = publish.hide_published_tempo_marks(
        publish.Cloudflare(config), songs_dir=state.SONGS_DIR, dry_run=args.dry_run, log=print)
    print(f"{len(changed)} song(s) {'would get' if args.dry_run else 'got'} a version "
          "with their tempo marks hidden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
