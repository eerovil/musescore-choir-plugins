"""Run homr (optical music recognition) on a page image.

One public call: give it a page image, get a MusicXML path back. Everything
about *how* homr is reached lives here — where its interpreter is, that the
picture and the answer land in the same folder, that a hundred lines of
progress on stderr are a progress channel rather than noise, and that a
failure has to say what went wrong rather than return a number.

**homr is not in the app's environment and cannot be.** It is ~660 MB of
onnxruntime and opencv wheels plus ~150 MB of model weights it keeps inside
its own site-packages, and the unattended deploy reinstalls the app's
requirements every two minutes on merge. So it lives in a venv of its own,
built by ``scripts/install-homr.sh`` outside the checkout, and is called as a
subprocess. A page is ~30 seconds, so the cost of a process is not a number
worth thinking about.

Two things about the CLI that this module exists to absorb:

* It takes one image, writes ``<image>.musicxml`` beside it, and has no
  ``--output``. It also drops a ``_teaser.png`` and, in debug mode, more. So
  the run happens on a copy in a scratch directory and only the MusicXML is
  kept.
* ``--gpu`` defaults to ``auto``, which asks whether the CUDA provider is
  *registered* and not whether it can run. This host's card is below
  onnxruntime's floor (issue #93), so auto would pick CUDA and die on the
  first segnet node without falling back. Every call passes ``no``.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import threading
from typing import Callable, List, Optional

Logger = Callable[[str], None]

#: Where ``scripts/install-homr.sh`` puts the venv when nobody says otherwise.
DEFAULT_VENV = os.path.join(
    os.path.expanduser("~"), ".local", "share", "musescore-choir-plugins", "homr-venv"
)

#: A page is ~30s (issue #93). This is a wedged-process guard, not a budget.
DEFAULT_TIMEOUT = 600

IMAGE_EXTS = (".png", ".jpg", ".jpeg")

#: How much of homr's output an error carries. Its stderr is chatty and the
#: line that explains the failure is at the end.
_ERROR_TAIL_LINES = 20


class HomrError(RuntimeError):
    """homr could not read the image, or could not be run at all."""


class HomrMissing(HomrError):
    """homr is not installed on this host."""


def _noop(_msg: str) -> None:
    pass


def homr_binary() -> str:
    """The homr executable: ``HOMR_BIN``, else the default venv, else PATH."""
    configured = os.getenv("HOMR_BIN")
    if configured:
        return configured
    default = os.path.join(DEFAULT_VENV, "bin", "homr")
    if os.path.exists(default):
        return default
    return "homr"


def homr_available() -> bool:
    """Whether :func:`read_page` can run at all on this host."""
    binary = homr_binary()
    if os.path.sep in binary:
        return os.access(binary, os.X_OK)
    return shutil.which(binary) is not None


def read_page(
    image_path: str,
    out_dir: Optional[str] = None,
    log: Logger = _noop,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Read one page image and return the path of the MusicXML written for it.

    The file is named after the image and lands in ``out_dir`` (the image's own
    directory by default). An existing file there is overwritten, so re-reading
    a page replaces its answer rather than accumulating.

    ``log`` is called with each line homr prints — that is the only progress
    this takes minutes to produce, so a caller with a person waiting should
    pass one.
    """
    if not os.path.exists(image_path):
        raise HomrError(f"No such image: {image_path}")
    if not image_path.lower().endswith(IMAGE_EXTS):
        raise HomrError(
            f"homr reads {', '.join(IMAGE_EXTS)}, not {os.path.splitext(image_path)[1]}: "
            f"{image_path}"
        )

    binary = homr_binary()
    if not homr_available():
        raise HomrMissing(
            f"homr is not installed ({binary}). Run scripts/install-homr.sh, "
            "or set HOMR_BIN if it lives somewhere else."
        )

    base = os.path.splitext(os.path.basename(image_path))[0]
    destination = os.path.join(out_dir or os.path.dirname(os.path.abspath(image_path)),
                               base + ".musicxml")

    # homr writes beside its input and litters a teaser image next to it, so it
    # is given a copy in a directory of its own and only the answer is kept.
    with tempfile.TemporaryDirectory(prefix="homr-") as scratch:
        scratch_image = os.path.join(scratch, os.path.basename(image_path))
        shutil.copy2(image_path, scratch_image)
        produced = os.path.join(scratch, base + ".musicxml")

        log(f"Reading {os.path.basename(image_path)} with homr")
        output = _run([binary, "--gpu", "no", scratch_image], log, timeout)

        if not os.path.exists(produced):
            # homr deletes its own output when parsing fails, so a zero exit
            # with no file is still a failure and has to be reported as one.
            raise HomrError(
                f"homr produced no MusicXML for {os.path.basename(image_path)}.\n"
                + _tail(output)
            )

        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.move(produced, destination)

    return destination


def _run(command: List[str], log: Logger, timeout: int) -> List[str]:
    """Run homr, streaming its output to ``log``, and return the lines.

    The deadline is a timer that kills the process, not ``wait(timeout=...)``:
    reading the pipe is what blocks, and a wedged homr holding it open would
    never reach the wait at all.
    """
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            # Its own process group, so a deadline can take any child with it.
            start_new_session=True,
        )
    except OSError as exc:
        raise HomrMissing(f"Could not run {command[0]}: {exc}") from exc

    expired = threading.Event()

    def give_up() -> None:
        expired.set()
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()

    deadline = threading.Timer(timeout, give_up)
    deadline.start()

    lines: List[str] = []
    try:
        assert process.stdout is not None
        for line in process.stdout:
            line = line.rstrip("\n")
            if line:
                lines.append(line)
                log(line)
        returncode = process.wait()
    finally:
        deadline.cancel()
        if process.stdout is not None:
            process.stdout.close()

    if expired.is_set():
        raise HomrError(f"homr did not finish within {timeout}s.\n" + _tail(lines))
    if returncode != 0:
        raise HomrError(f"homr exited {returncode}.\n" + _tail(lines))
    return lines


def _tail(lines: List[str]) -> str:
    return "\n".join(lines[-_ERROR_TAIL_LINES:])
