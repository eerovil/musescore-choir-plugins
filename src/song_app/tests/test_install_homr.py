"""The homr installer keeps its pinned source behind one environment override."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


DEFAULT_SOURCE = (
    "git+https://github.com/eerovil/homr.git@"
    "1ebd5933fac352d48d2e44243723e21b7dd783f7"
)


@pytest.mark.parametrize(
    ("override", "expected"),
    [(None, DEFAULT_SOURCE), ("homr==9.9.9", "homr==9.9.9")],
)
def test_installer_resolves_source(tmp_path: Path, override: str | None, expected: str) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uv_log = tmp_path / "uv.log"
    homr_log = tmp_path / "homr.log"
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$UV_LOG"
if [[ "$1" == "venv" ]]; then
    venv="${@: -1}"
    mkdir -p "$venv/bin"
    printf '#!/usr/bin/env bash\\nprintf "%%s\\\\n" "$*" >> "$HOMR_LOG"\\n' > "$venv/bin/homr"
    chmod +x "$venv/bin/homr"
fi
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "HOMR_VENV": str(tmp_path / "venv"),
            "UV_LOG": str(uv_log),
            "HOMR_LOG": str(homr_log),
        }
    )
    if override is None:
        env.pop("HOMR_SOURCE", None)
    else:
        env["HOMR_SOURCE"] = override

    subprocess.run(["scripts/install-homr.sh"], check=True, env=env, text=True)

    assert f"pip install {expected}" in uv_log.read_text(encoding="utf-8").splitlines()
    assert homr_log.read_text(encoding="utf-8").splitlines() == ["--init"]
