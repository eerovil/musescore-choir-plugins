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
