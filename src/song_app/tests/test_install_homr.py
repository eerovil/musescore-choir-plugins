"""The homr installer: one venv, and what it says about itself.

A branch of the fork is deliberately NOT installed — the app runs a local working
copy from source against this venv's dependencies (see test_omr.py). So all this
has to get right is where homr comes from and how the one install is labelled.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


# The [cpu] extra is not decoration: on `main` onnxruntime lives in an extra, so an
# install without it has no inference runtime and fails at the first parse.
DEFAULT_SOURCE = "homr[cpu] @ git+https://github.com/eerovil/homr.git@main"

FAKE_UV = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$UV_LOG"
if [[ "$1" == "venv" ]]; then
    venv="${@: -1}"
    mkdir -p "$venv/bin"
    printf '#!/usr/bin/env bash\\nprintf "%%s\\\\n" "$*" >> "$HOMR_LOG"\\n' > "$venv/bin/homr"
    chmod +x "$venv/bin/homr"
fi
"""


@pytest.fixture()
def install(tmp_path: Path):
    """Run the installer against a fake uv, and hand back what it did."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(FAKE_UV, encoding="utf-8")
    fake_uv.chmod(0o755)
    uv_log, homr_log = tmp_path / "uv.log", tmp_path / "homr.log"

    def run(**overrides: str | None) -> tuple[list[str], list[str]]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{fake_bin}:{env['PATH']}",
                "HOME": str(tmp_path / "home"),
                "UV_LOG": str(uv_log),
                "HOMR_LOG": str(homr_log),
            }
        )
        for key in ("HOMR_SOURCE", "HOMR_VENV"):
            env.pop(key, None)
        for key, value in overrides.items():
            if value is not None:
                env[key] = value
        subprocess.run(["scripts/install-homr.sh"], check=True, env=env, text=True)
        return (uv_log.read_text(encoding="utf-8").splitlines(),
                homr_log.read_text(encoding="utf-8").splitlines())

    run.home = tmp_path / "home"
    return run


@pytest.mark.parametrize(
    ("override", "expected"),
    [(None, DEFAULT_SOURCE), ("homr==9.9.9", "homr==9.9.9")],
)
def test_installer_resolves_source(install, tmp_path: Path,
                                   override: str | None, expected: str) -> None:
    uv_log, homr_log = install(HOMR_SOURCE=override, HOMR_VENV=str(tmp_path / "venv"))

    assert f"pip install {expected}" in uv_log
    assert homr_log == ["--init"]


def test_the_venv_says_what_is_in_it(install) -> None:
    """The app labels the default engine off this file."""
    install()

    base = install.home / ".local/share/musescore-choir-plugins"
    default = (base / "homr-venv" / "homr-engine.txt").read_text().splitlines()
    assert default == [f"source={DEFAULT_SOURCE}", "branch=main"]


def test_an_explicit_source_is_its_own_label(install) -> None:
    """A commit is how an old parse is got back, and "main" would then be a lie."""
    uv_log, _ = install(HOMR_SOURCE="homr==9.9.9")

    assert "pip install homr==9.9.9" in uv_log
    venv = install.home / ".local/share/musescore-choir-plugins/homr-venv"
    assert (venv / "homr-engine.txt").read_text().splitlines() == ["source=homr==9.9.9"]


# What astral's installer would do, minus the download: put a uv where it was told.
FAKE_CURL = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$CURL_LOG"
cat <<'SH'
mkdir -p "$UV_INSTALL_DIR"
cp "$FAKE_UV_SOURCE" "$UV_INSTALL_DIR/uv"
chmod +x "$UV_INSTALL_DIR/uv"
SH
"""


def test_a_host_without_uv_gets_one(tmp_path: Path) -> None:
    """A fresh host has no uv, and that was the step that stopped the install (#249)."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "curl").write_text(FAKE_CURL, encoding="utf-8")
    (fake_bin / "curl").chmod(0o755)
    uv_source = tmp_path / "uv-source"
    uv_source.write_text(FAKE_UV, encoding="utf-8")
    home = tmp_path / "home"
    env = {
        # No uv anywhere on this PATH: only the fake curl and the system tools.
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "HOME": str(home),
        "UV_LOG": str(tmp_path / "uv.log"),
        "HOMR_LOG": str(tmp_path / "homr.log"),
        "CURL_LOG": str(tmp_path / "curl.log"),
        "FAKE_UV_SOURCE": str(uv_source),
    }
    out = subprocess.run(["scripts/install-homr.sh"], check=True, env=env, text=True,
                         capture_output=True).stdout

    assert "uv not found" in out
    assert "astral.sh/uv/install.sh" in (tmp_path / "curl.log").read_text()
    assert (home / ".local/bin/uv").exists()
    assert f"pip install {DEFAULT_SOURCE}" in (tmp_path / "uv.log").read_text()


def test_status_reports_both_commits_and_installs_nothing(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    info = venv / "lib/python3.12/site-packages/homr-0.7.0.dist-info"
    info.mkdir(parents=True)
    (info / "direct_url.json").write_text(
        '{"url": "x", "vcs_info": {"vcs": "git", "commit_id": "' + "a" * 40 + '"}}')
    remote = tmp_path / "remote"
    subprocess.run(["git", "init", "-q", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "-C", str(remote), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    head = subprocess.run(["git", "-C", str(remote), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()
    env = dict(os.environ, HOMR_VENV=str(venv), HOMR_REPO=str(remote),
               PATH="/usr/bin:/bin", HOME=str(tmp_path / "home"))
    out = subprocess.run(["scripts/install-homr.sh", "--status"], check=True, env=env,
                         text=True, capture_output=True).stdout.splitlines()

    assert out == ["installed=" + "a" * 40, f"latest={head}"]
    assert not (tmp_path / "home").exists()
