"""scripts/backup-songs.sh: commit songs/, merge the remote in, push (#406)."""

import os
import subprocess
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


def backup(songs):
    return subprocess.run([str(SCRIPT)], env={**ENV, "SONGS_DIR": str(songs)},
                          capture_output=True, text=True)


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
