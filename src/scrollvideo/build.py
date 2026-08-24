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
from concurrent.futures import ThreadPoolExecutor
from bisect import bisect_left
from typing import Callable, List, Optional, Sequence

import mido
from lxml import etree

from . import audio as audio_mod
from . import score as score_mod
from . import spacing as spacing_mod
from .engrave import engrave
from .geometry import playing_coverage, rasterise
from .timing import (SMOOTH_SECONDS, TempoMap, note_events, rest_events,
                     scroll_anchors, smooth_scroll)
from .video import mux, place, render

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

# The name of the every-voice mix. Downstream (review, YouTube titles, delete) reads
# the part out of the file name, so this is also what the combined video is called
# there — the same name the old screen recorder used.
COMBINED = "ALL"


def _noop(_msg: str) -> None:
    pass


def unsupported_repeats(root: etree._Element) -> List[str]:
    """Repeat structures this pipeline cannot follow (empty when there are none)."""
    return [tag for tag in JUMP_MARKUP if root.find(f".//{tag}") is not None]


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
                 basename: Optional[str] = None,
                 log: Logger = _noop) -> List[str]:
    """Render a scrolling video per voice. Returns the paths written.

    The picture is the same for every voice, so by default it is encoded once and
    each voice is that video with its own mix muxed on (a stream copy). Encoding
    dominates the cost — 18s of a 21s render — so this is roughly 3x faster for a
    four-part score. `emphasise=True` instead re-renders per voice with that
    voice's notes lit brighter than the rest, which costs a full encode each.

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

    root = etree.parse(mscx_path).getroot()
    jumps = unsupported_repeats(root)
    if jumps:
        raise NotImplementedError(
            "This score uses " + ", ".join(sorted(set(jumps))) +
            " (a D.C./D.S. jump): MuseScore's audio follows the jump and the engraving "
            "does not, so the video would drift out of sync. Section repeats and voltas "
            "are supported; write the jump out in full first.")

    # Outputs are named "<base> <part>": the song app passes its slug so the files
    # match what the review and upload stages already look for.
    base = basename or os.path.splitext(os.path.basename(mscx_path))[0]
    os.makedirs(out_dir, exist_ok=True)
    written: List[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        source, dropped = score_mod.prepare(mscx_path, tmp, keep_silent=keep_silent)
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

        # Rasterise tall enough that the picture still fills `height` once the
        # spacer staff at the bottom is cropped away.
        visible = spacing_mod.visible_height(layout) if spaced else layout.height
        strip_height = int(round(height * layout.height / visible))
        px_per_unit = strip_height / layout.height

        log("Rasterising the strip")
        strip = rasterise(eng.svg, layout, strip_height)[:height]
        coverage = playing_coverage(eng.svg, layout, strip_height)[:height]
        placed = place(events, layout, px_per_unit, staff_limit=singing_staves)

        if smooth_seconds:
            anchors = smooth_scroll(*anchors, fps=fps, seconds=smooth_seconds,
                                    page_width=layout.width)

        # Each output is (file name, the voice its mix leans on). "ALL" leans on no
        # voice: render_mix(focus=None) is already the even mix, and render() with no
        # focus_staff already lights every voice equally. Asking for one part is asking
        # for one video, so ALL only rides along when the whole score was asked for.
        mixes = [(name, name) for name in wanted]
        if combined and with_audio and len(wanted) > 1:
            mixes.append((COMBINED, None))

        tracks: dict = {}
        if with_audio:
            log(f"Rendering {len(mixes)} audio mix(es)")
            with ThreadPoolExecutor(max_workers=min(4, len(mixes))) as pool:
                futures = {name: pool.submit(audio_mod.render_mix, source, focus,
                                             os.path.join(tmp, f"{name}.wav"))
                           for name, focus in mixes}
                tracks = {name: future.result() for name, future in futures.items()}

        if emphasise:
            for name, focus in mixes:
                staff = (names.index(focus)
                         if focus and len(layout.staff_tops) == len(names) else None)
                log(f"{name}: rendering video (emphasised)")
                out = os.path.join(out_dir, f"{base} {name}.mp4")
                render(strip, placed, anchors, out, px_per_unit=px_per_unit, width=width,
                       fps=fps, focus_staff=staff, audio_path=tracks.get(name),
                       coverage=coverage)
                written.append(out)
                log(f"{name}: wrote {os.path.basename(out)}")
            return written

        log("Rendering the video (once, shared by every voice)")
        shared = os.path.join(tmp, "shared.mp4")
        render(strip, placed, anchors, shared, px_per_unit=px_per_unit, width=width, fps=fps,
               coverage=coverage)
        for name, _focus in mixes:
            out = os.path.join(out_dir, f"{base} {name}.mp4")
            if tracks.get(name):
                mux(shared, tracks[name], out)
            else:
                shutil.copyfile(shared, out)
            written.append(out)
            log(f"{name}: wrote {os.path.basename(out)}")

    return written
