"""`./song.py --check` says what a machine has and lacks (#408).

Each check is handed its tools (`which`, the process runner, the choir reader),
so these run the same on a machine with everything installed and one with
nothing.
"""

import os
import subprocess
import sys

from src.song_app import check


def _ran(stdout="", stderr="", raises=None):
    def run(argv, **_kw):
        if raises:
            raise raises
        return subprocess.CompletedProcess(argv, 0, stdout, stderr)
    return run


def _exe(tmp_path, name="mscore"):
    path = tmp_path / name
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return str(path)


def test_musescore_3_is_found_and_its_version_said(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSESCORE_CLI_PATH", _exe(tmp_path))
    found = check.musescore(run=_ran(stdout="MuseScore3 3.6.2\n"))
    assert found.status == check.OK and "3.6.2" in found.detail


def test_musescore_4_is_refused_by_name(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSESCORE_CLI_PATH", _exe(tmp_path))
    found = check.musescore(run=_ran(stdout="MuseScore4 4.4.2\n"))
    assert found.status == check.MISSING and "MuseScore 4.4.2" in found.detail


def test_no_musescore_is_missing_and_says_how_to_get_one(monkeypatch):
    monkeypatch.setenv("MUSESCORE_CLI_PATH", "")
    found = check.musescore(which=lambda _name: None)
    assert found.status == check.MISSING and "scripts/setup.sh" in found.detail


def test_a_musescore_that_does_not_answer_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSESCORE_CLI_PATH", _exe(tmp_path))
    found = check.musescore(run=_ran(raises=subprocess.TimeoutExpired("mscore", 120)))
    assert found.status == check.MISSING and "did not answer" in found.detail


def test_missing_python_packages_are_named_by_their_pip_name():
    found = check.python_packages(find=lambda name: None if name == "PIL" else object())
    assert found.status == check.MISSING and "pillow" in found.detail


def test_poppler_is_optional_and_says_how_to_install_it():
    assert check.poppler(which=lambda t: "/usr/bin/" + t).status == check.OK
    found = check.poppler(which=lambda _t: None)
    assert found.status == check.OFF and "poppler-utils" in found.detail


def test_publishing_off_names_the_keys(monkeypatch, tmp_path):
    for key in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "STEMMANAUHAT_D1_DATABASE_ID"):
        monkeypatch.delenv(key, raising=False)
    found = check.publishing(str(tmp_path))
    assert found.status == check.OFF and "CLOUDFLARE_API_TOKEN" in found.detail


def test_publishing_on_lists_the_sites_choirs_or_says_why_not(monkeypatch, tmp_path):
    for key, value in (("CLOUDFLARE_ACCOUNT_ID", "acct"), ("CLOUDFLARE_API_TOKEN", "tok"),
                       ("STEMMANAUHAT_D1_DATABASE_ID", "db1")):
        monkeypatch.setenv(key, value)
    found = check.publishing(str(tmp_path), choirs=lambda _c, _d: {
        "choirs": [{"id": "kamari"}, {"id": "public"}], "error": None})
    assert found.status == check.OK and "kamari, public" in found.detail
    found = check.publishing(str(tmp_path), choirs=lambda _c, _d: {
        "choirs": [], "error": "403 no"})
    assert found.status == check.OFF and "403 no" in found.detail


def test_only_a_required_thing_missing_fails_the_check():
    fine = [check.Check("Python packages", check.OK, ""), check.Check("homr", check.OFF, "")]
    assert "Ready" in check.report(fine)
    broken = fine + [check.Check("MuseScore 3", check.MISSING, "not found")]
    assert "Missing: MuseScore 3" in check.report(broken)


def test_env_file_values_are_read_and_set_values_win(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text('# a comment\nCHECK_A="from file"\nexport CHECK_B=b\n'
                                   "CHECK_C='kept'\n")
    monkeypatch.delenv("CHECK_A", raising=False)
    monkeypatch.delenv("CHECK_B", raising=False)
    monkeypatch.setenv("CHECK_C", "already set")
    check.load_env(str(tmp_path))
    assert os.environ["CHECK_A"] == "from file" and os.environ["CHECK_B"] == "b"
    assert os.environ["CHECK_C"] == "already set"


def test_song_py_check_runs_and_exits_by_what_is_missing(tmp_path):
    repo = check.REPO
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLOUDFLARE_", "STEMMANAUHAT_"))}
    env["MUSESCORE_CLI_PATH"] = "/no-musescore-here"
    result = subprocess.run([sys.executable, os.path.join(repo, "song.py"), "--check"],
                            capture_output=True, text=True, env=env, cwd=str(tmp_path),
                            timeout=120)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "MISSING MuseScore 3" in result.stdout
    assert "Missing: MuseScore 3" in result.stdout
