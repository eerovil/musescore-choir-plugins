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

Three things about homr that this module exists to absorb:

* It takes one image, writes ``<image>.musicxml`` beside it, and has no
  ``--output``. It also drops a ``_teaser.png`` and, in debug mode, more. So
  the run happens on a copy in a scratch directory and only the MusicXML is
  kept.
* ``--gpu`` defaults to ``auto``, which asks whether the CUDA provider is
  *registered* and not whether it can run. This host's card is below
  onnxruntime's floor (issue #93), so auto would pick CUDA and die on the
  first segnet node without falling back. Every call passes ``no``.
* Its **slurs are not paired**. ``slurStart`` and ``slurStop`` are predicted one
  note at a time, and the MusicXML ``number`` they would pair by is the staff
  number, the same for every slur on the staff — so a dropped stop leaves its
  start open to be closed by whatever stop comes next, and the slur that
  results swallows the syllable slots of everything under it. Every parse this
  module returns has been through :func:`resolve_slurs`, which is where that is
  argued out. It is a property of the tool, not of a page or a crop, so it is
  normalised once, here.

**A scan takes one of this host's heavy slots**, the same way the video render
does (:mod:`heavy_slot`, issue #100). A page is ~30s of every core on a
four-core host shared with the deck's own suites and a song rendering, and
three such jobs at once finish no sooner than one after another. Failing to
get a slot is fail-open and losing one stops the work — both of those are
:mod:`heavy_slot`'s decisions and neither is re-argued here.

**One slot per page, not one for the whole song.** A song is several pages and
each is a separate homr call writing its own MusicXML, so the page is the unit
this module has: releasing between pages lets a render or a suite in, and an
interrupted scan costs the page in flight rather than the song. The pages
already read are on disk. A caller that would rather hold one lease across a
whole song passes ``queue=False`` and wraps the loop itself, so the two never
nest.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from lxml import etree

from . import heavy_slot

Logger = Callable[[str], None]

#: Where ``scripts/install-homr.sh`` puts the venv when nobody says otherwise.
DEFAULT_VENV = os.path.join(
    os.path.expanduser("~"), ".local", "share", "musescore-choir-plugins", "homr-venv"
)

#: A page is ~30s (issue #93). This is a wedged-process guard, not a budget.
DEFAULT_TIMEOUT = 600

IMAGE_EXTS = (".png", ".jpg", ".jpeg")

#: How many barlines a slur may cross before it is read as a pairing accident
#: rather than music. See :func:`resolve_slurs`.
MAX_SLUR_BARS = int(os.getenv("OMR_MAX_SLUR_BARS", "1"))

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


def homr_available(binary: Optional[str] = None) -> bool:
    """Whether :func:`read_page` can run at all on this host."""
    binary = binary or homr_binary()
    if os.path.sep in binary:
        return os.access(binary, os.X_OK)
    return shutil.which(binary) is not None


# --- engines -------------------------------------------------------------
#
# A homr change is tried out on a branch, and the only question worth asking
# about one is whether it reads *this* repertoire better than what we have. That
# needs both to be runnable at once, and it needs the branch to be runnable
# **without an install**: a branch is edited, re-read, edited again, and a
# 660 MB reinstall between each pass is not a loop anybody uses.
#
# So a branch is not installed at all. The local fork checkout and every git
# worktree beside it are engines in their own right: the dependencies come from
# the installed venv, and the *code* comes from the working copy, put in front of
# it on ``PYTHONPATH``. Switching a branch in that checkout changes what the next
# scan runs, with nothing to rebuild and nothing to keep in step.
#
# The entry point is spelled out rather than run as ``-m homr``: homr's package
# has no ``__main__``, its console script is ``homr.main:main``, and the venv's
# own ``bin/homr`` would import the *installed* copy however PYTHONPATH is set.
#
# What that costs is one thing worth naming: an engine is now whatever is
# checked out at the moment it runs, so the label is read live from git and a
# parse is only accounted for by what the checkout says at the time. The
# installed venv stays as it was — an immutable-ish default to compare against.
#
# The choice is per scan run, and this pull request proposes that **what it
# resolved to is recorded on every parse it produces** (#154, #157). The label is
# what a person recognises and is useless as a record — `main` in a working copy
# means a different commit next week — so the record is the **commit**, with the
# label kept as the hint, and a **dirty** working copy says so or the commit is a
# claim about code that is not what ran. It is provenance and not a stamp:
# :func:`scan.content_stamp` steps over it, so an upgrade discards nothing.


@dataclass(frozen=True)
class Engine:
    """One homr this host can run: what to call it, what to show, what to run.

    ``command`` is the argv the image path is appended to, and ``env`` is what
    has to be added to the environment for it — ``PYTHONPATH`` for a checkout,
    nothing at all for the installed venv.

    ``commit`` and ``dirty`` are what a parse is recorded against. They come
    from the same place the label does — pip's own metadata for the installed
    venv, git for a working copy — and ``dirty`` is not a detail: a working copy
    with uncommitted edits ran code that is not at ``commit``, and a record that
    did not say so would be worse than no record at all.
    """

    key: str
    label: str
    command: List[str]
    env: Dict[str, str] = field(default_factory=dict)
    default: bool = False
    commit: Optional[str] = None
    dirty: bool = False


#: Written by the installer into the venv it builds, saying what is in it.
ENGINE_MARKER = "homr-engine.txt"

#: The key standing for "whatever homr the app would use anyway".
DEFAULT_ENGINE = "default"

#: The local fork's working copy. Its git worktrees are found from it, so this
#: is one path rather than a list, and the app never writes to any of them
#: except to link the model weights it would otherwise re-download per worktree.
CHECKOUT = os.getenv("HOMR_CHECKOUT", os.path.join(os.path.expanduser("~"), "homr"))


def _marker(venv: str) -> dict:
    try:
        with open(os.path.join(venv, ENGINE_MARKER), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return {}
    return dict(line.split("=", 1) for line in lines if "=" in line)


def _installed_vcs(venv: str) -> Dict[str, str]:
    """What pip actually installed, read out of the wheel's own metadata.

    ``direct_url.json`` records the revision that was asked for and the commit
    it resolved to, which is the only account of the installed engine that
    cannot be out of date. Saying "main" without it was a guess: the venv here
    predates the marker file, so the label read `main` and would have read
    `main` whatever commit had been installed.

    Returns ``{"revision": ..., "commit": ...}``, either of which may be absent.
    """
    for info in sorted(glob.glob(os.path.join(
            venv, "lib", "python3.*", "site-packages", "homr-*.dist-info"))):
        try:
            with open(os.path.join(info, "direct_url.json"), encoding="utf-8") as f:
                direct = json.load(f)
        except (OSError, ValueError):
            continue
        vcs = direct.get("vcs_info") or {}
        return {k: v for k, v in (("revision", vcs.get("requested_revision")),
                                  ("commit", vcs.get("commit_id"))) if v}
    return {}


def _installed_from(venv: str) -> Optional[str]:
    """The installed engine's label: the revision asked for, at the commit."""
    vcs = _installed_vcs(venv)
    revision, commit = vcs.get("revision"), vcs.get("commit", "")[:7]
    if revision and commit:
        return f"{revision} @ {commit}"
    return revision or commit or None


def default_engine() -> Optional[Engine]:
    """The installed homr, or ``None`` when this host has not got one.

    It is the one engine that does not move: pip put a copy of the source in
    the venv, so it stays where it was installed while every checkout engine
    follows whatever is checked out. That is what makes it the thing to compare
    a branch against, and it is why the label says the commit.
    """
    binary = homr_binary()
    if not homr_available(binary):
        return None
    venv = os.path.dirname(os.path.dirname(binary))
    fields = _marker(venv)
    label = (_installed_from(venv) or fields.get("branch")
             or fields.get("source") or "installed")
    return Engine(key=DEFAULT_ENGINE, label=f"installed: {label}",
                  command=[binary], default=True,
                  commit=_installed_vcs(venv).get("commit") or fields.get("commit"))


def _venv_python() -> Optional[str]:
    """The interpreter beside the installed homr — where the dependencies are."""
    binary = homr_binary()
    if not homr_available(binary) or os.path.sep not in binary:
        return None
    python = os.path.join(os.path.dirname(binary), "python")
    return python if os.access(python, os.X_OK) else None


def _worktrees(checkout: str) -> List[tuple]:
    """``(path, label, commit)`` for the checkout and each of its git worktrees.

    The label is the branch, read now rather than remembered, because that is
    the whole point: switching a branch in a working copy changes the engine
    without anything being reinstalled or re-registered. The commit comes off
    the same listing, and it is what a parse is recorded against: a fragment
    stamped ``main`` would say nothing a month later, which is exactly what left
    #129 diagnosing a defect that had already been fixed.
    """
    try:
        out = subprocess.run(
            ["git", "-C", checkout, "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    found, path, label, commit = [], None, None, None
    for line in out.stdout.splitlines() + [""]:
        if line.startswith("worktree "):
            path, label, commit = line[len("worktree "):], None, None
        elif line.startswith("HEAD "):
            commit = line[len("HEAD "):].strip() or None
        elif line.startswith("branch "):
            label = line[len("branch refs/heads/"):]
        elif line.startswith("detached"):
            label = "detached"
        elif not line and path:
            found.append((path, label or "detached", commit))
            path = None
    return found


def _is_dirty(path: str) -> bool:
    """Whether a working copy has edits that are not in its commit.

    A commit is a claim about what ran, and an edited checkout breaks it — so
    this is asked at the moment the engine is listed, next to the branch, and
    for the same reason. A git that cannot answer reads as clean rather than
    dirty: this is a caveat on a record, not a gate on running anything.
    """
    try:
        out = subprocess.run(["git", "-C", path, "status", "--porcelain"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and bool(out.stdout.strip())


#: What the checkout engines run. ``homr`` is a package with no ``__main__``, so
#: its console script's entry point is called directly.
RUN_HOMR = "from homr.main import main; main()"


def _package_dir(root: str) -> str:
    return os.path.join(root, "homr")


def link_weights(checkout: str) -> int:
    """Point a checkout at the installed venv's model weights.

    homr keeps its ~150 MB of weights *beside its own source*, so a working copy
    run from ``PYTHONPATH`` would download its own set — per worktree. The file
    names carry a content hash, so a symlink cannot be the wrong weights: a
    branch wanting different ones asks for a different name and downloads it.
    Only missing files are linked and nothing real is ever replaced.
    """
    binary = homr_binary()
    if os.path.sep not in binary:
        return 0
    venv = os.path.dirname(os.path.dirname(binary))
    installed = None
    for lib in sorted(glob.glob(os.path.join(venv, "lib", "python3.*", "site-packages"))):
        if os.path.isdir(os.path.join(lib, "homr")):
            installed = os.path.join(lib, "homr")
    if not installed or not os.path.isdir(_package_dir(checkout)):
        return 0
    linked = 0
    for source in glob.glob(os.path.join(installed, "**", "*.onnx"), recursive=True):
        target = os.path.join(_package_dir(checkout),
                              os.path.relpath(source, installed))
        if os.path.exists(target):
            continue
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            os.symlink(source, target)
            linked += 1
        except OSError:
            pass
    return linked


def engines() -> List[Engine]:
    """Every homr this host can run, the installed one first.

    The rest are the local fork's checkout and its git worktrees (``HOMR_CHECKOUT``),
    each labelled with the branch it has out at this moment. They need the
    installed venv for their dependencies, so without it there are none.
    """
    found: List[Engine] = []
    default = default_engine()
    if default:
        found.append(default)

    python = _venv_python()
    if not python:
        return found
    used = {DEFAULT_ENGINE}
    for path, label, commit in _worktrees(CHECKOUT):
        if not os.path.isdir(_package_dir(path)):
            continue                       # not a homr working copy after all
        key = os.path.basename(os.path.normpath(path))
        while key in used:
            key += "-"
        used.add(key)
        # Branch *and* directory, because neither alone identifies a working
        # copy: a worktree keeps its directory name when its branch changes
        # (a tree called `system-4` is currently on `main`), and two trees can
        # be on branches that look alike.
        found.append(Engine(key=key, label=f"{label} — {key}",
                            command=[python, "-c", RUN_HOMR],
                            env={"PYTHONPATH": path},
                            commit=commit, dirty=_is_dirty(path)))
    return found


def engine_for(key: Optional[str]) -> Engine:
    """The engine a key names, or the default one for ``None``.

    An unknown key is refused rather than falling back: a scan run with a homr
    other than the one that was asked for is a parse nobody can account for.
    """
    if not key or key == DEFAULT_ENGINE:
        engine = default_engine()
        if not engine:
            raise HomrMissing(
                f"homr is not installed ({homr_binary()}). Run "
                "scripts/install-homr.sh, or set HOMR_BIN if it lives elsewhere.")
        return engine
    for engine in engines():
        if engine.key == key:
            if engine.env.get("PYTHONPATH"):
                link_weights(engine.env["PYTHONPATH"])
            return engine
    raise HomrMissing(
        f"No homr engine called {key!r} is available. Engines are the installed "
        f"venv and the working copies under {CHECKOUT}; check it is checked out "
        "there, or scan with the default one.")


# --- provenance ----------------------------------------------------------
#
# **Which homr read this, written into the parse itself** (#154, #157).
#
# The reader that has to be reached is not the app. #129 spent a session
# diagnosing a defect that had already been fixed, from
# `songs/test/scan/system-04@200-bedc89f000.musicxml` opened straight off disk by
# something that never opened the Scan panel — so a record kept only in
# `.song.json` would not have reached it. The file is what gets read in
# isolation, so the file is what has to carry it.
#
# It is one comment line rather than a `<miscellaneous>` element on purpose: it
# is inserted and removed textually, so a parse homr wrote comes back byte for
# byte once the line is taken off again. That is what lets
# :func:`scan.content_stamp` step over it, which is what makes this provenance
# rather than a stamp — a fragment read again by a newer homr with the same
# result costs nobody their grid answers or their approval.

#: What the line is called, in the file and in the regex that finds it again.
PROVENANCE_TAG = "homr-engine"

_PROVENANCE_RE = re.compile(
    rb"[ \t]*<!--\s*" + PROVENANCE_TAG.encode() + rb"\b[^\n]*?-->[ \t]*\n?")

#: Where a comment can go: before the root element, after the declaration and
#: any doctype. ``<?`` and ``<!`` are exactly those two.
_ROOT_RE = re.compile(rb"<(?![?!])")

_FIELD_RE = re.compile(r'(\w+)="([^"]*)"')


def provenance(engine: Optional["Engine"]) -> Dict[str, object]:
    """What to record about the homr that read a page.

    The commit is the part that still means something later; the label is what
    a person recognises and is worth nothing on its own, since ``main`` in a
    working copy is a different commit next week. ``dirty`` says the working
    copy had edits that are not in that commit.
    """
    if engine is None:
        return {}
    return {"engine": engine.key, "label": engine.label,
            "commit": engine.commit or "", "dirty": bool(engine.dirty)}


def _quotable(value: str) -> str:
    """A value safe inside an XML comment attribute."""
    return str(value).replace('"', "'").replace("--", "- -").replace("\n", " ")


def provenance_comment(record: Dict[str, object]) -> bytes:
    """The one line a fragment carries, as it is written into the file."""
    fields = " ".join(
        f'{k}="{_quotable(v)}"' for k, v in (
            ("engine", record.get("engine", "")),
            ("label", record.get("label", "")),
            ("commit", record.get("commit", "")),
            ("dirty", "yes" if record.get("dirty") else "no"),
        ))
    return f"<!-- {PROVENANCE_TAG} {fields} -->\n".encode("utf-8")


def strip_provenance(data: bytes) -> bytes:
    """The file as homr wrote it, with any provenance line taken back off.

    Byte for byte, which is the point: it is what lets the content of a parse be
    compared without the identity of its reader counting as content.
    """
    return _PROVENANCE_RE.sub(b"", data)


def stamp_provenance(path: str, engine: Optional["Engine"]) -> None:
    """Write which homr read this into the MusicXML, replacing any earlier line."""
    record = provenance(engine)
    if not record:
        return
    with open(path, "rb") as f:
        data = strip_provenance(f.read())
    match = _ROOT_RE.search(data)
    at = match.start() if match else len(data)
    with open(path, "wb") as f:
        f.write(data[:at] + provenance_comment(record) + data[at:])


def read_provenance(path: str) -> Optional[Dict[str, object]]:
    """Which homr read a MusicXML file, or ``None`` when nobody knows.

    ``None`` is the honest answer for every fragment that predates this and
    there is no way to recover a better one — "nobody knows which homr wrote
    this" is the state #129 was in, said out loud.
    """
    try:
        with open(path, "rb") as f:
            found = _PROVENANCE_RE.search(f.read())
    except OSError:
        return None
    if not found:
        return None
    fields = dict(_FIELD_RE.findall(found.group().decode("utf-8", "replace")))
    if not fields:
        return None
    return {"engine": fields.get("engine", ""), "label": fields.get("label", ""),
            "commit": fields.get("commit", ""), "dirty": fields.get("dirty") == "yes"}


def read_page(
    image_path: str,
    out_dir: Optional[str] = None,
    log: Logger = _noop,
    timeout: int = DEFAULT_TIMEOUT,
    label: Optional[str] = None,
    queue: bool = True,
    engine: Optional[Engine] = None,
) -> str:
    """Read one page image and return the path of the MusicXML written for it.

    The file is named after the image and lands in ``out_dir`` (the image's own
    directory by default). An existing file there is overwritten, so re-reading
    a page replaces its answer rather than accumulating.

    ``log`` is called with each line homr prints — that is the only progress
    this takes minutes to produce, so a caller with a person waiting should
    pass one. It is also where the run can be stopped: those lines are the
    heavy slot's checkpoints, so a lease lost mid-page raises ``SlotLost``
    there rather than at the end.

    ``label`` is what the queue shows for this page; ``queue=False`` runs
    without asking for a slot, for a caller already holding one. ``engine``
    reads the page with a homr other than the installed one (:func:`engines`) —
    a working copy of the fork, run from its own source.

    The MusicXML that comes back has had its slurs resolved (:func:`resolve_slurs`)
    and carries one comment line saying which homr read it
    (:func:`stamp_provenance`), so a parse read off disk on its own still says
    where it came from.
    """
    if not os.path.exists(image_path):
        raise HomrError(f"No such image: {image_path}")
    if not image_path.lower().endswith(IMAGE_EXTS):
        raise HomrError(
            f"homr reads {', '.join(IMAGE_EXTS)}, not {os.path.splitext(image_path)[1]}: "
            f"{image_path}"
        )

    engine = engine or default_engine()
    if not engine:
        raise HomrMissing(
            f"homr is not installed ({homr_binary()}). Run scripts/install-homr.sh, "
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

        with _queued(label or f"song app homr {base}", log, queue) as slot:
            # homr's own output is the only place a page can be interrupted, so
            # that is where the lease is checked (heavy_slot.Slot.guard).
            watched = slot.guard(log)
            watched(f"Reading {os.path.basename(image_path)} with homr")
            output = _run(list(engine.command) + ["--gpu", "no", scratch_image],
                          watched, timeout, engine.env)
            slot.check()

        if not os.path.exists(produced):
            # homr deletes its own output when parsing fails, so a zero exit
            # with no file is still a failure and has to be reported as one.
            raise HomrError(
                f"homr produced no MusicXML for {os.path.basename(image_path)}.\n"
                + _tail(output)
            )

        resolve_slurs_in(produced, log=watched)
        # Last, so the parse carries the identity of whatever produced it
        # however it got here — and so the line is the only thing between what
        # homr wrote and what is on disk.
        stamp_provenance(produced, engine)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.move(produced, destination)

    return destination


def resolve_slurs_in(musicxml_path: str, log: Logger = _noop) -> int:
    """Resolve the slurs of every part in a MusicXML file, in place.

    A parse with nothing to change is left untouched rather than rewritten, so
    a page homr got right comes back exactly as homr wrote it.
    """
    tree = etree.parse(musicxml_path)
    root = tree.getroot()
    before = len(root.findall(".//slur"))
    dropped = sum(resolve_slurs(part) for part in root.findall("part"))
    if len(root.findall(".//slur")) != before:
        tree.write(musicxml_path, xml_declaration=True, encoding="UTF-8")
    if dropped:
        log(f"Dropped {dropped} slur{'s' if dropped > 1 else ''} homr never engraved")
    return dropped


def resolve_slurs(part: etree._Element, max_bars: int = MAX_SLUR_BARS) -> int:
    """Pair up homr's slur tokens, and drop the pairs that run away.

    homr predicts ``slurStart`` / ``slurStop`` as **per-note tokens, one at a
    time** (``music_xml_generator.build_slurs``), and there is no pairing pass
    anywhere. The MusicXML ``number`` that pairing depends on is set to the
    *staff* number, so it is identical for every slur on that staff. Two things
    follow, and the second is the damaging one: homr cannot express two
    overlapping slurs, and a dropped stop does not merely lose its own slur --
    it leaves the start open to be closed by whatever stop comes next.

    On B5's whole-page parse, 42 starts and 37 stops import as 21 slurs, two of
    them runaway: one spanning 5 1/4 bars from m46, one spanning 3 bars from
    m54. Between them they cover 21 notes, and a slur continuation takes no
    syllable, so the page offers 91 lyric slots for 132 notes. **Sixteen
    syllable slots swallowed by two slurs nobody engraved** -- and they surface
    as ``too_few``, which the reading playbook teaches a reader to attribute to
    a voice sharing another staff's words. The failure points at a wrong
    diagnosis rather than at itself.

    So: walk the tokens in order, pair them, and keep only the pairs whose ends
    are at most ``max_bars`` barlines apart. Everything else goes -- the runaway
    pairs, a start made while one is already open, and a stop with nothing open.
    What is written back is one unambiguous alternating stream, which is the
    point: it says what we mean and leaves the importer nothing to guess at.
    Returns how many runaway pairs were dropped.

    **This belongs here and not further downstream**, because the ``number``
    the mis-pairing turns on is the staff number and nothing about it is
    per-page or per-crop. A whole-page parse has it, and so does one system cut
    out of the same page. It is a property of the tool, so it is normalised
    once at the boundary where the app meets the tool -- next to the missing
    ``--output``, the teaser litter and the MusicXML homr deletes when parsing
    raises.

    **The threshold was measured, not assumed.** Across all seven homr parses of
    the benchmark, every pair spans nought or one bar apart from those two
    runaways; on the very page they come from, the human-corrected ``Lemmen
    nosto`` has no slur crossing more than one barline in its first 68 bars, and
    the hand-verified fixture has none at all. One barline is what a genuine
    melisma crosses (``il-man il-ki-rii-vi-`` is the worked example in the lyric
    tests), so the rule leaves real music alone. It is not a claim about
    engraving in general -- modern choral scores in ``songs/`` do print phrase
    marks over four bars. It is a claim about *this input*, where a long slur
    cannot be told from an accident because homr has no way to write one
    deliberately.

    **Taking the unmatched tokens out is not tidiness, and this is the part that
    cost the most to find.** Issue #112 measured a lone dangler as cosmetic --
    MuseScore drops it, silently -- and it is, in isolation. It is not cosmetic
    in a stream. Put four quarter-note bars through the CLI with a stop that
    closes nothing, and *every later slur of that number is lost too*; and
    leaving the redundant starts in means that removing a runaway pair merely
    promotes one, which closes on a stop further away still. Removing the
    runaway pairs alone left B5 with a fresh 2-bar runaway at m51. Removing the
    redundant starts alone dropped B5 from 21 slurs to 6. Doing all of it in one
    pass gives 24 slurs, none of them spanning more than a bar -- exactly the
    pairing computed here, so what the score says and what this function decided
    cannot drift apart. Five of those 24 are short slurs homr got right and the
    unmatched tokens were costing it.
    """
    doomed: List[etree._Element] = []
    dropped = 0
    open_slurs: dict = {}

    for bar, measure in enumerate(part.findall("measure")):
        for note in measure.findall("note"):
            for slur in note.findall("notations/slur"):
                number = slur.get("number", "1")
                kind = slur.get("type")
                if kind == "start":
                    if number in open_slurs:
                        # MuseScore keeps the first of two starts sharing a
                        # number and discards this one; so do we, explicitly.
                        doomed.append(slur)
                    else:
                        open_slurs[number] = (bar, slur)
                elif kind == "stop":
                    began = open_slurs.pop(number, None)
                    if began is None:
                        doomed.append(slur)
                    elif bar - began[0] > max_bars:
                        doomed.extend((began[1], slur))
                        dropped += 1

    doomed.extend(slur for _, slur in open_slurs.values())
    for slur in doomed:
        notations = slur.getparent()
        notations.remove(slur)
        if len(notations) == 0:
            notations.getparent().remove(notations)
    return dropped


@contextmanager
def _queued(label: str, log: Logger, queue: bool):
    """A heavy slot for this page, or the un-held Slot when the caller has one."""
    if not queue:
        yield heavy_slot.Slot()
        return
    with heavy_slot.heavy_slot(label, log=log) as slot:
        yield slot


def _run(command: List[str], log: Logger, timeout: int,
         extra_env: Optional[Dict[str, str]] = None) -> List[str]:
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
            env={**os.environ, **(extra_env or {})},
        )
    except OSError as exc:
        raise HomrMissing(f"Could not run {command[0]}: {exc}") from exc

    expired = threading.Event()

    def give_up() -> None:
        expired.set()
        _kill(process)

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
    except BaseException:
        # The log callback carries the heavy slot's check, so it can raise
        # here. Abandoning the loop without this would leave homr running on
        # cores that have been promised to somebody else.
        _kill(process)
        raise
    finally:
        deadline.cancel()
        if process.stdout is not None:
            process.stdout.close()

    if expired.is_set():
        raise HomrError(f"homr did not finish within {timeout}s.\n" + _tail(lines))
    if returncode != 0:
        raise HomrError(f"homr exited {returncode}.\n" + _tail(lines))
    return lines


def _kill(process: subprocess.Popen) -> None:
    """Kill homr and anything it started (it runs in its own process group)."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _tail(lines: List[str]) -> str:
    return "\n".join(lines[-_ERROR_TAIL_LINES:])
