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
* Its **slurs used to come out unpaired**, so a dropped stop let one slur run
  away over bars nobody engraved and swallow their syllable slots. That repair
  lived here until #144 and is now homr's own (``homr/slur_resolution.py``,
  eerovil/homr#62): under #141's rule a slur that is not on the page is homr's
  not to emit. Re-measured on the installed fork, the old pass dropped nothing
  on 21 systems. A homr older than that fork is not repaired here any more.
* Its **whole-measure rests are not given a voice of their own**. A whole rest
  and a sung voice's notes come out sharing one ``<voice>``, which overfills
  the bar by a whole note by construction. :func:`split_measure_rests` moves
  the rest out; the reasoning is there.

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
from fractions import Fraction
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


#: The bar length the music before an image was in; see `read_page`'s ``bar_length``.
BAR_LENGTH_FLAG = "--bar-length"

#: Asks homr to read each image twice and put a red ``⚠`` text on every bar it is
#: probably wrong about (#245). A homr without it reads as before, unmarked.
MARK_DOUBT_FLAG = "--mark-doubt"


def _engine_source(engine: Engine) -> Optional[str]:
    """The ``homr/main.py`` an engine runs: its checkout's, or the installed venv's."""
    checkout = engine.env.get("PYTHONPATH")
    if checkout:
        return os.path.join(_package_dir(checkout), "main.py")
    binary = engine.command[0] if engine.command else homr_binary()
    if os.path.sep not in binary:
        return None
    venv = os.path.dirname(os.path.dirname(binary))
    found = sorted(glob.glob(os.path.join(venv, "lib", "python3.*", "site-packages",
                                          "homr", "main.py")))
    return found[-1] if found else None


def engine_supports(engine: Engine, flag: str) -> bool:
    """Whether this engine's homr takes an option, read off its own source.

    Asked rather than tried: an older homr refuses an option it does not know
    and the page is lost, while a missing hint only costs the reading it would
    have helped choose. So an engine whose source cannot be found is taken not
    to have it.
    """
    source = _engine_source(engine)
    if not source or not os.path.exists(source):
        return False
    with open(source, encoding="utf-8") as handle:
        return f'"{flag}"' in handle.read()


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
                "scripts/install-homr.sh (or press Install homr in a song's Scan panel), "
                "or set HOMR_BIN if it lives elsewhere.")
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


#: Where homr thinks each note is on the image it was given (upstream dda4d2f): one
#: comment inside every ``<note>``, from the decoder's attention. It is a position,
#: not music, so like the provenance line it is stepped over when a parse's content
#: is compared -- otherwise every song's first re-read after the upgrade would lapse
#: its scan approval with no note changed (#220).
_IMAGE_POSITION_RE = re.compile(rb"\s*<!--\s*imgpos:[^>]*?-->")


def strip_image_positions(data: bytes) -> bytes:
    """The parse with homr's ``imgpos`` comments taken out, whitespace before them too."""
    return _IMAGE_POSITION_RE.sub(b"", data)


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
    repairs: Optional[List["MovedRest"]] = None,
    bar_length: Optional[Fraction] = None,
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

    ``bar_length`` is the bar length the music before this image was in, as a
    fraction of a whole note. homr reads one printed system at a time and knows
    nothing of the one before it, so where a bar's rhythm reads equally well at
    two lengths -- Legenda system 8 (eerovil/musescore-choir-plugins#245) fits
    4/4 and 6/4, and system 7 before it is in 4/4 -- this is what chooses. It is
    passed only to a homr that takes it (:func:`engine_supports`).

    Every read asks homr to mark the bars it is probably wrong about
    (``--mark-doubt``, #245): homr reads the image a second time and puts a red
    ``⚠`` text, naming what it doubted, at the head of each such bar. Those are
    the same marks cleaning leaves, so the health check and the Fix panel list
    them until somebody deletes them in MuseScore, and the video never shows
    them. The owner asked that no wrong bar go unmarked; a read takes twice as
    long for it. A homr too old to mark is said so in the log, because a score
    with no marks then means "not checked" rather than "nothing doubted".

    The MusicXML that comes back has had its shared whole-measure rests moved
    out (:func:`split_measure_rests`),
    and it carries one comment line saying which homr read it
    (:func:`stamp_provenance`), so a parse read off disk on its own still says
    where it came from.

    ``repairs`` is a list to fill with what the whole-rest repair moved. A
    caller that has to tell somebody a bar was touched passes one -- the log
    says it too, but a log is not a record. :mod:`scan` writes them into the
    song's ``fixes.json``.
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
            f"homr is not installed ({homr_binary()}). Run scripts/install-homr.sh "
            "(or press Install homr in a song's Scan panel), "
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
            argv = list(engine.command) + ["--gpu", "no", "--no-title"]
            if bar_length is not None and engine_supports(engine, BAR_LENGTH_FLAG):
                argv += [BAR_LENGTH_FLAG, str(bar_length)]
            if engine_supports(engine, MARK_DOUBT_FLAG):
                argv.append(MARK_DOUBT_FLAG)
            else:
                watched("This homr cannot mark the bars it is unsure of, so none are "
                        "marked: update homr to have them checked")
            output = _run(argv + [scratch_image], watched, timeout, engine.env)
            slot.check()

        if not os.path.exists(produced):
            # homr deletes its own output when parsing fails, so a zero exit
            # with no file is still a failure and has to be reported as one.
            raise HomrError(
                f"homr produced no MusicXML for {os.path.basename(image_path)}.\n"
                + _tail(output)
            )

        was_moved = split_measure_rests_in(produced, log=watched)
        if repairs is not None:
            repairs.extend(was_moved)
        # Last, so the parse carries the identity of whatever produced it
        # however it got here — and so the line is the only thing between what
        # homr wrote and what is on disk.
        stamp_provenance(produced, engine)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.move(produced, destination)

    return destination


@dataclass(frozen=True)
class MovedRest:
    """One whole-measure rest that was given a voice of its own.

    Everything a person needs to find the bar again on the page: the measure as
    the parse numbers it, the staff it is written on, the voice it was sharing
    and the voice it was moved to.
    """

    measure: str
    staff: str
    was: str
    now: str

    def said(self) -> str:
        return (f"bar {self.measure}, staff {self.staff}: a whole-measure rest was "
                f"sharing voice {self.was} with sung notes and was moved to voice "
                f"{self.now}")


#: The elements that move a measure's cursor. Anything else between the first
#: and the last note of a measure is something :func:`split_measure_rests` does
#: not understand, and it leaves such a measure alone.
_TIMED = ("note", "backup", "forward")


def split_measure_rests_in(musicxml_path: str, log: Logger = _noop) -> List[MovedRest]:
    """Split every part's shared whole-measure rests, in place.

    A parse with nothing to move is left untouched rather than rewritten, so a
    page homr got right comes back exactly as homr wrote it.
    """
    tree = etree.parse(musicxml_path)
    root = tree.getroot()
    moved: List[MovedRest] = []
    for part in root.findall("part"):
        moved.extend(split_measure_rests(part))
    if moved:
        tree.write(musicxml_path, xml_declaration=True, encoding="UTF-8")
        log(f"Moved {len(moved)} whole-measure rest{'s' if len(moved) > 1 else ''} "
            "into a voice of their own")
        for one in moved:
            log("  " + one.said())
    return moved


def split_measure_rests(part: etree._Element) -> List[MovedRest]:
    """Give a shared whole-measure rest a voice of its own, and close the gap.

    **The rule.** A rest written ``type="whole"`` that shares a ``<voice>`` with
    any other note or rest cannot be that voice's, because a whole-measure rest
    alone fills the bar. So it belongs to a voice nothing else is in.

    homr has no way to say "second voice on this staff" -- there is no voice
    token, no voice field on its symbol record, and ``upper``/``lower`` mean
    *staff* rather than voice (upstream say so themselves in liebharc/homr#126).
    So a printed whole-bar rest and the notes of the voice engraved beside it
    come out in one stream with no ``<backup>`` between them, and the bar
    overfills by a whole note automatically. The bar this was diagnosed against
    (#130) is 4/4 and came out **seven quarters long**: a whole rest of 16
    divisions in front of a half, a dotted eighth and a 16th. Neither
    ``preprocess_corrupted_measures`` nor ``fix_overfull_measures`` will touch
    it -- the second correctly refuses a voice that ends on a note -- so it
    reaches the cleaned score as ``len="28/16"``, and every other staff's
    measure rest in that bar is then the wrong length, which no health check
    sees and which surfaces only as a scrolling video the renderer refuses.

    **Why this is honest.** Nothing is invented and nothing is deleted: the same
    notes and the same rest come out, with one voice number changed and the
    cursor arithmetic redone around it. It needs **no meter**, which is what
    makes it a boundary repair rather than a job for ``clean_score``: a
    per-system crop usually declares no time signature at all, so a rule that
    had to know the bar length could not run here.

    **What it does not fix.** The voice the rest was sharing is left however
    homr read it -- in the diagnosed bar, three quarters of music in a bar of
    four, because a quarter rest was lost as well. That is deliberate: a bar
    that is *short* is a better failure than a bar 7 quarters long, since the
    missing note surfaces as lyric syllable overflow at import while the long
    bar silently drags the practice track. Inventing the missing note is what
    ``fix_overfull_measures`` refuses to do and this refuses it too. It is still
    a bar somebody has to look at, which is why :mod:`scan` writes what was
    moved into the song's ``fixes.json`` as an outstanding free-text fix. A
    repair nobody is told about turns a loudly wrong bar into a quietly wrong
    one, and cleaning would then pad the hole and health would go silent.

    **How narrow it is.** Measured over the seven benchmark parses and the
    fixture's nine system crops (#130): all 41 whole rests carry a full whole
    note whatever the meter, and this rule fires on **two** of them -- the ones
    that share a voice. The other 39 rest alone in their voice, make no musical
    claim, and are already re-lengthed to the real bar by
    ``fix_overfull_measures``.

    Returns one :class:`MovedRest` per rest moved.
    """
    highest = 0
    for note in part.iter("note"):
        voice = (note.findtext("voice") or "").strip()
        if voice.isdigit():
            highest = max(highest, int(voice))

    # One number past every voice the part uses, so it is free in every measure
    # -- and the same number in each, because a voice means nothing outside the
    # measure it is written in and a score with a voice per bar is one MuseScore
    # cannot hold (it keeps four to a staff).
    moved: List[MovedRest] = []
    for measure in part.findall("measure"):
        moved.extend(_split_measure(measure, highest + 1))
    return moved


def _is_measure_rest(note: etree._Element) -> bool:
    return (note.find("rest") is not None and note.find("chord") is None
            and (note.findtext("type") or "").strip() == "whole")


def _duration(el: etree._Element) -> int:
    text = (el.findtext("duration") or "").strip()
    return int(text) if text.isdigit() else 0


def _cursor_move(tag: str, duration: int) -> etree._Element:
    el = etree.Element(tag)
    etree.SubElement(el, "duration").text = str(duration)
    return el


def _split_measure(measure: etree._Element, spare: int) -> List[MovedRest]:
    """One measure's worth of :func:`split_measure_rests`.

    The measure is re-emitted rather than patched in place. Relabelling the
    rest's ``<voice>`` on its own would leave the bar exactly as long as it was,
    because the rest still occupies its 16 divisions of the cursor and the notes
    behind it still start after them. What has to happen is that the rest comes
    *out of that voice's stream* and the notes behind it move back into the room
    it was taking, which is arithmetic across the whole measure and not a local
    edit.
    """
    children = list(measure)
    notes = [c for c in children if c.tag == "note"]
    if not notes:
        return []
    first, last = children.index(notes[0]), children.index(notes[-1])
    body = children[first:last + 1]
    if any(c.tag not in _TIMED for c in body):
        # A direction, a print or a barline sitting among the notes is something
        # whose place in the stream carries meaning we would be guessing at.
        return []

    # Where each note actually sounds, by the cursor rules: a note advances it,
    # a note in a chord shares the previous onset, backup and forward move it.
    items: List[list] = []
    cursor = previous = 0
    for el in body:
        if el.tag == "backup":
            cursor -= _duration(el)
        elif el.tag == "forward":
            cursor += _duration(el)
        else:
            chord = el.find("chord") is not None
            items.append([el, previous if chord else cursor, chord])
            if not chord:
                previous = cursor
                cursor += _duration(el)

    order: List[str] = []
    groups: Dict[str, List[list]] = {}
    for item in items:
        voice = (item[0].findtext("voice") or "1").strip() or "1"
        if voice not in groups:
            groups[voice] = []
            order.append(voice)
        groups[voice].append(item)

    targets = []
    for voice in list(order):
        group = groups[voice]
        rests = [it for it in group if _is_measure_rest(it[0])]
        if rests and len(group) > len(rests):
            targets.extend((voice, it) for it in rests
                           if it[0].find("voice") is not None)
    if not targets:
        return []

    moved: List[MovedRest] = []
    for voice, item in targets:
        group = groups[voice]
        position = group.index(item)
        room = _duration(item[0])
        for later in group[position + 1:]:
            later[1] -= room
        group.pop(position)
        now = str(spare + len(moved))
        item[0].find("voice").text = now
        groups[now] = [item]
        order.append(now)
        moved.append(MovedRest(
            measure=measure.get("number") or "?",
            staff=(item[0].findtext("staff") or "1").strip() or "1",
            was=voice, now=now,
        ))

    rebuilt: List[etree._Element] = []
    cursor = 0
    for voice in order:
        for el, onset, chord in groups[voice]:
            if not chord:
                if onset > cursor:
                    rebuilt.append(_cursor_move("forward", onset - cursor))
                elif onset < cursor:
                    rebuilt.append(_cursor_move("backup", cursor - onset))
                cursor = onset
            rebuilt.append(el)
            if not chord:
                cursor += _duration(el)

    for el in body:
        measure.remove(el)
    for offset, el in enumerate(rebuilt):
        measure.insert(first + offset, el)
    return moved


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
