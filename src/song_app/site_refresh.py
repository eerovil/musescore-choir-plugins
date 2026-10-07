"""Ask the stemmanauhat site to pick up a fresh upload now.

The practice-track site (``eerovil/stemmanauhat``, GitHub Pages) lists each
choir's YouTube playlist. Its *Update Videos* workflow reads the playlists,
commits the new list and redeploys — but only on a schedule, and GitHub runs a
``*/10`` schedule a handful of times a day, so a song uploaded here could take
hours to appear (#321). The workflow already accepts ``workflow_dispatch``, so
after an upload (or a delete) the app asks GitHub to run it now. Nothing about
what the site shows is decided here.

Off unless ``STEMMANAUHAT_DISPATCH_TOKEN`` is set: a fine-grained token with
Actions read and write on that one repository. The service has no ``gh`` login
to borrow. A refresh that fails is a line in the song's log and nothing more —
the upload has already happened and the schedule still catches up.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Callable

DEFAULT_REPO = "eerovil/stemmanauhat"
WORKFLOW = "update-videos.yml"
TIMEOUT = 10


def refresh_stemmanauhat(log: Callable[[str], None]) -> bool:
    """Start the site's video refresh. True when GitHub accepted it."""
    token = (os.environ.get("STEMMANAUHAT_DISPATCH_TOKEN") or "").strip()
    if not token:
        log("The stemmanauhat site refresh is off (no STEMMANAUHAT_DISPATCH_TOKEN); "
            "the site will update on its own schedule.")
        return False
    repo = (os.environ.get("STEMMANAUHAT_REPO") or "").strip() or DEFAULT_REPO
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/workflows/{WORKFLOW}/dispatches",
        data=json.dumps({"ref": "main"}).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read() or b"{}").get("message", "")
        except (ValueError, AttributeError):
            pass
        log(f"Could not refresh the stemmanauhat site: GitHub said {exc.code}"
            f"{' ' + detail if detail else ''}; it will update on its own schedule.")
        return False
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log(f"Could not refresh the stemmanauhat site: {exc}; "
            "it will update on its own schedule.")
        return False
    if status != 204:
        log(f"Could not refresh the stemmanauhat site: GitHub said {status}; "
            "it will update on its own schedule.")
        return False
    log("Asked the stemmanauhat site to refresh — it usually updates within a "
        "couple of minutes.")
    return True
