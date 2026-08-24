"""Durable clean/render status and recent logs for the song app."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from typing import Dict, Optional

JOBS_FILE = ".jobs.json"
LOG_LIMIT = 100
_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _path(song_dir: str) -> str:
    return os.path.join(song_dir, JOBS_FILE)


def _lock(song_dir: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(os.path.abspath(song_dir), threading.Lock())


def load(song_dir: str) -> Dict:
    try:
        with open(_path(song_dir), encoding="utf-8") as f:
            value = json.load(f)
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(song_dir: str, value: Dict) -> None:
    os.makedirs(song_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".jobs-", suffix=".tmp", dir=song_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, indent=2, ensure_ascii=False)
        os.replace(tmp, _path(song_dir))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def start(song_dir: str, kind: str, source_fingerprint: Optional[str] = None) -> Dict:
    with _lock(song_dir):
        jobs = load(song_dir)
        job = {
            "kind": kind,
            "status": "running",
            "started_at": time.time(),
            "finished_at": None,
            "source_fingerprint": source_fingerprint,
            "error": None,
            "logs": [],
        }
        jobs[kind] = job
        _write(song_dir, jobs)
        return job


def start_if_idle(
    song_dir: str,
    kind: str,
    conflicts: tuple[str, ...],
    source_fingerprint: Optional[str] = None,
) -> Optional[Dict]:
    """Atomically start `kind`, or return None when a conflicting job is running."""
    with _lock(song_dir):
        jobs = load(song_dir)
        if any(jobs.get(other, {}).get("status") == "running" for other in conflicts):
            return None
        job = {
            "kind": kind,
            "status": "running",
            "started_at": time.time(),
            "finished_at": None,
            "source_fingerprint": source_fingerprint,
            "error": None,
            "logs": [],
        }
        jobs[kind] = job
        _write(song_dir, jobs)
        return job


def append(song_dir: str, kind: str, line: str, entry_type: str = "log") -> Dict:
    with _lock(song_dir):
        jobs = load(song_dir)
        job = jobs.setdefault(kind, {
            "kind": kind, "status": "running", "started_at": time.time(), "logs": [],
        })
        logs = job.setdefault("logs", [])
        entry = {"type": entry_type, "line": str(line), "at": time.time()}
        if entry_type == "progress" and logs and logs[-1].get("type") == "progress":
            logs[-1] = entry
        else:
            logs.append(entry)
        job["logs"] = logs[-LOG_LIMIT:]
        _write(song_dir, jobs)
        return job


def finish(song_dir: str, kind: str, *, error: Optional[str] = None) -> Dict:
    with _lock(song_dir):
        jobs = load(song_dir)
        job = jobs.setdefault(kind, {"kind": kind, "logs": []})
        job["status"] = "failed" if error else "succeeded"
        job["error"] = error
        job["finished_at"] = time.time()
        _write(song_dir, jobs)
        return job


def is_running(song_dir: str, kind: str) -> bool:
    return load(song_dir).get(kind, {}).get("status") == "running"


def interrupt_running(song_dir: str) -> None:
    """A server restart cannot leave a job looking live forever."""
    with _lock(song_dir):
        jobs = load(song_dir)
        changed = False
        for job in jobs.values():
            if isinstance(job, dict) and job.get("status") == "running":
                job["status"] = "failed"
                job["error"] = "Server restarted before this job finished."
                job["finished_at"] = time.time()
                changed = True
        if changed:
            _write(song_dir, jobs)
