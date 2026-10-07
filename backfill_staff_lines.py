#!/usr/bin/env python3
"""Add `stemmanauha-staff: N/M` to the descriptions of videos already uploaded.

  .venv/bin/python backfill_staff_lines.py --dry-run     # print title → line
  .venv/bin/python backfill_staff_lines.py               # write them (YouTube login)
  .venv/bin/python backfill_staff_lines.py lempilintu    # only these song folders

Run from the repo root (token.pickle / client_secrets.json are read from here).
"""
import sys

import dotenv

dotenv.load_dotenv()

from src.stemmanauha.backfill_staff import main  # noqa: E402

sys.exit(main())
