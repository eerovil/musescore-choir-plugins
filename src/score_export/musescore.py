"""The MuseScore 3 command line: exports, and the part names a score carries."""

from __future__ import annotations

import os
import subprocess
from typing import List

from lxml import etree

# The CLI occasionally wedges (a stuck dialog, another instance); without a bound
# it hangs the whole publish. Long enough for a big score to export.
CLI_TIMEOUT = 600


def musescore_cli() -> str:
    return (os.getenv("MUSESCORE_CLI_PATH") or "musescore3")


def run_musescore(input_path: str, output_path: str, timeout: int = CLI_TIMEOUT) -> str:
    """Convert/export via the MuseScore CLI; returns output_path.

    Whether it worked is decided by `src.musescore_cli`, which also accepts
    MuseScore 4's crash on exit after a complete file.
    """
    from src import musescore_cli as cli_run

    try:
        return cli_run.export(musescore_cli(), input_path, output_path, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"MuseScore CLI did not finish within {timeout}s writing "
            f"{os.path.basename(output_path)}. A stuck MuseScore process will do this; "
            "check for a running mscore and kill it.") from None


def part_name(part: etree._Element, index: int) -> str:
    """What this part is called. Unnamed parts get a positional name.

    One function so the name a caller is given by `part_names` is the same one
    `set_mix` matches on: matching on a bare trackName instead would silently
    leave an unnamed part unboosted in its own practice track.
    """
    return (part.findtext("trackName") or f"Part {index + 1}").strip()


def part_names(root: etree._Element) -> List[str]:
    """Part track names, in score order (top staff first)."""
    return [part_name(p, i) for i, p in enumerate(root.iter("Part"))]
