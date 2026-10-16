"""Run the MuseScore command line, and decide whether it worked.

Every place this project asks MuseScore for a file used to decide for itself, and
they all decided the same way: exit code 0 and the file is there, otherwise the
step failed. MuseScore 4 breaks that rule without breaking the work. Measured on
MuseScore Studio 4.7.5, macOS 26 (October 2026): the export is written, complete
and usable, and the process then dies while shutting down --

    libc++abi: terminating due to uncaught exception of type
    std::__1::system_error: mutex lock failed: Invalid argument

-- which Python reports as return code -6 (SIGABRT) and a shell as 134. It is not
occasional: in one test run it hit nearly every export. Treated as a failure, it
fails Record, the previews and the conversions, all with their files sitting on
disk.

So the decision lives here, once, and it is deliberately narrow:

* Exit 0 and the file exists: success. Unchanged.
* **That** crash -- an abort, carrying that message -- after a file that was
  **written during this run** and is **complete for its kind**: success, and it is
  said out loud. A step that quietly succeeded past a crash is how a real failure
  gets missed later.
* Anything else is failure: the crash with no file, with a file cut short, or with
  only an older file left where the new one should be; an abort without that
  message; any other non-zero exit, file or no file.

"Written during this run" is not a nicety. A leftover file from an earlier run is
complete and well-formed, and accepting it would hand back the previous result as
if it were the new one -- `pipeline.scan_system_render` records exactly that
happening with an old picture. The file's size and modification time are noted
before MuseScore starts and compared afterwards.
"""

from __future__ import annotations

import logging
import os
import struct
import subprocess
import zipfile
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Sequence, Tuple

from lxml import etree

logger = logging.getLogger(__name__)

Logger = Callable[[str], None]

#: What MuseScore 4 says as it dies on the way out.
CRASH_SIGNATURE = "mutex lock failed"

#: SIGABRT as subprocess reports it, and as a shell does.
ABORTED = (-6, 134)

#: The application "Open in MuseScore" asks macOS for, unless MUSESCORE_APP says
#: otherwise.
DEFAULT_APP = "MuseScore 3"


class MuseScoreError(RuntimeError):
    """MuseScore did not produce the file it was asked for."""


@dataclass(frozen=True)
class Outcome:
    """What one MuseScore run came to.

    ``before`` is the output file's (modification time in ns, size) as it stood
    before the run, or ``None`` if there was no such file. It is what lets
    :func:`ok` tell a file this run wrote from one that was already lying there.
    """

    returncode: int
    stdout: str
    stderr: str
    output_path: str
    before: Optional[Tuple[int, int]]

    @property
    def said(self) -> str:
        """What MuseScore printed, for putting in an error message."""
        return self.stderr or self.stdout or ""


def _fingerprint(path: str) -> Optional[Tuple[int, int]]:
    try:
        found = os.stat(path)
    except OSError:
        return None
    return (found.st_mtime_ns, found.st_size)


def run(argv: Sequence[str], output_path: str,
        timeout: Optional[float] = None) -> Outcome:
    """Run MuseScore with ``argv`` (the binary first) and report what happened.

    ``output_path`` is the file the run is expected to leave. It is only looked
    at, never passed on: the caller's ``argv`` already says where to write.
    ``subprocess.TimeoutExpired`` is left to the caller, as it always was.
    """
    before = _fingerprint(output_path)
    result = subprocess.run(list(argv), capture_output=True, text=True,
                            errors="replace", timeout=timeout)
    return Outcome(result.returncode, result.stdout or "", result.stderr or "",
                   output_path, before)


def crashed_on_exit(outcome: Outcome) -> bool:
    """Whether this is the one crash that is forgiven: an abort with that message."""
    return (outcome.returncode in ABORTED
            and CRASH_SIGNATURE in (outcome.stderr + outcome.stdout))


def written_this_run(outcome: Outcome) -> bool:
    """Whether the output file is there and is not the one that was there before."""
    now = _fingerprint(outcome.output_path)
    return now is not None and now != outcome.before


# --- is the file whole? -----------------------------------------------------
#
# One check per kind of file MuseScore is asked for here. Each answers a single
# question -- did the writing finish -- and none of them judges the music.


def _xml(path: str) -> bool:
    etree.parse(path)
    return True


def _zip(path: str) -> bool:
    with zipfile.ZipFile(path) as archive:
        return archive.testzip() is None


def _midi(path: str) -> bool:
    """A header chunk, then track chunks that add up to exactly the file."""
    with open(path, "rb") as source:
        data = source.read()
    if len(data) < 8 or data[:4] != b"MThd":
        return False
    position = 8 + struct.unpack(">I", data[4:8])[0]
    tracks = 0
    while position < len(data):
        if position + 8 > len(data):
            return False
        kind = data[position:position + 4]
        position += 8 + struct.unpack(">I", data[position + 4:position + 8])[0]
        if position > len(data):
            return False
        if kind == b"MTrk":
            tracks += 1
    return tracks > 0 and position == len(data)


def wav_details(path: str) -> Optional[Dict[str, int]]:
    """What a whole WAV holds, or ``None`` if it is not one.

    ``format`` (1 integer PCM, 3 floating point, ...), ``channels``, ``rate`` and
    ``frames``. Whole means: RIFF/WAVE, no longer than it says it is, naming at
    least one channel and a sample rate, with a data chunk that has audio in it and
    is all there.

    Read by hand rather than with the ``wave`` module, for two reasons that pull in
    opposite directions. That module reads integer PCM and nothing else, and
    MuseScore 4 writes floating point: ``wave.Error: unknown format: 3`` on a file
    that plays. And it believes the header, so a file cut short opens without
    complaint.
    """
    size = os.path.getsize(path)
    with open(path, "rb") as source:
        head = source.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            return None
        if struct.unpack("<I", head[4:8])[0] + 8 > size:
            return None
        described = None
        position = 12
        while position + 8 <= size:
            source.seek(position)
            kind, length = struct.unpack("<4sI", source.read(8))
            if kind == b"fmt ":
                if length < 16:
                    return None
                described = struct.unpack("<HHIIHH", source.read(16))
            elif kind == b"data":
                if described is None:
                    return None
                tag, channels, rate, _byte_rate, block, _bits = described
                if channels <= 0 or rate <= 0 or block <= 0 or length <= 0:
                    return None
                if position + 8 + length > size:
                    return None
                return {"format": tag, "channels": channels, "rate": rate,
                        "frames": length // block}
            position += 8 + length + (length & 1)
    return None


def _wav(path: str) -> bool:
    return wav_details(path) is not None


def _pdf(path: str) -> bool:
    size = os.path.getsize(path)
    with open(path, "rb") as source:
        if source.read(5) != b"%PDF-":
            return False
        source.seek(max(0, size - 1024))
        return b"%%EOF" in source.read()


def _png(path: str) -> bool:
    size = os.path.getsize(path)
    if size < 20:
        return False
    with open(path, "rb") as source:
        if source.read(8) != b"\x89PNG\r\n\x1a\n":
            return False
        source.seek(size - 12)
        return source.read(12) == b"\x00\x00\x00\x00IEND\xaeB`\x82"


_CHECKS: Dict[str, Callable[[str], bool]] = {
    ".musicxml": _xml, ".xml": _xml, ".mscx": _xml, ".mei": _xml, ".svg": _xml,
    ".mpos": _xml,
    ".mscz": _zip, ".mxl": _zip,
    ".mid": _midi, ".midi": _midi,
    ".wav": _wav,
    ".pdf": _pdf,
    ".png": _png,
}


def complete(path: str) -> bool:
    """Whether the file at ``path`` was written to its end.

    A kind with no check of its own (mp3, ogg, flac) is held only to not being
    empty: that is weaker, and it is said here rather than dressed up.
    """
    try:
        if os.path.getsize(path) == 0:
            return False
        check = _CHECKS.get(os.path.splitext(path)[1].lower())
        return True if check is None else bool(check(path))
    except (OSError, ValueError, struct.error, etree.XMLSyntaxError,
            zipfile.BadZipFile):
        return False


def ok(outcome: Outcome, log: Optional[Logger] = None) -> bool:
    """Whether the run produced its file. See the module docstring for the rule."""
    path = outcome.output_path
    if outcome.returncode == 0:
        return os.path.exists(path)
    if crashed_on_exit(outcome) and written_this_run(outcome) and complete(path):
        note = (f"MuseScore crashed on exit after writing {os.path.basename(path)}; "
                "the file is complete and was accepted.")
        logger.warning(note)
        if log is not None:
            log(note)
        return True
    return False


def export(cli: str, input_path: str, output_path: str,
           timeout: Optional[float] = None, log: Optional[Logger] = None) -> str:
    """Ask MuseScore to write ``output_path`` from ``input_path``; return the path.

    Raises :class:`MuseScoreError`, carrying what MuseScore printed, if it did not.
    """
    outcome = run([cli, input_path, "-o", output_path], output_path, timeout=timeout)
    if not ok(outcome, log):
        raise MuseScoreError(
            f"MuseScore CLI failed writing {os.path.basename(output_path)}. "
            "Check MUSESCORE_CLI_PATH.\n" + outcome.said)
    return output_path


# --- opening a score in the application --------------------------------------


def app_name() -> str:
    """The application macOS is asked to open a score in."""
    return os.getenv("MUSESCORE_APP") or DEFAULT_APP


def open_score(path: str):
    """Open ``path`` in MuseScore on this machine (macOS ``open -a``)."""
    return subprocess.Popen(["open", "-a", app_name(), path])
