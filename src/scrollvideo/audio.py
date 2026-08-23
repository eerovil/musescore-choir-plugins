"""Per-voice audio, straight from the MuseScore CLI — no GUI, no AppleScript.

Each practice track wants one voice louder than the rest. MuseScore's mixer
volume is MIDI controller 7 on a Part's ``<Channel>``, and that lives in the
.mscx itself, so a mix is: copy the score, set the volumes, ask the CLI to
render it. This replaces the `export.qml` + AppleScript round-trip.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from typing import List, Optional

from lxml import etree

VOLUME_CTRL = "7"
# The CLI occasionally wedges (a stuck dialog, another instance); without a bound
# it hangs the whole render. Long enough for a big score to export audio.
CLI_TIMEOUT = 600
FOCUS_VOLUME = 127
BACKGROUND_VOLUME = 36


def musescore_cli() -> str:
    return os.getenv("MUSESCORE_CLI_PATH", "musescore3")


def run_musescore(input_path: str, output_path: str, timeout: int = CLI_TIMEOUT) -> str:
    """Convert/export via the MuseScore CLI; returns output_path."""
    try:
        result = subprocess.run([musescore_cli(), input_path, "-o", output_path],
                                capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"MuseScore CLI did not finish within {timeout}s writing "
            f"{os.path.basename(output_path)}. A stuck MuseScore process will do this; "
            "check for a running mscore and kill it.") from None
    if result.returncode != 0 or not os.path.exists(output_path):
        raise RuntimeError(
            f"MuseScore CLI failed writing {os.path.basename(output_path)}. "
            "Check MUSESCORE_CLI_PATH.\n" + (result.stderr or result.stdout or ""))
    return output_path


def part_names(root: etree._Element) -> List[str]:
    """Part track names, in score order (top staff first)."""
    return [(p.findtext("trackName") or f"Part {i + 1}").strip()
            for i, p in enumerate(root.iter("Part"))]


def set_mix(root: etree._Element, focus: Optional[str],
            focus_volume: int = FOCUS_VOLUME,
            background_volume: int = BACKGROUND_VOLUME) -> etree._Element:
    """Set channel volumes so `focus` stands out. focus=None -> an even mix."""
    for part in root.iter("Part"):
        name = (part.findtext("trackName") or "").strip()
        for channel in part.iter("Channel"):
            for existing in channel.findall("controller"):
                if existing.get("ctrl") == VOLUME_CTRL:
                    channel.remove(existing)
            volume = focus_volume if (focus is None or name == focus) else background_volume
            controller = etree.Element("controller")
            controller.set("ctrl", VOLUME_CTRL)
            controller.set("value", str(volume))
            channel.insert(0, controller)
    return root


def render_mix(mscx_path: str, focus: Optional[str], out_path: str, **volumes) -> str:
    """Render `mscx_path` to audio (by out_path's extension) with `focus` boosted."""
    tree = etree.parse(mscx_path)
    set_mix(tree.getroot(), focus, **volumes)
    tmp = tempfile.NamedTemporaryFile(suffix=".mscx", delete=False)
    try:
        tree.write(tmp.name, encoding="UTF-8", xml_declaration=True)
        tmp.close()
        return run_musescore(tmp.name, out_path)
    finally:
        os.unlink(tmp.name)
