"""Installing homr from the app (#249): when it may run, and what it says.

The installer itself is pinned in test_install_homr.py. A stub stands in for it
here, because what this module owns is around the script: the status the Library
box draws, one install at a time, never underneath a running song job, scans
refused while it runs, and a lost heavy slot stopping the script.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.song_app import heavy_slot, homr_install, job_state, omr, server, state

OLD, NEW = "a" * 40, "b" * 40


@pytest.fixture
def host(tmp_path: Path, monkeypatch):
    """A songs folder, a fork whose main is NEW, and homr installed at OLD."""
    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    monkeypatch.delenv("AGENTDECK_API_URL", raising=False)
    monkeypatch.delenv("AGENTDECK_URL", raising=False)
    installed = {"commit": OLD}
    monkeypatch.setattr(omr, "default_engine", lambda: omr.Engine(
        key="default", label=f"installed: main @ {installed['commit'][:7]}",
        command=["homr"], default=True, commit=installed["commit"])
        if installed["commit"] else None)
    remote = {"commit": NEW}
    monkeypatch.setattr(homr_install, "_ls_remote", lambda: remote["commit"])
    monkeypatch.setitem(homr_install._latest, "commit", None)
    monkeypatch.setitem(homr_install._latest, "at", 0.0)
    homr_install._log.clear()
    homr_install._result.clear()

    def script(body: str) -> None:
        path = tmp_path / "install-homr.sh"
        path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
        path.chmod(0o755)
        monkeypatch.setattr(homr_install, "SCRIPT", str(path))

    script("echo installing\n")
    yield type("Host", (), {"installed": installed, "remote": remote,
                            "script": staticmethod(script), "tmp": tmp_path})


def _inline(work):
    work()


def test_status_says_a_newer_homr_is_on_github(host) -> None:
    st = homr_install.status()
    assert (st["installed"], st["latest"], st["up_to_date"]) == (OLD, NEW, False)
    assert st["label"] == "main @ aaaaaaa"
    assert st["running"] is False


def test_status_is_up_to_date_when_the_commits_match(host) -> None:
    host.remote["commit"] = OLD
    assert homr_install.status()["up_to_date"] is True


def test_unreachable_github_is_unknown_not_up_to_date(host) -> None:
    host.remote["commit"] = None
    st = homr_install.status()
    assert st["latest"] is None and st["up_to_date"] is None


def test_not_installed(host) -> None:
    host.installed["commit"] = None
    st = homr_install.status()
    assert st["installed"] is None and st["label"] is None and st["up_to_date"] is None


def test_the_fork_commit_is_cached_until_refreshed(host) -> None:
    homr_install.status()
    host.remote["commit"] = OLD
    assert homr_install.status()["latest"] == NEW
    assert homr_install.status(refresh=True)["latest"] == OLD


def test_github_going_quiet_after_the_cache_expires_is_unknown(host) -> None:
    """An expired cache must not keep showing the commit from before GitHub went away."""
    assert homr_install.status()["latest"] == NEW
    homr_install._latest["at"] -= homr_install.LATEST_TTL_S + 1
    host.remote["commit"] = None

    st = homr_install.status()
    assert st["latest"] is None and st["up_to_date"] is None


def test_an_install_streams_the_log_and_records_success(host) -> None:
    host.script('echo "step one"\necho "step two" >&2\necho "venv=$HOMR_VENV"\n'
                'echo "source=${HOMR_SOURCE:-unset}"\n')
    os.environ["HOMR_SOURCE"] = "homr==1.0"
    try:
        host.installed["commit"] = NEW  # what the script would leave behind
        homr_install.start(_inline)
    finally:
        os.environ.pop("HOMR_SOURCE")

    st = homr_install.status()
    assert "step one" in st["log"] and "step two" in st["log"]
    assert f"venv={homr_install._venv()}" in st["log"]
    # The button always installs main; an explicit source belongs to a shell.
    assert "source=unset" in st["log"]
    assert st["result"]["ok"] is True and st["result"]["installed"] == NEW
    assert st["running"] is False
    assert not os.path.exists(homr_install._lock_path())


def test_a_failed_install_is_recorded_with_its_reason(host) -> None:
    host.script("echo 'no network'\nexit 3\n")
    homr_install.start(_inline)

    st = homr_install.status()
    assert st["result"]["ok"] is False
    assert "exit 3" in st["result"]["error"]
    assert "no network" in st["log"]
    assert not os.path.exists(homr_install._lock_path())


def test_a_second_install_is_refused_while_one_runs(host) -> None:
    host.script(f"while [ ! -e {host.tmp}/go ]; do sleep 0.05; done\n")
    thread = None

    def background(work):
        nonlocal thread
        thread = threading.Thread(target=work)
        thread.start()

    homr_install.start(background)
    try:
        assert homr_install.busy()
        with pytest.raises(homr_install.Refused, match="already"):
            homr_install.start(_inline)
    finally:
        (host.tmp / "go").touch()
        thread.join(timeout=10)
    assert not homr_install.busy()


def test_a_lock_left_by_a_dead_server_is_stale(host) -> None:
    Path(homr_install._lock_path()).write_text("999999999")
    assert homr_install.busy() is False
    assert not os.path.exists(homr_install._lock_path())


def test_refused_while_a_song_job_runs(host) -> None:
    song = state.create("Talviuni", per_system=False)
    job_state.start(song.dir, "scan")
    with pytest.raises(homr_install.Refused, match="Talviuni"):
        homr_install.start(_inline)
    assert not os.path.exists(homr_install._lock_path())


def test_a_lost_slot_stops_the_script(host, monkeypatch) -> None:
    host.script("for i in $(seq 1 200); do echo line $i; sleep 0.05; done\n"
                f"touch {host.tmp}/finished\n")
    slot = heavy_slot.Slot("lease")

    class Held:
        def __enter__(self):
            return slot

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(heavy_slot, "heavy_slot", lambda *a, **k: Held())

    def lose_soon(work):
        threading.Timer(0.3, slot._lose).start()
        work()

    homr_install.start(lose_soon)
    st = homr_install.status()
    assert st["result"]["ok"] is False
    assert st["result"]["error"].startswith("Stopped")
    time.sleep(0.3)
    assert not (host.tmp / "finished").exists()


def test_the_routes(host) -> None:
    client = TestClient(server.app)
    st = client.get("/api/homr/install").json()
    assert st["up_to_date"] is False

    song = state.create("Talviuni", per_system=False)
    job_state.start(song.dir, "render")
    refused = client.post("/api/homr/install")
    assert refused.status_code == 409 and "Talviuni" in refused.json()["detail"]
    job_state.finish(song.dir, "render")

    assert client.post("/api/homr/install").json() == {"started": True}
    deadline = time.time() + 10
    while client.get("/api/homr/install").json()["running"] and time.time() < deadline:
        time.sleep(0.05)
    assert client.get("/api/homr/install").json()["result"]["ok"] is True


def test_scanning_and_asking_homr_wait_for_an_install(host, monkeypatch) -> None:
    from src.song_app import pdf_systems, scan

    song = state.create("Talviuni", per_system=False)
    song.data.setdefault("sources", {})["pdf"] = "page.pdf"
    song.save()
    Path(song.path("page.pdf")).write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(pdf_systems, "load_bounds", lambda _d: [object()])
    monkeypatch.setattr(scan, "pages_without_bands", lambda _s: [])
    Path(homr_install._lock_path()).write_text(str(os.getpid()))

    client = TestClient(server.app)
    for path, body in ((f"/api/songs/{song.slug}/scan", {}),
                       (f"/api/songs/{song.slug}/find-systems", {"method": "homr"})):
        r = client.post(path, json=body)
        assert r.status_code == 409, (path, r.text)
        assert "being updated" in r.json()["detail"]
    assert not job_state.is_running(song.dir, "scan")
