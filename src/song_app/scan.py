"""The scan stage: read a score off its PDF, one printed system at a time.

:mod:`omr_systems` can read a band and put the bands back together. Nothing
called it. This module is what the app calls: it takes a song that has a PDF and
printed-system bounds, and leaves behind an input score the rest of the app
already knows what to do with.

**A band is padded before it is cropped.** Issue #112 measured it: B5's second
system cropped exactly on its printed bounds came back as 2 parts, and padded by
60px it came back as 3 -- the staves the page prints -- with the same bars, the
same notes and the same slurs. Tightening the same crop by 40-60px instead lost
five slur tokens, because a slur's arc hangs below the staff it belongs to and a
tight edge cuts it off. Padding cost nothing on that page, so the band is given
:data:`PAD` of the page's height at each edge. It is a fraction rather than
pixels for the reason bounds themselves are: it has to mean the same thing at
whatever resolution the crop is taken.

**A failed system is a hole, not a failed song.** Twenty homr runs is twenty
chances to fail, and losing the nineteenth should not throw away the eighteen
that worked. So a band that cannot be read is recorded as a hole and the loop
carries on to the next one; the fragments already read stay on disk, and a later
run reads only what is missing. The song cannot leave ``scan`` while a hole is
open, because a score silently missing one of its systems is the failure this
whole approach exists to avoid.

That includes a **lost lease**. :mod:`heavy_slot` stops work whose slot was
handed to somebody else, and each band takes its own slot, so a lease lost during
band 7 costs band 7 and nothing else -- band 8 asks for a fresh slot of its own,
which is a request that queues behind whoever the cores went to rather than
competing with them.

**Everything here is derived, and derived things go stale.** The fragments are
derived from the bands, the grid answers from the fragments, the input score from
the fragments, and the reviewer's approval from all of it. When an input changes,
everything downstream of it stops being true -- see :func:`reconcile`, which is
the only place that idea is written down.

**The stage does not advance on its own.** Assembling a score does not move the
song to ``clean``; :func:`approve` does, and only a person calls it. This is the
opposite of how ``clean`` behaves and it is deliberate (#99): the dangerous parse
is the *tidy* one, so advancing automatically on a parse that looks fine would
skip exactly the parses most worth looking at. The OK is a claim about a
particular reading of the page, so it is recorded against :func:`revision` and
lapses the moment any system is read again -- and the content stamps it was given
are kept, so the panel can say **which** systems have changed since anybody
looked.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from lxml import etree

from src.clean_score.utils import per_system

from . import heavy_slot, omr, omr_systems, pdf_systems, state
from .pdf_systems import SystemBounds

Logger = Callable[[str], None]

#: Where a song keeps the MusicXML of each band it has read.
FRAGMENT_DIR = "scan"

#: The input score assembled from those fragments. **Derived**: regenerate it,
#: never hand-edit it -- the next scan overwrites whatever is there.
ASSEMBLED_NAME = "scanned.musicxml"

#: How far past a printed band the crop reaches, at each edge, as a fraction of
#: page height. ~2% is 60px on a 300 dpi A4, which is what #112 measured.
PAD = float(os.getenv("SCAN_BAND_PAD", "0.02"))


def _noop(_msg: str) -> None:
    pass


class ScanError(RuntimeError):
    """The song cannot be scanned at all (no PDF, no bounds)."""


# --- provenance ----------------------------------------------------------
#
# One stamp, computed the same way everywhere: what a thing was made from,
# reduced to a string that changes whenever any of it changes.


def band_stamp(band: SystemBounds, source: str, pad: float = PAD,
               dpi: int = omr_systems.SCAN_DPI) -> str:
    """What a fragment of this band was read from.

    The PDF's own version, the band's page and geometry, the padding and the
    resolution: change any of them and the crop is a different picture, so the
    MusicXML read off it is no longer an answer about what is on the page now.

    Note that the *index* is deliberately not in it. Indices are positional and
    an inserted band re-points every one after it; a stamp made of the geometry
    is what makes that shift visible instead of silent.
    """
    raw = (f"{source}:{band.page}:{band.top:.9f}:{band.bottom:.9f}"
           f":{pad:.6f}:{dpi}")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def content_stamp(path: str) -> str:
    """What was actually read off a band.

    A fragment carries two stamps and they answer different questions. The band
    stamp says *what it was read from*, and is what decides whether the fragment
    is still an answer about the page. This one says *what came back*, and is
    what everything further downstream is derived from -- the grid answers are
    about the staves in this file, and the assembled score is these files joined
    up. Re-reading a band therefore invalidates them exactly when the reading
    came out different, which is the only time anything derived from it was
    wrong.
    """
    digest = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()[:12]


def revision(song: state.Song) -> str:
    """What the whole scan currently is: every fragment's content, in order.

    A band read, re-read differently, lost or inserted all move this, which is
    what the assembled score and the reviewer's approval hang off.
    """
    fragments = _fragments(song)
    parts = [f"{index}={fragments[index].get('content') or '-'}"
             for index in sorted(fragments)]
    return hashlib.sha1(":".join(parts).encode("utf-8")).hexdigest()[:12]


def padded(band: SystemBounds, pad: float = PAD) -> SystemBounds:
    """The band with room around it, clamped to the page."""
    return SystemBounds(
        index=band.index, page=band.page,
        top=max(0.0, band.top - pad), bottom=min(1.0, band.bottom + pad),
        measure_start=band.measure_start, measure_end=band.measure_end,
    )


# --- the state a scan keeps ----------------------------------------------


def _scan(song: state.Song) -> Dict:
    return song.data.setdefault("scan", {})


def _fragments(song: state.Song) -> Dict[int, Dict]:
    """What has been read, by system index."""
    raw = song.data.get("scan", {}).get("systems", {})
    return {int(k): v for k, v in raw.items() if isinstance(v, dict)}


def _write_fragments(song: state.Song, fragments: Dict[int, Dict]) -> None:
    _scan(song)["systems"] = {str(k): v for k, v in sorted(fragments.items())}


def pages_without_bands(song: state.Song,
                        bands: Optional[Sequence[SystemBounds]] = None) -> List[int]:
    """Pages of the PDF nobody has drawn a system on.

    Scanning reads the bands and nothing else, so a page with none is a page that
    would be silently left out of the score. The panel disables its Scan button on
    this and :func:`run`'s caller refuses on it.

    A page count needs poppler. Without it this answers "no gaps" rather than
    "every page is a gap": a missing binary must not be indistinguishable from an
    operator who has not drawn the bands yet.
    """
    pdf = song.source_path("pdf")
    if not pdf or not os.path.exists(pdf):
        return []
    try:
        total = pdf_systems.page_count(pdf)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return []
    drawn = {b.page for b in (pdf_systems.load_bounds(song.dir)
                              if bands is None else bands)}
    return [page for page in range(1, total + 1) if page not in drawn]


def status(song: state.Song) -> Dict:
    """What the scan stage has and has not got, for the app to show and act on.

    ``complete`` is the gate on assembling: every printed band read, at least one
    band, and an assembled score that matches what was read. ``approved`` is the
    gate on *leaving* the stage, and it is a separate thing: complete says the app
    has a score, approved says a person has looked at it.
    """
    bands = pdf_systems.load_bounds(song.dir)
    fragments = _fragments(song)
    holes = [b.index for b in bands
             if b.index not in fragments or fragments[b.index].get("error")]
    scan = song.data.get("scan", {})
    assembled = scan.get("assembled")
    current = revision(song)
    ok = scan.get("ok") or {}
    # What each system read as when it was last approved. A system missing from
    # it, or reading differently now, is one nobody has looked at -- which is a
    # hint about where to look, not a second gate.
    seen = ok.get("systems") or {}
    return {
        "systems": len(bands),
        "read": sum(1 for f in fragments.values() if not f.get("error")),
        "holes": holes,
        "errors": {str(i): fragments[i].get("error") for i in sorted(fragments)
                   if fragments[i].get("error")},
        "assembled": assembled,
        "complete": bool(bands) and not holes
        and scan.get("assembled_revision") == current,
        "revision": current,
        "pages_without_bands": pages_without_bands(song, bands),
        "approved": bool(ok.get("revision")) and ok["revision"] == current,
        "ever_approved": bool(ok),
        "new_since_ok": [i for i in sorted(fragments)
                         if not fragments[i].get("error")
                         and seen.get(str(i)) != fragments[i].get("content")]
        if ok else [],
    }


def fragment_path(song: state.Song, index: int) -> Optional[str]:
    """The MusicXML read off one band, or None while that system is a hole."""
    entry = _fragments(song).get(int(index)) or {}
    name = entry.get("musicxml")
    if not name:
        return None
    path = song.path(name)
    return path if os.path.exists(path) else None


def approve(song: state.Song) -> Dict:
    """Record that a person looked at this reading of the page, and move on.

    The one explicit OK per song. It is recorded against the revision it approved
    and against each system's content, so re-reading a system both lapses it and
    says which system did it.
    """
    result = status(song)
    if not result["complete"]:
        raise ScanError(
            "This scan is not finished: system(s) "
            f"{', '.join(str(i) for i in result['holes']) or 'none'} still need "
            "reading, so there is nothing whole to approve."
        )
    fragments = _fragments(song)
    _scan(song)["ok"] = {
        "revision": result["revision"],
        "systems": {str(i): f.get("content") for i, f in sorted(fragments.items())},
    }
    song.set_stage("clean")
    song.save()
    return status(song)


# --- the invalidation rule -----------------------------------------------
#
# There is one idea here and it is worth stating before the code: **when an
# input changes, everything derived from it stops being true, and the app says
# so.** A bounds edit throwing away fragments, a re-scanned system throwing away
# its grid answers, and a re-scan clearing the reviewer's approval look like
# three special cases. They are three links of one chain, and writing them as
# three would be how a fourth comes to be forgotten.


@dataclass(frozen=True)
class Derived:
    """One thing the app computed, and the thing it computed it from.

    ``made_from`` is the stamp of the input **as it is now**; ``recorded`` is the
    stamp that was true when the derived thing was made. They differ exactly when
    the derived thing has stopped being true, and then ``discard`` takes it away.
    """

    what: str
    made_from: Callable[[state.Song], Optional[str]]
    recorded: Callable[[state.Song], Optional[str]]
    discard: Callable[[state.Song], None]


def _chain(song: state.Song, bands: Sequence[SystemBounds],
           source: str) -> List[Derived]:
    """Everything this song derived, in the order it was derived in.

    The order is the whole cascade. A fragment discarded by an earlier row is
    already gone when the answers row asks what it was answered against, so the
    answers go too; both are gone when the assembly asks. Nothing has to know it
    is downstream of anything.
    """
    by_index = {b.index: b for b in bands}
    rows: List[Derived] = []
    for index in sorted(set(_fragments(song)) | set(by_index)):
        band = by_index.get(index)
        rows.append(Derived(
            what=f"the scan of system {index}",
            made_from=(lambda _s, b=band: band_stamp(b, source) if b else None),
            recorded=(lambda s, i=index: _fragments(s).get(i, {}).get("band")),
            discard=(lambda s, i=index: _drop_fragment(s, i)),
        ))
        rows.append(Derived(
            # Same staff count in a different order is the dangerous case: the
            # grid reads as answered and every answer points at the wrong staff.
            what=f"the grid answers for system {index}",
            made_from=(lambda s, i=index: _fragments(s).get(i, {}).get("content")),
            recorded=(lambda s, i=index: _answered(s).get(str(i))),
            discard=(lambda s, i=index: _drop_answers(s, i)),
        ))
    rows.append(Derived(
        what="the assembled input score",
        made_from=revision,
        recorded=(lambda s: s.data.get("scan", {}).get("assembled_revision")),
        discard=_drop_assembled,
    ))
    rows.append(Derived(
        what="the reviewer's approval",
        made_from=revision,
        recorded=(lambda s: s.data.get("review", {}).get("scan_revision")),
        discard=_drop_approval,
    ))
    return rows


def reconcile(song: state.Song) -> List[str]:
    """Discard everything this song derived from an input that has since changed.

    Returns what was discarded, in words, so the app can say it rather than
    quietly doing it. Saves the song only when something actually changed, so
    this is safe to call on every read.

    A song with no scan derives nothing from any of this, which is what keeps the
    48 songs that predate the stage out of it entirely.
    """
    if "scan" not in song.data:
        return []
    bands = pdf_systems.load_bounds(song.dir)
    source = pdf_systems.file_version(song.source_path("pdf") or "")
    dropped: List[str] = []
    for row in _chain(song, bands, source):
        recorded = row.recorded(song)
        if recorded is None:
            continue           # never made; there is nothing to stop being true
        if recorded != row.made_from(song):
            row.discard(song)
            dropped.append(row.what)
    if dropped:
        song.save()
    return dropped


def _answered(song: state.Song) -> Dict[str, str]:
    return song.data.get("scan", {}).get("answered_against", {})


def stamp_answers(song: state.Song, indices: Sequence[int]) -> None:
    """Record which fragment each system's grid answers were answered against."""
    if "scan" not in song.data:
        return
    fragments = _fragments(song)
    against = _scan(song).setdefault("answered_against", {})
    for index in indices:
        stamp = fragments.get(int(index), {}).get("content")
        if stamp:
            against[str(int(index))] = stamp


def _drop_fragment(song: state.Song, index: int) -> None:
    fragments = _fragments(song)
    stale = fragments.pop(index, None)
    _write_fragments(song, fragments)
    # The MusicXML goes with it. Its name carries the geometry it was read from,
    # so leaving it would only make the folder harder to read, not safer.
    path = (stale or {}).get("musicxml")
    if path:
        try:
            os.remove(song.path(path))
        except OSError:
            pass


def _drop_answers(song: state.Song, index: int) -> None:
    _scan(song).get("answered_against", {}).pop(str(index), None)
    assembled = song.data.get("scan", {}).get("assembled")
    if not assembled:
        return
    answers = per_system.saved_answers(song.path(assembled))
    if answers and index in answers:
        del answers[index]
        per_system.save_answers(song.path(assembled), answers)


def _drop_assembled(song: state.Song) -> None:
    _scan(song).pop("assembled_revision", None)
    # The file is left where it is -- the previews read it, and it is about to be
    # written over by the next scan -- but the song is back on the stage that
    # produces it, because what the rest of the app would build from it now is a
    # score that is missing a system.
    song.set_stage("scan")


def _drop_approval(song: state.Song) -> None:
    song.data.pop("review", None)


# --- running a scan ------------------------------------------------------


def run(
    song: state.Song,
    log: Logger = _noop,
    only: Optional[Sequence[int]] = None,
    pad: float = PAD,
    dpi: int = omr_systems.SCAN_DPI,
    binary: Optional[str] = None,
) -> Dict:
    """Read every printed system that is not already read, and assemble.

    ``only`` re-reads named systems even if they are current -- a person looking
    at a band that came back wrong asking for it again. Nothing here throws that
    system's grid answers away: :func:`reconcile` does it afterwards, and only if
    the re-read actually came back different, which is the only case in which
    anything derived from it was wrong.

    ``binary`` reads with a homr other than the default one (:func:`omr.engines`)
    — a branch being tried against this repertoire. It is not recorded: what a
    fragment has to carry is what came back, not what produced it, and the
    comparison people actually make is re-reading one system with the other
    engine and looking at both.
    """
    pdf = song.source_path("pdf")
    if not pdf or not os.path.exists(pdf):
        raise ScanError("This song has no PDF to scan.")
    bands = pdf_systems.load_bounds(song.dir)
    if not bands:
        raise ScanError(
            "No printed systems to read. Set the system boundaries in the "
            "Systems viewer before scanning."
        )
    gaps = pages_without_bands(song, bands)
    if gaps:
        # Not a hole to be filled later: a page nobody marked is music that would
        # never be read at all, and the score would come out looking complete.
        raise ScanError(
            "Page(s) " + ", ".join(str(p) for p in gaps) + " have no printed "
            "systems marked. Mark every page in the Systems viewer before scanning."
        )

    source = pdf_systems.file_version(pdf)
    out_dir = song.path(FRAGMENT_DIR)
    os.makedirs(out_dir, exist_ok=True)
    forced = {int(i) for i in (only or ())}
    reconcile(song)

    wanted = [b for b in bands if b.index in forced] if forced else list(bands)
    log(f"Scanning {len(wanted)} of {len(bands)} printed system(s) at {dpi} dpi.")
    for band in wanted:
        stamp = band_stamp(band, source, pad, dpi)
        current = _fragments(song).get(band.index, {})
        if band.index not in forced and current.get("band") == stamp \
                and not current.get("error"):
            log(f"System {band.index}: already read.")
            continue
        _read_one(song, pdf, band, stamp, out_dir, pad, dpi, len(bands), log, binary)
        song.save()

    # Everything downstream of a fragment that just changed goes here, through
    # the same rule a bounds edit goes through. There is no separate re-scan case.
    for gone in reconcile(song):
        log(f"Discarded {gone}: what it was made from has changed.")
    return _assemble(song, log)


def _read_one(song: state.Song, pdf: str, band: SystemBounds, stamp: str,
              out_dir: str, pad: float, dpi: int, total: int, log: Logger,
              binary: Optional[str] = None) -> None:
    """Read one band, and record either its fragment or its hole.

    A lost lease is a hole like any other: each band takes its own slot, so band
    N+1 asking for a fresh one is a request that queues behind whoever the cores
    went to, rather than a second job competing with them for the cores this one
    was just told to give up.
    """
    fragments = _fragments(song)
    entry: Dict = {"index": band.index, "band": stamp,
                   "page": band.page, "top": band.top, "bottom": band.bottom}
    try:
        log(f"System {band.index} of {total}: cropping")
        image = pdf_systems.crop_systems(pdf, [padded(band, pad)], out_dir, dpi=dpi)[0]
        produced = omr_systems.read_system(image, out_dir, log=log, binary=binary)
        entry.update(
            musicxml=os.path.relpath(produced.musicxml, song.dir),
            content=content_stamp(produced.musicxml),
            staves=produced.width,
            bars=produced.bars,
            error=None,
        )
        log(f"System {band.index}: {entry['staves']} staves, {entry['bars']} bars")
    # Only the ways *reading a band* fails become holes. Everything else — a
    # missing module, a full disk, a bug here — is allowed to stop the scan and
    # be seen. Catching broadly turned one unrelated import error into fifteen
    # identical "homr could not read this band" holes and hid it completely.
    except (omr.HomrError, omr_systems.ScanError, heavy_slot.SlotLost,
            subprocess.CalledProcessError, etree.XMLSyntaxError) as exc:
        entry.update(musicxml=None, staves=0, bars=0, error=str(exc))
        log(f"System {band.index} could not be read: {exc}")
    fragments[band.index] = entry
    _write_fragments(song, fragments)


def _assemble(song: state.Song, log: Logger) -> Dict:
    """Put the fragments together, if there are no holes left.

    A hole stops this rather than being filled with silence: an assembled score
    quietly short of a system reads as a complete score and would be cleaned,
    lyricked and sung.
    """
    result = status(song)
    if result["holes"]:
        missing = ", ".join(str(i) for i in result["holes"])
        log(f"Not assembling: system(s) {missing} still need reading.")
        song.set_stage("scan")
        song.save()
        return status(song)

    fragments = _fragments(song)
    scans = [
        omr_systems.SystemScan(
            index=index,
            musicxml=song.path(fragments[index]["musicxml"]),
            staves=omr_systems.flatten(song.path(fragments[index]["musicxml"])),
        )
        for index in sorted(fragments)
    ]
    out = song.path(ASSEMBLED_NAME)
    omr_systems.assemble(scans, out)
    log(f"Assembled {len(scans)} system(s) into {ASSEMBLED_NAME}.")

    song.data.setdefault("sources", {})["xml"] = ASSEMBLED_NAME
    _scan(song)["assembled"] = ASSEMBLED_NAME
    _scan(song)["assembled_revision"] = revision(song)
    # The stage deliberately stays where it is. A finished scan is a score the app
    # has, not a score anybody has looked at, and `approve` is the only thing that
    # moves a song out of `scan` (#99). The lapse in the other direction needs no
    # code of its own: re-reading a system moves `revision`, which is what both the
    # assembly and the OK are recorded against.
    song.save()
    return status(song)
