"""`./song.py --check`: what this machine can do, one line each (#408).

A new machine used to find out what it was missing one failure at a time: a
preview that would not render, a crop that never appeared, a Publish button that
did nothing. This asks each thing the app depends on directly and says what it
found. It imports nothing outside the standard library at the top, because the
first thing it may have to say is that the Python packages are not installed.

Two things are required — the Python packages and MuseScore 3 — and only those
make it fail. poppler, homr and publishing are each optional: the app runs
without them and says so where they are used.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import name -> the name in pip-requirements.txt.
PACKAGES = (("lxml", "lxml"), ("dotenv", "dotenv"), ("PIL", "pillow"),
            ("fastapi", "fastapi"), ("uvicorn", "uvicorn"),
            ("python_multipart", "python-multipart"), ("verovio", "verovio"),
            ("mido", "mido"), ("numpy", "numpy"))

OK, MISSING, OFF = "ok", "MISSING", "off"


@dataclass
class Check:
    name: str
    status: str            # OK, MISSING (required and absent), OFF (optional and absent)
    detail: str


Runner = Callable[..., subprocess.CompletedProcess]


def load_env(repo: str = REPO) -> None:
    """Read `.env`, else `.env.default`, the way the server does; set values win."""
    path = os.path.join(repo, ".env")
    if not os.path.exists(path):
        path = os.path.join(repo, ".env.default")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key, value)


def python_packages(find=importlib.util.find_spec) -> Check:
    missing = [pip for module, pip in PACKAGES if find(module) is None]
    if missing:
        return Check("Python packages", MISSING,
                     f"not installed: {', '.join(missing)} — run scripts/setup.sh, or "
                     ".venv/bin/pip install -r pip-requirements.txt")
    return Check("Python packages", OK, "all of pip-requirements.txt")


def musescore(run: Runner = subprocess.run, which=shutil.which) -> Check:
    cli = os.getenv("MUSESCORE_CLI_PATH") or "musescore3"
    path = cli if os.sep in cli else which(cli)
    if not path or not os.access(path, os.X_OK):
        return Check("MuseScore 3", MISSING,
                     f"{cli!r} not found — run scripts/setup.sh, or set MUSESCORE_CLI_PATH "
                     "in .env to the MuseScore 3 program")
    try:
        result = run([path, "--version"], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Check("MuseScore 3", MISSING, f"{path} did not answer --version: {exc}")
    said = f"{result.stdout}\n{result.stderr}"
    found = re.search(r"MuseScore\s*(\d)\S*\s+(\d+(?:\.\d+)+)", said) \
        or re.search(r"\b(\d+)\.\d+\.\d+", said)
    if not found:
        return Check("MuseScore 3", MISSING, f"{path} --version said nothing recognisable")
    major = found.group(1)
    version = found.group(found.lastindex) if found.lastindex > 1 else found.group(0)
    if major != "3":
        return Check("MuseScore 3", MISSING,
                     f"{path} is MuseScore {version}; the app needs MuseScore 3 "
                     "(MuseScore 4's command line is different)")
    return Check("MuseScore 3", OK, f"{path} ({version})")


def poppler(which=shutil.which) -> Check:
    missing = [tool for tool in ("pdftoppm", "pdfinfo") if not which(tool)]
    if missing:
        return Check("poppler", OFF,
                     f"{' and '.join(missing)} not found: no page crops, so no scanning "
                     "and no page pictures — install poppler (dnf install poppler-utils, "
                     "apt install poppler-utils, brew install poppler)")
    return Check("poppler", OK, "pdftoppm and pdfinfo")


def homr() -> Check:
    try:
        from src.song_app import omr
    except ImportError as exc:
        return Check("homr", OFF, f"not checked: {exc}")
    if omr.homr_available():
        return Check("homr", OK, omr.homr_binary())
    return Check("homr", OFF, "not installed: scanned PDFs cannot be read — run "
                 "scripts/install-homr.sh, or press Install homr on a song's Scan stage")


def songs_folder(repo: str = REPO) -> Check:
    path = os.path.join(repo, "songs")
    where = path if os.path.exists(path) else repo
    if not os.access(where, os.W_OK):
        return Check("songs/", MISSING, f"{where} is not writable")
    if not os.path.exists(path):
        return Check("songs/", OK, f"{path} (made on first start)")
    songs = sum(os.path.isfile(os.path.join(path, entry, ".song.json"))
                for entry in os.listdir(path))
    return Check("songs/", OK, f"{path}, {songs} song(s)")


def publishing(repo: str = REPO, choirs: Optional[Callable] = None) -> Check:
    try:
        from src.song_app import publish
    except ImportError as exc:
        return Check("Publish", OFF, f"not checked: {exc}")
    config = publish.Config.from_env()
    if config is None:
        return Check("Publish", OFF, "off: set CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN "
                     "and STEMMANAUHAT_D1_DATABASE_ID in .env (see SETUP.md)")
    reply = (choirs or publish.choirs)(config, os.path.join(repo, "songs"))
    if reply["error"]:
        return Check("Publish", OFF, f"the site did not answer: {reply['error']}")
    names = ", ".join(c["id"] for c in reply["choirs"]) or "none yet"
    return Check("Publish", OK, f"bucket {config.bucket}, choirs: {names}")


def run_all() -> List[Check]:
    load_env()
    checks = [python_packages(), musescore(), poppler(), songs_folder()]
    if checks[0].status == OK:
        checks += [homr(), publishing()]
    else:
        checks += [Check("homr", OFF, "not checked: Python packages missing"),
                   Check("Publish", OFF, "not checked: Python packages missing")]
    return checks


def report(checks: List[Check]) -> str:
    width = max(len(c.name) for c in checks)
    lines = [f"  {c.status:<7} {c.name:<{width}}  {c.detail}" for c in checks]
    bad = [c.name for c in checks if c.status == MISSING]
    lines.append("")
    lines.append(f"Missing: {', '.join(bad)}. The app will not work until these are fixed."
                 if bad else "Ready: ./song.py starts the app.")
    return "\n".join(lines)


def main() -> int:
    checks = run_all()
    print(report(checks))
    return 1 if any(c.status == MISSING for c in checks) else 0
