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

**A fragment says which homr read it, and that record invalidates nothing.**
This pull request proposes it (#154, #157). The identity goes into the MusicXML
itself as well as into the song's state, because a fragment is routinely read
straight off disk by something that never opens the app — that is exactly how
#129 came to spend a session diagnosing a defect that had already been fixed.
It is provenance and not a stamp: :func:`content_stamp` steps over it, so
upgrading homr discards no fragment, drops no grid answer and takes away no
reviewer's approval. Re-reading stays what it already is, a person pressing a
button. Fragments read before this read as **unknown**, which is the true value
rather than a gap.

**A bar the boundary straightened is written down for a person to read.**
:func:`omr.split_measure_rests` moves a whole-measure rest homr put in a sung
voice, and :func:`_record_repairs` puts one ``text`` entry per move into the
song's ``fixes.json``, which the Fix panel lists as outstanding. Both halves or
neither: cleaning pads what the repair leaves behind, health then goes quiet,
and a loudly wrong bar becomes a quietly wrong one — this project's named
failure mode, occurring inside a repair.

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

import dataclasses
import hashlib
import os
import subprocess
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from lxml import etree

from src.clean_score.utils import per_system

from . import health, heavy_slot, omr, omr_systems, pdf_systems, state
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

    **Which homr read it is deliberately not part of this.** The fragment
    carries that identity as a comment line (:func:`omr.stamp_provenance`) and
    it is stepped over here, because it is provenance and not a stamp: the same
    crop read again by a newer homr with the same result is the same reading,
    and charging a person their grid answers and their approval for an upgrade
    is the hours of re-reading #154 decided against. Stripping it textually also
    keeps every stamp already recorded on this host unchanged — a file with no
    such line hashes exactly as it always did.
    """
    try:
        with open(path, "rb") as f:
            data = omr.strip_provenance(f.read())
    except OSError:
        return ""
    return hashlib.sha1(data).hexdigest()[:12]


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

    ``homr`` says which homr read each system and ``homr_now`` which one this host
    would use today, so the panel can show both. Neither gates anything: a system
    read by an older homr is still read, and an approval made under one is still
    an approval (#154).
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
        "homr": {str(i): fragments[i].get("homr") for i in sorted(fragments)
                 if not fragments[i].get("error")},
        "homr_now": current_homr(),
        "new_since_ok": [i for i in sorted(fragments)
                         if not fragments[i].get("error")
                         and seen.get(str(i)) != fragments[i].get("content")]
        if ok else [],
        "findings": findings_by_system(song),
    }


def findings_by_system(song: state.Song) -> Optional[Dict[str, int]]:
    """How many health findings landed in each printed system, or ``None``.

    The verdict on a parse is not knowable here -- it comes off the cleaned score,
    two stages along, and this was measured rather than assumed (see the pull
    request: on the only two scanned songs on this host, a scan-time count of bars
    whose voices disagree ranked the known-bad song *below* the other one, so a
    verdict said here would have been a guess dressed as a reading).

    What is knowable here, once a song has been cleaned at least once, is *where*
    the findings fell -- and this is the one screen with a button that re-reads a
    system. So the findings are carried back and attributed by bar.

    ``None`` rather than zeros whenever the attribution cannot be trusted: no
    cleaning yet, a hole, or a cleaned score that is not the length the fragments
    add up to. A wrong system number sends somebody to re-read music that was read
    correctly, which is worse than saying nothing.
    """
    fragments = _fragments(song)
    bands = pdf_systems.load_bounds(song.dir)
    if not bands or not fragments:
        return None
    issues = [i for i in (song.data.get("health", {}).get("issues") or [])
              if i.get("status") == "open"]
    cleaned = song.cleaned_path()
    if not cleaned or not os.path.exists(cleaned):
        return None
    # Cleaning does not renumber bars, so the fragments' own lengths lay the score
    # out -- but only if they still add up to it. They do not after a per-system
    # rebuild that dropped a bar, and then every number below would be off by one
    # from that point on.
    lengths = []
    for band in bands:
        entry = fragments.get(band.index) or {}
        if entry.get("error") or not entry.get("bars"):
            return None
        lengths.append(int(entry["bars"]))
    try:
        if sum(lengths) != health.score_bars(cleaned):
            return None
    except (OSError, etree.XMLSyntaxError):
        return None
    counts = {str(b.index): 0 for b in bands}
    start = 1
    ranges = []
    for band, length in zip(bands, lengths):
        ranges.append((band.index, start, start + length - 1))
        start += length
    for issue in issues:
        weight = int(issue.get("collapsed") or 1)
        measures = ([int(m) for m in issue["collapsed_measures"]]
                    if issue.get("collapsed_measures")
                    else ([int(issue["measure"])] if issue.get("measure") is not None else []))
        if not measures:
            continue
        # A collapsed row stands for more findings than it names bars, so its weight
        # is shared out over the bars it does name rather than landing on the first.
        share = weight / len(measures)
        for measure in measures:
            for index, first, last in ranges:
                if first <= measure <= last:
                    counts[str(index)] += share
                    break
    return {k: int(round(v)) for k, v in counts.items()}


def current_homr() -> Optional[Dict]:
    """The homr this host would read a page with today, or ``None`` without one.

    It is what a fragment's own record is held against, and nothing more than
    that: an upgrade is a thing the panel says, never a thing it acts on.
    """
    try:
        return omr.provenance(omr.default_engine()) or None
    except OSError:
        return None


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
    engine: Optional[omr.Engine] = None,
) -> Dict:
    """Read every printed system that is not already read, and assemble.

    ``only`` re-reads named systems even if they are current -- a person looking
    at a band that came back wrong asking for it again. Nothing here throws that
    system's grid answers away: :func:`reconcile` does it afterwards, and only if
    the re-read actually came back different, which is the only case in which
    anything derived from it was wrong.

    ``engine`` reads with a homr other than the installed one (:func:`omr.engines`)
    — a working copy of the fork being tried against this repertoire. Whichever
    one runs, **what it was is recorded on the fragment it produced** — in the
    MusicXML itself and in the song's state — and recorded is all it is: it
    invalidates nothing, here or in :func:`reconcile`.
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
        _read_one(song, pdf, band, stamp, out_dir, pad, dpi, len(bands), log, engine)
        song.save()

    # Everything downstream of a fragment that just changed goes here, through
    # the same rule a bounds edit goes through. There is no separate re-scan case.
    for gone in reconcile(song):
        log(f"Discarded {gone}: what it was made from has changed.")
    return _assemble(song, log)


def _read_one(song: state.Song, pdf: str, band: SystemBounds, stamp: str,
              out_dir: str, pad: float, dpi: int, total: int, log: Logger,
              engine: Optional[omr.Engine] = None) -> None:
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
        produced = omr_systems.read_system(image, out_dir, log=log, engine=engine)
        entry.update(
            musicxml=os.path.relpath(produced.musicxml, song.dir),
            content=content_stamp(produced.musicxml),
            # Which homr read it, taken back out of the file rather than from
            # the engine argument, so the two accounts of it cannot disagree —
            # and `None` when whatever ran left no line, which is a fragment
            # reading as unknown rather than a gap.
            homr=omr.read_provenance(produced.musicxml),
            staves=produced.width,
            bars=produced.bars,
            error=None,
        )
        log(f"System {band.index}: {entry['staves']} staves, {entry['bars']} bars")
        moved = [dataclasses.asdict(one) for one in produced.moved_rests]
    # Only the ways *reading a band* fails become holes. Everything else — a
    # missing module, a full disk, a bug here — is allowed to stop the scan and
    # be seen. Catching broadly turned one unrelated import error into fifteen
    # identical "homr could not read this band" holes and hid it completely.
    except (omr.HomrError, omr_systems.ScanError, heavy_slot.SlotLost,
            subprocess.CalledProcessError, etree.XMLSyntaxError) as exc:
        entry.update(musicxml=None, staves=0, bars=0, error=str(exc))
        log(f"System {band.index} could not be read: {exc}")
        moved = []
    fragments[band.index] = entry
    _write_fragments(song, fragments)
    # After the fragment is on disk, so a `fixes.json` somebody has broken by hand
    # costs the record rather than the reading. Written for a clean read too, with
    # nothing to say: that is what takes an old sentence away once a re-read stops
    # moving anything.
    _record_repairs(song, band.index, moved, log)


def _record_repairs(song: state.Song, index: int, moved: List[Dict],
                    log: Logger) -> None:
    """Write what the whole-rest repair moved in this system into `fixes.json`.

    The repair itself is `omr.split_measure_rests`, at the boundary, and this is
    its other half: a bar the app quietly straightened is a bar somebody has to
    read against the page, and the Fix panel is where they are told. See
    `pipeline.record_scan_repairs` for why it is a sentence and not a replayable
    entry.

    A `fixes.json` that cannot be read at all is said out loud and skipped rather
    than allowed to stop a scan twenty bands long -- the reading is the expensive
    thing here, and cleaning is where a broken file is refused properly.
    """
    from . import pipeline  # local: pipeline pulls in clean_score, scan does not

    try:
        written = pipeline.record_scan_repairs(song.dir, index, moved)
    except (RuntimeError, OSError) as exc:
        log(f"System {index}: could not record the moved rest(s) in fixes.json: {exc}")
        return
    if written:
        log(f"System {index}: recorded {written} moved whole-measure rest(s) in "
            "fixes.json — the Fix stage lists them as outstanding.")


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
