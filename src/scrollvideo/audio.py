"""Per-voice audio, straight from the MuseScore CLI — no GUI, no AppleScript.

Each practice track wants one voice louder than the rest. MuseScore's mixer
volume is MIDI controller 7 on a Part's ``<Channel>``, and that lives in the
.mscx itself, so a mix is: copy the score, set the volumes, ask the CLI to
render it. This replaces the `export.qml` + AppleScript round-trip.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import wave
from typing import List, Optional

from lxml import etree

VOLUME_CTRL = "7"
# The CLI occasionally wedges (a stuck dialog, another instance); without a bound
# it hangs the whole render. Long enough for a big score to export audio.
CLI_TIMEOUT = 600
FOCUS_VOLUME = 127
BACKGROUND_VOLUME = 36
AUDIO_CACHE_VERSION = 1


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


def set_mix(root: etree._Element, focus: Optional[str],
            focus_volume: int = FOCUS_VOLUME,
            background_volume: int = BACKGROUND_VOLUME) -> etree._Element:
    """Set channel volumes so `focus` stands out. focus=None -> an even mix."""
    for index, part in enumerate(root.iter("Part")):
        name = part_name(part, index)
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


def _valid_wav(path: str) -> bool:
    """A cache entry is reusable only after a complete, readable WAV was written."""
    try:
        with wave.open(path, "rb") as wav:
            return wav.getnchannels() > 0 and wav.getframerate() > 0 and wav.getnframes() > 0
    except (FileNotFoundError, EOFError, wave.Error):
        return False


def musescore_identity() -> list:
    """The configured renderer binary, for caches whose output depends on it."""
    configured = musescore_cli()
    executable = shutil.which(configured) or configured
    try:
        stat = os.stat(executable)
        return [os.path.realpath(executable), stat.st_size, stat.st_mtime_ns]
    except FileNotFoundError:
        return [executable]


def _cache_key(mscx_path: str, focus: Optional[str], **volumes) -> str:
    with open(mscx_path, "rb") as source:
        score_digest = hashlib.sha256(source.read()).hexdigest()
    payload = {
        "version": AUDIO_CACHE_VERSION,
        "score": score_digest,
        "focus": focus,
        "focus_volume": volumes.get("focus_volume", FOCUS_VOLUME),
        "background_volume": volumes.get("background_volume", BACKGROUND_VOLUME),
        "musescore": musescore_identity(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def render_mix_cached(mscx_path: str, focus: Optional[str], cache_dir: str,
                      **volumes) -> tuple[str, bool]:
    """Return (WAV path, reused), rendering atomically on a cache miss."""
    os.makedirs(cache_dir, exist_ok=True)
    cached = os.path.join(cache_dir, f"{_cache_key(mscx_path, focus, **volumes)}.wav")
    if _valid_wav(cached):
        return cached, True

    handle = tempfile.NamedTemporaryFile(dir=cache_dir, suffix=".wav", delete=False)
    pending = handle.name
    handle.close()
    os.unlink(pending)
    try:
        render_mix(mscx_path, focus, pending, **volumes)
        if not _valid_wav(pending):
            raise RuntimeError("MuseScore wrote an invalid WAV audio mix")
        os.replace(pending, cached)
        return cached, False
    finally:
        if os.path.exists(pending):
            os.unlink(pending)


def prune_mix_cache(cache_dir: str, keep: set[str]) -> None:
    """Keep only the current build's complete WAVs in the dedicated cache."""
    if not os.path.isdir(cache_dir):
        return
    keep = {os.path.abspath(path) for path in keep}
    for name in os.listdir(cache_dir):
        path = os.path.abspath(os.path.join(cache_dir, name))
        if name.endswith(".wav") and path not in keep:
            os.unlink(path)
