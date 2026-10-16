"""scripts/backup-songs.sh: commit songs/, merge the remote in, push (#406)."""

import os
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "backup-songs.sh"

ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, check=True,
                          capture_output=True, text=True).stdout.strip()


def _tmpdir(songs):
    """Each test's own temporary folder, where the run's lock lives."""
    tmp = songs.parent / "tmp"
    tmp.mkdir(exist_ok=True)
    return tmp


def backup(songs, *args):
    return subprocess.run([str(SCRIPT), *args],
                          env={**ENV, "SONGS_DIR": str(songs), "TMPDIR": str(_tmpdir(songs))},
                          capture_output=True, text=True)


def lock_path(songs) -> Path:
    """Where the script puts its lock. Refused unless it is a full path inside this
    test's temporary folder, so a script that answers something else (an older
    one, which runs the backup instead) makes nothing anywhere."""
    result = backup(songs, "--lock-path")
    assert result.returncode == 0, result.stderr
    lock = Path(result.stdout.strip())
    assert lock.is_absolute() and lock.parent == _tmpdir(songs), \
        f"--lock-path answered {result.stdout!r}"
    return lock


def hold_lock(songs, pid) -> Path:
    """Take the lock the way the script does, in the name of process `pid`."""
    lock = lock_path(songs)
    lock.mkdir()
    (lock / "pid").write_text(f"{pid}\n")
    return lock


def ended_pid() -> int:
    """The number of a process that has run and finished."""
    done = subprocess.Popen(["true"])
    done.wait()
    return done.pid


@pytest.fixture
def repos(tmp_path):
    """A bare 'origin' and two clones of it: songs/ and somebody else's."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", str(origin), str(seed))
    (seed / ".gitignore").write_text("**/scan/*.png\n")
    (seed / "a.txt").write_text("one\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed")
    git(seed, "push", "-q", "origin", "HEAD:main")
    songs = tmp_path / "songs"
    git(tmp_path, "clone", "-q", str(origin), str(songs))
    return origin, songs, seed


def test_a_change_is_committed_and_pushed(repos):
    origin, songs, _ = repos
    (songs / "new-song").mkdir()
    (songs / "new-song" / "fixes.json").write_text("[]\n")
    (songs / "a.txt").write_text("two\n")
    result = backup(songs)
    assert result.returncode == 0, result.stderr
    assert "committed 2 file(s)" in result.stdout
    assert git(origin, "log", "-1", "--format=%s", "main").startswith("Backup ")
    assert git(origin, "show", "main:new-song/fixes.json") == "[]"
    assert git(songs, "status", "--porcelain") == ""


def test_nothing_changed_makes_no_commit(repos):
    origin, songs, _ = repos
    before = git(origin, "rev-parse", "main")
    result = backup(songs)
    assert result.returncode == 0, result.stderr
    assert "nothing changed" in result.stdout
    assert git(origin, "rev-parse", "main") == before


def test_ignored_files_are_not_committed(repos):
    origin, songs, _ = repos
    scan = songs / "some-song" / "scan"
    scan.mkdir(parents=True)
    (scan / "system-01.png").write_bytes(b"png")
    (scan / "system-01.musicxml").write_text("<x/>\n")
    assert backup(songs).returncode == 0
    tracked = git(origin, "ls-tree", "-r", "--name-only", "main").splitlines()
    assert "some-song/scan/system-01.musicxml" in tracked
    assert "some-song/scan/system-01.png" not in tracked


def test_a_commit_on_the_remote_is_merged_in(repos):
    origin, songs, seed = repos
    (seed / "fixture.txt").write_text("from a pull request\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "fixture")
    git(seed, "push", "-q", "origin", "HEAD:main")
    (songs / "local.txt").write_text("local\n")
    result = backup(songs)
    assert result.returncode == 0, result.stderr
    assert (songs / "fixture.txt").exists()
    tracked = git(origin, "ls-tree", "-r", "--name-only", "main").splitlines()
    assert {"fixture.txt", "local.txt"} <= set(tracked)


def test_a_conflict_fails_and_keeps_the_local_commit(repos):
    origin, songs, seed = repos
    (seed / "a.txt").write_text("theirs\n")
    git(seed, "commit", "-q", "-am", "theirs")
    git(seed, "push", "-q", "origin", "HEAD:main")
    remote_head = git(origin, "rev-parse", "main")
    (songs / "a.txt").write_text("ours\n")
    result = backup(songs)
    assert result.returncode != 0
    assert "merging origin/main failed" in result.stderr
    assert git(origin, "rev-parse", "main") == remote_head
    assert (songs / "a.txt").read_text() == "ours\n"
    assert git(songs, "log", "-1", "--format=%s").startswith("Backup ")
    assert git(songs, "status", "--porcelain") == ""
    assert not (songs / ".git" / "MERGE_HEAD").exists()


# The lock (macOS has no flock) -------------------------------------------


def test_the_lock_is_in_this_machines_temporary_folder_not_in_the_synced_repo(repos):
    """songs/ sits in a synced folder (Nextcloud here); a lock in its git folder
    would travel to other machines."""
    _, songs, _ = repos
    lock = lock_path(songs)
    assert lock.parent == _tmpdir(songs)
    assert songs not in lock.parents


def test_a_run_while_another_holds_the_lock_skips_and_commits_nothing(repos):
    origin, songs, _ = repos
    before = git(origin, "rev-parse", "main")
    (songs / "a.txt").write_text("changed\n")
    hold_lock(songs, os.getpid())
    result = backup(songs)
    assert result.returncode == 0, result.stderr
    assert "another run is in progress" in result.stdout
    assert git(origin, "rev-parse", "main") == before
    assert git(songs, "status", "--porcelain") != ""


def test_a_lock_left_by_a_run_that_ended_is_taken_over(repos):
    origin, songs, _ = repos
    (songs / "a.txt").write_text("changed\n")
    hold_lock(songs, ended_pid())
    result = backup(songs)
    assert result.returncode == 0, result.stderr
    assert "stale lock" in result.stdout
    assert "committed 1 file(s)" in result.stdout
    assert git(origin, "show", "main:a.txt") == "changed"


def test_a_run_leaves_no_lock_behind(repos):
    _, songs, _ = repos
    (songs / "a.txt").write_text("changed\n")
    assert backup(songs).returncode == 0
    assert not lock_path(songs).exists()
    second = backup(songs)
    assert second.returncode == 0, second.stderr
    assert "nothing changed" in second.stdout


def test_the_script_does_not_depend_on_flock():
    code = [line.split("#", 1)[0] for line in SCRIPT.read_text().splitlines()]
    assert not any("flock" in line for line in code)


def test_a_skip_says_which_lock_to_delete(repos):
    _, songs, _ = repos
    lock = hold_lock(songs, os.getpid())
    result = backup(songs)
    assert result.returncode == 0, result.stderr
    assert f"delete {lock}" in result.stdout


def test_a_lock_older_than_the_timers_limit_is_taken_over(repos):
    """A live number in an old lock is another process given the dead run's number:
    the timer stops a run after 30 minutes."""
    origin, songs, _ = repos
    (songs / "a.txt").write_text("changed\n")
    lock = hold_lock(songs, os.getpid())
    old = time.time() - 31 * 60
    os.utime(lock, (old, old))
    result = backup(songs)
    assert result.returncode == 0, result.stderr
    assert "stale lock" in result.stdout
    assert git(origin, "show", "main:a.txt") == "changed"


def test_a_stale_lock_replaced_before_it_is_cleared_is_left_alone(repos, tmp_path):
    """Two runs find the same dead number; the other clears it and takes a fresh
    lock between this run's check and its clearing. This run must not remove that
    fresh lock, and must not run."""
    origin, songs, _ = repos
    before = git(origin, "rev-parse", "main")
    (songs / "a.txt").write_text("changed\n")
    lock = hold_lock(songs, ended_pid())
    # `ps` is the check; this one answers "ended" after the other run has
    # replaced the lock with its own.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ps = bin_dir / "ps"
    ps.write_text(f"#!/bin/sh\nrm -rf '{lock}'\nmkdir '{lock}'\n"
                  f"echo {os.getpid()} > '{lock}/pid'\nexit 1\n")
    ps.chmod(0o755)
    result = subprocess.run(
        [str(SCRIPT)], capture_output=True, text=True,
        env={**ENV, "SONGS_DIR": str(songs), "TMPDIR": str(_tmpdir(songs)),
             "PATH": f"{bin_dir}:{ENV['PATH']}"})
    assert result.returncode == 0, result.stderr
    assert "another run is in progress" in result.stdout
    assert (lock / "pid").read_text().strip() == str(os.getpid())
    assert git(origin, "rev-parse", "main") == before
    assert not list(_tmpdir(songs).glob("*.stale.*"))
