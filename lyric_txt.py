#!/usr/bin/env python3
"""
Export or import lyrics between MuseScore .mscx and a plain TXT format.
Uses XML slur info: only the first note of a slur gets a token.

Usage:
  Export:  python lyric_txt.py export score.mscx -o lyrics.txt
  Import:  python lyric_txt.py import lyrics.txt score.mscx -o score_updated.mscx
  In-place: python lyric_txt.py import lyrics.txt score.mscx  (overwrites score.mscx)
"""

import argparse
import os
import sys

# Run from repo root: src is the package parent
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.clean_score.lyric_txt import export_file, import_file


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export or import lyrics between .mscx and TXT (measure blocks, staff lines)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help="export or import")

    # export: mscx -> txt
    export_parser = subparsers.add_parser("export", help="Export lyrics from .mscx to .txt")
    export_parser.add_argument(
        "mscx",
        metavar="MSCX",
        help="Input MuseScore .mscx file",
    )
    export_parser.add_argument(
        "-o", "--output",
        metavar="TXT",
        help="Output .txt file (default: same base name as MSCX with .txt)",
    )

    # import: txt + mscx -> mscx
    import_parser = subparsers.add_parser("import", help="Import lyrics from .txt into .mscx")
    import_parser.add_argument(
        "txt",
        metavar="TXT",
        help="Input lyrics .txt file",
    )
    import_parser.add_argument(
        "mscx",
        metavar="MSCX",
        help="Input MuseScore .mscx file",
    )
    import_parser.add_argument(
        "-o", "--output",
        metavar="MSCX",
        help="Output .mscx file (default: overwrite input MSCX)",
    )
    import_parser.add_argument(
        "--split",
        metavar="N,M,...",
        help="JSON only: split listed parts into two staves each (e.g. --split 3,4 => parts 3,4 become 3+4, 5+6)",
    )

    args = parser.parse_args()

    if args.command == "export":
        mscx = args.mscx
        if not os.path.exists(mscx):
            sys.exit(f"File not found: {mscx}")
        out = args.output
        if not out:
            out = os.path.splitext(mscx)[0] + ".txt"
        export_file(mscx, out)
        print(f"Exported to {out}")

    elif args.command == "import":
        txt_path = args.txt
        mscx_in = args.mscx
        if not os.path.exists(txt_path):
            sys.exit(f"File not found: {txt_path}")
        if not os.path.exists(mscx_in):
            sys.exit(f"File not found: {mscx_in}")
        out = args.output or mscx_in
        split = None
        if getattr(args, "split", None):
            try:
                split = [int(x.strip()) for x in args.split.split(",") if x.strip()]
            except ValueError:
                sys.exit("--split must be comma-separated part numbers (e.g. 3,4)")
        import_file(txt_path, mscx_in, out, split=split)
        print(f"Imported to {out}")


if __name__ == "__main__":
    main()
