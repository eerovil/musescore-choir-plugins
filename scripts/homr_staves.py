"""Report where the staves are on a page image, as JSON on stdout.

This runs **inside homr's own venv**, not the app's — it imports homr. The app
calls it as a subprocess the same way :mod:`src.song_app.omr` calls homr itself,
and for the same reason: homr is 660 MB of wheels and weights that must not
become a pip requirement of the app.

It stops where homr's own pipeline stops caring about pixels: segmentation, the
symbol bounding boxes, and ``detect_staff``. Nothing here parses any music, so a
page costs a segmentation pass (seconds) rather than a full read (~30s).

**autocrop is deliberately skipped.** homr's own entry point runs it first,
because homr's input may be a photograph of a page lying on a desk. Ours never
is — it is a page this program rasterised out of a PDF — so there is no page to
find inside the image, and skipping it keeps every y coordinate a plain scale of
the original page. autocrop returns the cropped image and not where it cut, so
leaving it in would mean guessing the offset back.

Output (one JSON object):

    {"width": w, "height": h, "staves": [{"top":, "bottom":, "left":, "right":}],
     "bar_lines": [{"top":, "bottom":, "left":, "right":}]}

All coordinates are **fractions** of the image, so they mean the same thing at
any resolution — the units ``.systems.json`` already uses.
"""

import argparse
import json
import sys

import cv2
import numpy as np

from homr import color_adjust
from homr.bar_line_detection import detect_bar_lines
from homr.debug import Debug
from homr.main import get_predictions, predict_symbols
from homr.noise_filtering import filter_predictions
from homr.note_detection import combine_noteheads_with_stems
from homr.resize import resize_image
from homr.staff_detection import break_wide_fragments, detect_staff, make_lines_stronger


def _box(top: float, bottom: float, left: float, right: float,
         height: int, width: int) -> dict:
    return {
        "top": float(top) / height,
        "bottom": float(bottom) / height,
        "left": float(left) / width,
        "right": float(right) / width,
    }


def find_staves(image_path: str, use_gpu: bool = False) -> dict:
    image = cv2.imread(image_path)
    if image is None:
        raise SystemExit(f"Could not read {image_path}")
    image = resize_image(image)
    preprocessed = color_adjust.apply_clahe(image)
    predictions = get_predictions(image, preprocessed, image_path, False, use_gpu)
    debug = Debug(predictions.original, image_path, False)
    predictions = filter_predictions(predictions, debug)
    predictions.staff = make_lines_stronger(predictions.staff, (1, 2))

    symbols = predict_symbols(debug, predictions)
    symbols.staff_fragments = break_wide_fragments(symbols.staff_fragments)

    noteheads_with_stems = combine_noteheads_with_stems(symbols.noteheads, symbols.stems_rest)
    if not noteheads_with_stems:
        raise SystemExit("No noteheads found")
    average_note_head_height = float(
        np.median([n.notehead.size[1] for n in noteheads_with_stems])
    )
    all_noteheads = [n.notehead for n in noteheads_with_stems]
    all_stems = [n.stem for n in noteheads_with_stems if n.stem is not None]
    bar_lines_or_rests = [
        line for line in symbols.bar_lines
        if not line.is_overlapping_with_any(all_noteheads)
        and not line.is_overlapping_with_any(all_stems)
    ]
    bar_line_boxes = detect_bar_lines(bar_lines_or_rests, average_note_head_height)

    staves = detect_staff(
        debug, predictions.staff, symbols.staff_fragments, symbols.clefs_keys, bar_line_boxes
    )

    height, width = predictions.staff.shape[:2]
    return {
        "width": width,
        "height": height,
        "staves": [
            _box(s.min_y, s.max_y, s.min_x, s.max_x, height, width)
            for s in staves
        ],
        "bar_lines": [
            _box(b.center[1] - b.size[1] / 2, b.center[1] + b.size[1] / 2,
                 b.center[0] - b.size[0] / 2, b.center[0] + b.size[0] / 2,
                 height, width)
            for b in bar_line_boxes
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--gpu", action="store_true",
                        help="run segmentation on the GPU (this host cannot; see omr.py)")
    args = parser.parse_args()
    json.dump(find_staves(args.image, args.gpu), sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
