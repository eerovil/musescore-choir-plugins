#!/usr/bin/env python3
"""
Render scrolling practice videos from a cleaned score — one per voice, with the
currently sounding notes highlighted. No MuseScore GUI and no screen recording:
the engraving comes from verovio, the clock from MuseScore's MIDI export, and
the audio from the MuseScore CLI.

Usage:
  ./scroll_video.py songs/MySong/MySong_cleaned.mscx
  ./scroll_video.py score.mscx -o out/ --parts S1 A1 --height 1080 --fps 30
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

from src.scrollvideo import build_videos

load_dotenv(".env") or load_dotenv(".env.default")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render a scrolling, note-highlighting practice video per voice.")
    parser.add_argument("score", help="cleaned .mscx score")
    parser.add_argument("-o", "--out-dir", default=None,
                        help="output directory (default: alongside the score, in media/scroll)")
    parser.add_argument("--parts", nargs="+", default=None,
                        help="only these part names (default: every part)")
    parser.add_argument("--height", type=int, default=2160, help="video height (default 2160, i.e. 4K)")
    parser.add_argument("--width", type=int, default=None,
                        help="video width (default: 16:9 for the chosen height)")
    parser.add_argument("--fps", type=int, default=60, help="frames per second (default 60)")
    parser.add_argument("--no-audio", action="store_true", help="video only, no audio mix")
    parser.add_argument("--keep-silent", action="store_true",
                        help="keep click/percussion staves (dropped by default)")
    parser.add_argument("--spacer", type=int, default=2, choices=[0, 1, 2, 4, 8],
                        metavar="N",
                        help="rests per quarter in the hidden spacing staff, which makes "
                             "measure width follow beats (0 disables; default 2 = eighths)")
    parser.add_argument("--smooth", type=float, default=2.0, metavar="SECONDS",
                        help="seconds to average the scroll speed over (0 disables)")
    parser.add_argument("--emphasise", action="store_true",
                        help="light each voice's own notes brighter; re-encodes per voice (slow)")
    args = parser.parse_args()

    out_dir = args.out_dir or os.path.join(os.path.dirname(os.path.abspath(args.score)),
                                           "media", "scroll")
    try:
        # Height alone should give a sensible video: setting only --height used to
        # keep the 4K width and produce a 32:9 letterbox.
        width = args.width or round(args.height * 16 / 9 / 2) * 2
        written = build_videos(args.score, out_dir, parts=args.parts, height=args.height,
                               width=width, fps=args.fps, with_audio=not args.no_audio,
                               keep_silent=args.keep_silent, emphasise=args.emphasise,
                               spacer_per_quarter=args.spacer, smooth_seconds=args.smooth,
                               log=lambda m: print(m, flush=True))
    except NotImplementedError as exc:
        sys.exit(f"Unsupported score: {exc}")
    print(f"\nWrote {len(written)} video(s) to {out_dir}")


if __name__ == "__main__":
    main()
