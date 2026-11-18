"""Singing a song from the app (#510): the Publish panel's Sing and publish and the
Pronunciation list's Try.

The app does not sing in its own process. A run is `sing_song.py` in a transient systemd user
unit (`sing-<slug>`, a trial `sing-<slug>-try`), so it outlives the app's restarts, and it runs
off a **snapshot** of the checkout's HEAD under /var/tmp/sing-code/<sha>/, so a merge that
rewrites the live checkout mid-run does not change the code singing. A REMOTE_RUNNER inside the
checkout is pointed into the snapshot too, since the runner sends its own copy of sing_ufr.py.

The app writes nothing in songs/: its files are under progress.state_dir(slug) (`app.json`,
`run.log`, `try/<id>/`, `try/current.json`). Which run a `.sing-run.json` record belongs to is
its `run_id`, passed to the unit as SING_RUN_ID. One full run of a song at a time is the song
lock sing_song.py takes (progress.run_lock); this module only checks it to answer early.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import shutil
import subprocess
import time
from typing import Dict, List, Optional

import dotenv

from src.sing_song import progress
from . import state

CHECKOUT = state.SCRIPT_DIR
CODE_DIR = os.environ.get("SING_CODE_DIR") or "/var/tmp/sing-code"
SNAPSHOT_TTL = 24 * 3600   # a snapshot not started from for a day has no unit in it (3 h limit)
RUNTIME_MAX = "3h"
KEEP_TRIES = 3
TAIL = 20
CARD_LINES = ("waiting for the graphics card", "got the graphics card")


class StartError(Exception):
    """systemd-run or the snapshot failed; the message is what it said (a 502)."""


def unit(slug: str, trial: bool = False) -> str:
    return f"sing-{slug}-try" if trial else f"sing-{slug}"


def _run(argv: List[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=kw.pop("timeout", 60), **kw)


def active(name: str) -> bool:
    """True while the unit runs (activating or active)."""
    try:
        r = _run(["systemctl", "--user", "is-active", name], timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.stdout.strip() in ("active", "activating", "reloading", "deactivating")


def stop(name: str) -> None:
    """Stop the unit: SIGTERM, which a run records as cancelled."""
    r = _run(["systemctl", "--user", "stop", name], timeout=120)
    if r.returncode:
        raise StartError((r.stderr or r.stdout).strip() or f"systemctl stop {name} failed")


def tail(path: str, n: int = TAIL) -> List[str]:
    try:
        with open(path, errors="replace") as f:
            return f.read().splitlines()[-n:]
    except OSError:
        return []


def _read_json(path: str) -> Optional[Dict]:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path: str, data: Dict) -> None:
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


# ---- the pinned code -------------------------------------------------------------------------

def head() -> str:
    r = _run(["git", "-C", CHECKOUT, "rev-parse", "HEAD"])
    if r.returncode:
        raise StartError((r.stderr or r.stdout).strip() or "git rev-parse HEAD failed")
    return r.stdout.strip()


def _snapshot(sha: str) -> str:
    """/var/tmp/sing-code/<sha>/ (made, or reused and touched); old ones removed. Called under
    the code lock."""
    path = os.path.join(CODE_DIR, sha)
    if os.path.isdir(path):
        os.utime(path)
    else:
        tmp = f"{path}.tmp-{os.getpid()}"
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        try:
            archive = subprocess.Popen(["git", "-C", CHECKOUT, "archive", sha],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            untar = subprocess.run(["tar", "-x", "-C", tmp], stdin=archive.stdout,
                                   capture_output=True, timeout=300)
            archive.stdout.close()
            err = archive.stderr.read().decode(errors="replace")
            if archive.wait(timeout=60) or untar.returncode:
                raise StartError((err or untar.stderr.decode(errors="replace")).strip()
                                 or "git archive failed")
            for name, target in (("songs", state.SONGS_DIR), (".env", os.path.join(CHECKOUT, ".env"))):
                if os.path.exists(target):
                    os.symlink(os.path.realpath(target), os.path.join(tmp, name))
            os.rename(tmp, path)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    now = time.time()
    for name in os.listdir(CODE_DIR):
        other = os.path.join(CODE_DIR, name)
        if name == sha or name.startswith(".") or not os.path.isdir(other):
            continue
        try:
            if now - os.path.getmtime(other) > SNAPSHOT_TTL:
                shutil.rmtree(other, ignore_errors=True)
        except OSError:
            pass
    return path


def _remote_runner(snapshot: str) -> Optional[str]:
    """REMOTE_RUNNER pointed into the snapshot when it names a file in the checkout, else None
    (left as .env says: not this repo's code)."""
    if "REMOTE_RUNNER" in os.environ:   # set, even empty, wins over .env (load_dotenv's rule)
        runner = os.environ["REMOTE_RUNNER"]
    else:
        runner = dotenv.dotenv_values(os.path.join(CHECKOUT, ".env")).get("REMOTE_RUNNER") or ""
    runner = runner.strip()
    if not runner:
        return None
    real, root = os.path.realpath(runner), os.path.realpath(CHECKOUT)
    if not real.startswith(root + os.sep):
        return None
    return os.path.join(snapshot, os.path.relpath(real, root))


def _desktop_env() -> Dict[str, str]:
    """DISPLAY and XAUTHORITY from the user manager: MuseScore needs a screen even to export."""
    try:
        r = _run(["systemctl", "--user", "show-environment"], timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    out = {}
    for line in r.stdout.splitlines():
        key, _, value = line.partition("=")
        if key in ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY") and value:
            out[key] = value
    return out


def _start(name: str, args: List[str], log: str, env: Dict[str, str]) -> str:
    """Start sing_song.py in a transient unit off the pinned snapshot; the commit it runs."""
    os.makedirs(CODE_DIR, exist_ok=True)
    lock = os.open(os.path.join(CODE_DIR, ".lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)   # held until systemd-run returns: no removal meanwhile
        sha = head()
        snapshot = _snapshot(sha)
        env = {**_desktop_env(), **env}
        runner = _remote_runner(snapshot)
        if runner:
            env["REMOTE_RUNNER"] = runner
        python = os.path.join(CHECKOUT, ".venv", "bin", "python")
        argv = ["systemd-run", "--user", "--unit", name, "--collect",
                "-p", f"RuntimeMaxSec={RUNTIME_MAX}",
                "-p", f"StandardOutput=truncate:{log}", "-p", f"StandardError=append:{log}",
                f"--working-directory={snapshot}"]
        argv += [f"--setenv={k}={v}" for k, v in sorted(env.items())]
        argv += ["--", python, "-u", os.path.join(snapshot, "sing_song.py"), *args]
        try:
            r = _run(argv, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StartError(f"systemd-run: {exc}")
        if r.returncode:
            raise StartError((r.stderr or r.stdout).strip() or "systemd-run failed")
        return sha
    finally:
        os.close(lock)


# ---- a full run: Sing and publish --------------------------------------------------------------

def busy_reason(slug: str) -> Optional[str]:
    """Why a full run cannot start now, or None."""
    if active(unit(slug)):
        return "This song is already singing."
    if progress.run_locked(slug):
        return "Another sing_song run is singing this song."
    return None


def start(slug: str, choir: str) -> Dict:
    folder = progress.state_dir(slug)
    os.makedirs(folder, exist_ok=True)
    run_id = f"{slug}-{int(time.time() * 1000)}-{secrets.token_hex(3)}"
    log = os.path.join(folder, "run.log")
    sha = _start(unit(slug), [slug, "--publish", choir], log, {"SING_RUN_ID": run_id})
    record = {"unit": unit(slug), "run_id": run_id, "started": time.time(), "commit": sha, "choir": choir}
    _write_json(os.path.join(folder, "app.json"), record)
    return record


def status(slug: str, song_dir: str) -> Dict:
    """What the app's last run is doing: {state, run?, log?, started, choir}. `state` is
    `none` (no app run), `starting`, a progress state (waiting, bundle, singing, publishing,
    done, failed) or `failed` with the log's tail."""
    folder = progress.state_dir(slug)
    app = _read_json(os.path.join(folder, "app.json"))
    if not app:
        return {"state": "none"}
    record = progress.read(song_dir)
    mine = record if record and record.get("run_id") == app.get("run_id") else None
    is_active = active(app.get("unit") or unit(slug))
    out = {"started": app.get("started"), "choir": app.get("choir"), "commit": app.get("commit")}
    if mine and (is_active or mine.get("state") in ("done", "failed")):
        return {**out, "state": mine.get("state"), "run": mine}
    if is_active:
        return {**out, "state": "starting"}
    return {**out, "state": "failed", "run": mine,
            "log": tail(os.path.join(folder, "run.log"))}


# ---- a trial: Try ------------------------------------------------------------------------------

def _try_root(slug: str) -> str:
    return os.path.join(progress.state_dir(slug), "try")


def start_try(slug: str, part: str, first: int, last: int) -> Dict:
    root = _try_root(slug)
    os.makedirs(root, exist_ok=True)
    trial_id = int(time.time() * 1000)
    while True:   # two presses in one millisecond get two folders
        folder = os.path.join(root, str(trial_id))
        try:
            os.makedirs(folder)
            break
        except FileExistsError:
            trial_id += 1
    trial_id = str(trial_id)
    try:
        _start(unit(slug, True), [slug, "--try", f"{part}:{first}-{last}", "--out", folder],
               os.path.join(folder, "log"), {})
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    record = {"id": trial_id, "part": part, "first": first, "last": last, "started": time.time()}
    _write_json(os.path.join(root, "current.json"), record)
    ids = sorted((n for n in os.listdir(root) if n.isdigit()), key=int)
    for old in ids[:-KEEP_TRIES]:
        shutil.rmtree(os.path.join(root, old), ignore_errors=True)
    return record


TRIAL_SAID = re.compile(r"\(([\d.]+) s of music, sung in ([\d.]+) s\)")


def try_status(slug: str) -> Dict:
    """The last trial: {state: none|waiting|singing|done|failed, part, first, last, mp3?, music?,
    took?, log?}."""
    root = _try_root(slug)
    current = _read_json(os.path.join(root, "current.json"))
    if not current:
        return {"state": "none"}
    folder = os.path.join(root, str(current["id"]))
    log = os.path.join(folder, "log")
    lines = tail(log, 10 ** 6)
    out = {k: current.get(k) for k in ("id", "part", "first", "last", "started")}
    if active(unit(slug, True)):
        card = [l for l in lines if any(c in l for c in CARD_LINES)]
        waiting = bool(card) and CARD_LINES[0] in card[-1]
        return {**out, "state": "waiting" if waiting else "singing"}
    mp3s = sorted(n for n in os.listdir(os.path.join(folder, "try"))
                  if n.endswith(".mp3")) if os.path.isdir(os.path.join(folder, "try")) else []
    if mp3s:
        said = next((TRIAL_SAID.search(l) for l in reversed(lines) if TRIAL_SAID.search(l)), None)
        return {**out, "state": "done", "mp3": mp3s[-1],
                "music": float(said.group(1)) if said else None,
                "took": float(said.group(2)) if said else None}
    return {**out, "state": "failed", "log": lines[-TAIL:]}


MP3_NAME = re.compile(r"[\w.-]+\.mp3")


def try_mp3(slug: str, name: str) -> Optional[str]:
    """The current trial's mp3 by its file name, or None."""
    if not MP3_NAME.fullmatch(name) or name.startswith("."):
        return None
    current = _read_json(os.path.join(_try_root(slug), "current.json"))
    if not current:
        return None
    path = os.path.join(_try_root(slug), str(current["id"]), "try", name)
    return path if os.path.isfile(path) else None
