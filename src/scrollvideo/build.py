"""Score in, practice videos out.

    .mscx --(MuseScore CLI)--> MusicXML --(verovio)--> SVG + timemap
          --(MuseScore CLI)--> MIDI     ------------->  tempo map (the clock)
          --(MuseScore CLI)--> WAV per voice

The engraving is rasterised once and reused for every voice; only the highlight
emphasis and the audio differ between them.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from bisect import bisect_left
from dataclasses import dataclass, replace
from typing import Callable, List, Optional, Sequence

import mido
import numpy as np
from lxml import etree

from . import audio as audio_mod
from . import score as score_mod
from . import spacing as spacing_mod
from .engrave import engrave
from .geometry import playing_coverage, rasterise
from .timing import (SMOOTH_SECONDS, TempoMap, note_events, rest_events,
                     scroll_anchors, smooth_scroll)
from .video import (NVIDIA_ENCODER, SOFTWARE_ENCODER, TAIL_SECONDS, mux, place,
                    preferred_encoder, render)

Logger = Callable[[str], None]

# Section repeats and voltas are fine: verovio expands them in its timemap exactly
# as MuseScore does, and `engrave` maps the repeat-pass ids back to the notes on the
# page. D.C./D.S. jumps it does *not* follow (on Jouluriemua verovio plays 181
# quarters where MuseScore plays 257.5), so those are refused. A Marker (segno,
# coda, fine) on its own is just a label and changes nothing without a Jump.
JUMP_MARKUP = ("Jump",)

# How closely the highlights must track the audio before we are willing to ship a
# video: nearly every onset within a fifth of a second.
ALIGNMENT_TOLERANCE = 0.2
ALIGNMENT_REQUIRED = 0.98

# Margin controls are adjustments to today's vertical viewport, not pretend absolute
# page margins: the hidden spacing staff means the lower edge is not Verovio's page
# edge at all. Percent keeps the result visually consistent across 4K/1080p/720p.
# Positive values add white space; negative values crop that edge and enlarge music.
MIN_MARGIN_PERCENT = -40.0
MAX_MARGIN_PERCENT = 100.0

# The name of the every-voice mix. Downstream (review, YouTube titles, delete) reads
# the part out of the file name, so this is also what the combined video is called
# there — the same name the old screen recorder used.
COMBINED = "ALL"


def _noop(_msg: str) -> None:
    pass


def video_encoder(hardware_encoding: bool, width: int, height: int) -> str:
    """Select automatic hardware encoding unless the caller explicitly disabled it."""
    return preferred_encoder(width, height) if hardware_encoding else SOFTWARE_ENCODER


def _margin_viewport(visible_height: float, top_percent: float,
                     bottom_percent: float) -> tuple[float, float]:
    """Virtual source-unit viewport for independent top/bottom margin adjustments.

    ``0, 0`` is exactly the old viewport ``0..visible_height``. Positive values
    extend the viewport into white space outside the engraving; negative values move
    the corresponding edge inward. Returning source units keeps the same adjustment
    independent of output resolution.
    """
    top = float(top_percent)
    bottom = float(bottom_percent)
    for name, value in (("top", top), ("bottom", bottom)):
        if not MIN_MARGIN_PERCENT <= value <= MAX_MARGIN_PERCENT:
            raise ValueError(
                f"{name} video margin must be between {MIN_MARGIN_PERCENT:g}% and "
                f"{MAX_MARGIN_PERCENT:g}%, got {value:g}%")
    start = -visible_height * top / 100.0
    end = visible_height * (1.0 + bottom / 100.0)
    if end <= start:
        raise ValueError("top and bottom video margins leave no visible picture")
    return start, end


def _vertical_view(array: np.ndarray, *, visible_height: float, output_height: int,
                   px_per_unit: float, start: float, end: float,
                   fill_value: int) -> np.ndarray:
    """Crop/pad a raster to the virtual vertical viewport.

    Only ``0..visible_height`` is ever copied from the engraving. That matters when
    the hidden spacer staff exists below the real score: adding bottom margin must
    reveal white, never the spacer we intentionally cropped away.
    """
    if start == 0.0 and end == visible_height:
        # Preserve the old code path byte-for-byte at the default settings.
        return array[:output_height]

    shape = (output_height, *array.shape[1:])
    out = np.full(shape, fill_value, dtype=array.dtype)
    source_start = max(0.0, start)
    source_end = min(visible_height, end)
    if source_end <= source_start:
        return out

    src0 = int(round(source_start * px_per_unit))
    src1 = int(round(source_end * px_per_unit))
    dst0 = int(round((source_start - start) * px_per_unit))
    if src1 <= src0 or dst0 >= output_height or src0 >= array.shape[0]:
        return out
    if dst0 < 0:
        src0 -= dst0
        dst0 = 0
    count = min(src1 - src0, array.shape[0] - src0, output_height - dst0)
    if count > 0:
        out[dst0:dst0 + count] = array[src0:src0 + count]
    return out


def _collect_audio_results(futures: dict, progress: Logger) -> dict:
    """Collect concurrent audio renders while reporting each completed mix.

    MuseScore audio export is often the longest quiet phase before frame rendering.
    Reporting completions rather than starts makes the number durable and truthful
    even though mixes finish out of order.
    """
    total = len(futures)
    if not total:
        return {}
    progress(f"Creating audio mixes: 0/{total} (0%)")
    names = {future: name for name, future in futures.items()}
    results = {}
    for completed, future in enumerate(as_completed(names), 1):
        name = names[future]
        results[name] = future.result()
        percent = round(completed * 100 / total)
        progress(f"Creating audio mixes: {completed}/{total} ({percent}%) — {name}")
    return results


def unsupported_repeats(root: etree._Element) -> List[str]:
    """Repeat structures this pipeline cannot follow (empty when there are none)."""
    return [tag for tag in JUMP_MARKUP if root.find(f".//{tag}") is not None]


@dataclass(frozen=True)
class Prepared:
    """Everything decided about a render before a single pixel is drawn.

    This is the whole picture except its rasterisation: the engraving, the clock,
    what lights up when, where the page has scrolled to, and which slice of it the
    frame shows. `raster` turns it into pixels — once for the video, again at a
    smaller height for the browser preview — and neither recomputes any of it, so
    the two cannot disagree about layout or timing.
    """

    source: str               # the .mscx actually rendered (silent parts dropped)
    musicxml: str
    midi: str
    engraving: object         # engrave.Engraving
    names: List[str]          # the parts that can be sung
    wanted: List[str]         # the parts asked for
    dropped: List[str]        # parts left out for having nothing to sing
    spaced: bool              # a spacer staff was engraved (and is cropped off)
    singing_staves: int
    tempo: TempoMap
    notes: List             # NoteEvent per sounding note
    rests: List             # NoteEvent per sounding rest
    events: List            # both, in the order they start
    anchors: tuple          # (seconds, x in verovio units), smoothed
    visible_height: float   # page height with the spacer staff cropped off
    view_start: float       # top of the frame, in verovio units
    view_end: float         # ... and its bottom
    duration: float         # seconds of video, including the tail past the last note

    @property
    def layout(self):
        return self.engraving.layout

    @property
    def view_height(self) -> float:
        return self.view_end - self.view_start


def prepare(mscx_path: str, tmp: str, *, parts: Optional[Sequence[str]] = None,
            keep_silent: bool = False, initial_bpm: Optional[int] = None,
            spacer_per_quarter: int = spacing_mod.DEFAULT_PER_QUARTER,
            smooth_seconds: float = SMOOTH_SECONDS, fps: int = 60,
            top_margin_percent: float = 0.0, bottom_margin_percent: float = 0.0,
            log: Logger = _noop) -> Prepared:
    """Do everything a render needs before rasterising, and check it is renderable.

    `tmp` is a directory the intermediate files are written into; it has to outlive
    the returned `Prepared`, whose `source` the audio mixes are rendered from.

    The refusals live here rather than in the renderer so that a preview fails the
    same way a render would, before either has spent any time: a D.C./D.S. jump the
    engraving cannot follow, margins that leave no picture, and — measured against
    the audio itself — a timeline too far out of step to ship.
    """
    # Validated on a unit page first, so bad margins fail before the minutes of
    # engraving rather than after them. Applied for real once the page is known.
    _margin_viewport(1.0, top_margin_percent, bottom_margin_percent)

    jumps = unsupported_repeats(etree.parse(mscx_path).getroot())
    if jumps:
        raise NotImplementedError(
            "This score uses " + ", ".join(sorted(set(jumps))) +
            " (a D.C./D.S. jump): MuseScore's audio follows the jump and the engraving "
            "does not, so the video would drift out of sync. Section repeats and voltas "
            "are supported; write the jump out in full first.")

    source, dropped = score_mod.prepare(mscx_path, tmp, keep_silent=keep_silent,
                                        initial_bpm=initial_bpm)
    if dropped:
        log(f"Leaving out {', '.join(dropped)} (no notes to sing)")

    root = etree.parse(source).getroot()
    names = audio_mod.part_names(root)
    wanted = list(parts) if parts else names
    unknown = [p for p in wanted if p not in names]
    if unknown:
        raise ValueError(
            f"No such part(s): {', '.join(unknown)}. Score has: {', '.join(names)}"
            + (f" (left out: {', '.join(dropped)})" if dropped else ""))

    log("Converting for engraving (MuseScore CLI)")
    musicxml = audio_mod.run_musescore(source, os.path.join(tmp, "score.musicxml"))
    midi = audio_mod.run_musescore(source, os.path.join(tmp, "score.mid"))

    # A rest-only staff makes measure width follow beats rather than note
    # density; it is engraved and then cropped off the bottom.
    spaced = None
    if spacer_per_quarter:
        spaced = spacing_mod.add_spacer_staff(
            musicxml, os.path.join(tmp, "spaced.musicxml"), spacer_per_quarter)
        if spaced is None:
            log("Could not build the spacer staff; engraving as-is")

    log("Engraving one continuous system (verovio)")
    eng = engrave(spaced or musicxml)
    layout = eng.layout
    singing_staves = len(layout.staff_tops) - (1 if spaced else 0)
    if singing_staves != len(names):
        log(f"Warning: {singing_staves} engraved staves vs {len(names)} parts; "
            "highlighting every voice equally.")

    tempo = TempoMap.from_midi(midi)
    notes = note_events(eng.timemap, tempo, eng.drawn_id)
    rests = rest_events(eng.timemap, tempo, eng.drawn_id)
    events = sorted([*notes, *rests], key=lambda event: (event.on, event.off))
    anchors = scroll_anchors(eng.timemap, tempo, layout, eng.drawn_id,
                             staff_limit=singing_staves)
    log(f"{len(notes)} notes, {len(rests)} rests, "
        f"{anchors[0][-1]:.1f}s on MuseScore's clock")

    # Verify against the audio itself rather than trusting the structure.
    landed = alignment(notes, midi)
    if landed < ALIGNMENT_REQUIRED:
        raise NotImplementedError(
            f"Only {landed:.0%} of the notes MuseScore plays get a highlight, so this "
            "video would look out of sync. The engraved timeline and the played one "
            "disagree — usually an unsupported repeat structure.")
    repeated = len(notes) - len(set(e.note_id for e in notes))
    if repeated:
        log(f"{repeated} notes are played more than once (repeats followed)")

    # The base visible region is exactly what the old renderer used. Margin
    # adjustments create a virtual viewport around/inside it.
    visible = spacing_mod.visible_height(layout) if spaced else layout.height
    view_start, view_end = _margin_viewport(
        visible, top_margin_percent, bottom_margin_percent)

    if smooth_seconds:
        anchors = smooth_scroll(*anchors, fps=fps, seconds=smooth_seconds,
                                page_width=layout.width)

    return Prepared(
        source=source, musicxml=musicxml, midi=midi, engraving=eng, names=names,
        wanted=wanted, dropped=dropped, spaced=bool(spaced),
        singing_staves=singing_staves, tempo=tempo, notes=notes, rests=rests,
        events=events, anchors=anchors, visible_height=visible,
        view_start=view_start, view_end=view_end,
        duration=_duration(events, layout, singing_staves))


def _duration(events: Sequence, layout, staff_limit: int) -> float:
    """Seconds of video: the last thing drawn, plus the tail the renderer keeps rolling.

    Counted over the symbols that are actually on screen — the same ones `place`
    keeps — so a preview and a render end at the same moment.
    """
    last = 0.0
    for event in events:
        geom = layout.playing(event.note_id)
        if geom is not None and layout.staff_index(geom) < staff_limit:
            last = max(last, event.off)
    return last + TAIL_SECONDS


@dataclass(frozen=True)
class Raster:
    """The engraving as pixels, already cut to the frame's height.

    A frame is a window onto `strip`, so this is the picture itself rather than a
    description of it. The video renders it at the output height; the browser
    preview asks for the same thing at a height a page can carry. Two sizes of one
    drawing, made by one call — which is the only way a preview can promise the
    render will look like it, down to which font the words came out in.
    """

    strip: np.ndarray        # RGB, `height` tall, the whole score end to end
    coverage: np.ndarray     # how much of each pixel a notehead or rest glyph covers
    placed: List             # Placed per symbol: pixel box, in strip coordinates
    px_per_unit: float
    height: int

    @property
    def width(self) -> int:
        return self.strip.shape[1]


def raster(ready: Prepared, height: int, log: Logger = _noop) -> Raster:
    """Draw `ready` at `height` pixels tall, cropped and padded to the viewport.

    Rasterising at the viewport scale is what keeps music, scroll anchors and
    highlight geometry in one coordinate system: the page is drawn taller than the
    frame by exactly the amount the margins and the spacer staff take away, and
    `_vertical_view` then cuts the frame out of it.
    """
    layout = ready.layout
    strip_height = int(round(height * layout.height / ready.view_height))
    px_per_unit = strip_height / layout.height

    log("Rasterising the strip")
    view = dict(visible_height=ready.visible_height, output_height=height,
                px_per_unit=px_per_unit, start=ready.view_start, end=ready.view_end)
    strip = _vertical_view(rasterise(ready.engraving.svg, layout, strip_height),
                           fill_value=255, **view)
    coverage = _vertical_view(playing_coverage(ready.engraving.svg, layout, strip_height),
                              fill_value=0, **view)

    placed = place(ready.events, layout, px_per_unit, staff_limit=ready.singing_staves)
    y_offset = int(round(-ready.view_start * px_per_unit))
    if y_offset:
        placed = [replace(p, y0=p.y0 + y_offset, y1=p.y1 + y_offset) for p in placed]
    return Raster(strip=strip, coverage=coverage, placed=placed,
                  px_per_unit=px_per_unit, height=height)


def midi_onsets(midi_path: str) -> List[float]:
    """Every moment MuseScore actually strikes a note, in seconds."""
    onsets, now = [], 0.0
    for msg in mido.MidiFile(midi_path):
        now += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            onsets.append(now)
    return sorted(set(onsets))


def alignment(events: Sequence, midi_path: str) -> float:
    """Fraction of the notes MuseScore plays that something on screen lights up for.

    Measured in this direction on purpose. The reverse — every highlight matching a
    played note — punishes tie continuations, which correctly light the tied
    notehead while the sound carries on and so have no note-on of their own. What
    would actually look broken is audio with no highlight, and that is what this
    catches: a timeline the engraving does not follow leaves whole stretches of the
    music unlit.
    """
    played = midi_onsets(midi_path)
    if not played or not events:
        return 0.0
    onsets = sorted(e.on for e in events)
    hits = 0
    for moment in played:
        i = bisect_left(onsets, moment)
        near = onsets[max(0, i - 1):i + 1]
        if any(abs(moment - o) <= ALIGNMENT_TOLERANCE for o in near):
            hits += 1
    return hits / len(played)


def build_videos(mscx_path: str, out_dir: str, *, parts: Optional[Sequence[str]] = None,
                 height: int = 2160, width: int = 3840, fps: int = 60,
                 with_audio: bool = True, keep_silent: bool = False,
                 emphasise: bool = False, combined: bool = True,
                 spacer_per_quarter: int = spacing_mod.DEFAULT_PER_QUARTER,
                 smooth_seconds: float = SMOOTH_SECONDS,
                 top_margin_percent: float = 0.0,
                 bottom_margin_percent: float = 0.0,
                 basename: Optional[str] = None,
                 initial_bpm: Optional[int] = None,
                 hardware_encoding: bool = True,
                 audio_cache_dir: Optional[str] = None,
                 log: Logger = _noop, progress: Logger = _noop) -> List[str]:
    """Render a scrolling video per voice. Returns the paths written.

    The picture is the same for every voice, so by default it is encoded once and
    each voice is that video with its own mix muxed on (a stream copy). Encoding
    dominates the cost — 18s of a 21s render — so this is roughly 3x faster for a
    four-part score. `emphasise=True` instead re-renders per voice with that
    voice's notes lit brighter than the rest, which costs a full encode each.

    ``top_margin_percent`` and ``bottom_margin_percent`` adjust the current vertical
    viewport independently. Zero preserves the historical layout exactly; positive
    values add white space and negative values crop that edge.

    `combined` also writes "<base> ALL", the same picture with every voice at equal
    volume — what a singer listens to once they know their own line, and what the
    old screen recorder always produced.
    """
    # Checked before the work, not at the encode: engraving, rasterising and the
    # audio mixes take minutes, and failing at the end with a bare FileNotFoundError
    # from Popen wastes all of it.
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not on PATH — it renders the video. "
                           "macOS: brew install ffmpeg")

    # Outputs are named "<base> <part>": the song app passes its slug so the files
    # match what the review and upload stages already look for.
    base = basename or os.path.splitext(os.path.basename(mscx_path))[0]
    os.makedirs(out_dir, exist_ok=True)
    written: List[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        # Everything up to the pixels, and every refusal, is decided here — the same
        # call the browser preview makes, so what a singer previews is what renders.
        ready = prepare(mscx_path, tmp, parts=parts, keep_silent=keep_silent,
                        initial_bpm=initial_bpm, spacer_per_quarter=spacer_per_quarter,
                        smooth_seconds=smooth_seconds, fps=fps,
                        top_margin_percent=top_margin_percent,
                        bottom_margin_percent=bottom_margin_percent, log=log)
        source = ready.source
        layout = ready.layout
        names, wanted = ready.names, ready.wanted
        anchors = ready.anchors

        # The same call the browser preview makes, at the output height instead of a
        # page-sized one.
        drawn = raster(ready, height, log)
        strip, coverage, placed = drawn.strip, drawn.coverage, drawn.placed
        px_per_unit = drawn.px_per_unit

        # Each output is (file name, the voice its mix leans on). "ALL" leans on no
        # voice: render_mix(focus=None) is already the even mix, and render() with no
        # focus_staff already lights every voice equally. Asking for one part is asking
        # for one video, so ALL only rides along when the whole score was asked for.
        mixes = [(name, name) for name in wanted]
        if combined and with_audio and len(wanted) > 1:
            mixes.append((COMBINED, None))

        tracks: dict = {}
        if with_audio:
            log(f"Preparing {len(mixes)} audio mix(es)")
            with ThreadPoolExecutor(max_workers=min(4, len(mixes))) as pool:
                if audio_cache_dir:
                    futures = {name: pool.submit(audio_mod.render_mix_cached, source, focus,
                                                 audio_cache_dir)
                               for name, focus in mixes}
                    results = _collect_audio_results(futures, progress)
                    tracks = {name: result[0] for name, result in results.items()}
                    reused = sum(result[1] for result in results.values())
                    if reused:
                        log(f"Reused {reused} unchanged audio mix(es)")
                    audio_mod.prune_mix_cache(audio_cache_dir, set(tracks.values()))
                else:
                    futures = {name: pool.submit(audio_mod.render_mix, source, focus,
                                                 os.path.join(tmp, f"{name}.wav"))
                               for name, focus in mixes}
                    tracks = _collect_audio_results(futures, progress)

        encoder = video_encoder(hardware_encoding, width, height)
        if encoder == NVIDIA_ENCODER:
            log("Using NVIDIA hardware video encoding")
        else:
            log("Using software video encoding")

        if emphasise:
            for name, focus in mixes:
                staff = (names.index(focus)
                         if focus and len(layout.staff_tops) == len(names) else None)
                log(f"{name}: rendering video (emphasised)")
                out = os.path.join(out_dir, f"{base} {name}.mp4")
                render(strip, placed, anchors, out, px_per_unit=px_per_unit, width=width,
                       fps=fps, focus_staff=staff, audio_path=tracks.get(name),
                       coverage=coverage, progress=progress, encoder=encoder)
                written.append(out)
                log(f"{name}: wrote {os.path.basename(out)}")
            return written

        log("Rendering the video (once, shared by every voice)")
        shared = os.path.join(tmp, "shared.mp4")
        render(strip, placed, anchors, shared, px_per_unit=px_per_unit, width=width, fps=fps,
               coverage=coverage, progress=progress, encoder=encoder)
        for name, _focus in mixes:
            out = os.path.join(out_dir, f"{base} {name}.mp4")
            if tracks.get(name):
                mux(shared, tracks[name], out)
            else:
                shutil.copyfile(shared, out)
            written.append(out)
            log(f"{name}: wrote {os.path.basename(out)}")

    return written
