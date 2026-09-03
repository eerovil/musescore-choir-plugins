#!/usr/bin/env python3
"""Every page of one song: each PDF it has, and the whole imploded reference.

The side-by-side report shows first pages, which is enough to see that a
reference has the right shape and not enough to judge a piece.  This lays a song
out in full -- including every PDF in its folder, since which one is the page the
score was made from has twice been the thing that was wrong.

    .venv/bin/python scripts/song_pages.py "Kolme käkeä"

Writes `song-pages/<song>.html`.
"""

import glob
import html
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.implode_report import (  # noqa: E402
    _shrink,
    chosen_pdf,
    drop_rests_for,
    override_for,
)
from src.clean_score.implode import implode  # noqa: E402

OUT = Path("song-pages")


def pdf_pages(pdf: Path, stem: str) -> list[str]:
    names = []
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["pdftoppm", "-r", "150", "-png", str(pdf), str(Path(tmp) / "p")],
            capture_output=True,
        )
        for number, page in enumerate(sorted(Path(tmp).glob("p*.png")), start=1):
            target = OUT / f"{stem}-{number}.jpg"
            if _shrink(page, target):
                names.append(target.name)
    return names


def engraved_pages(score: Path, cli: str, stem: str) -> list[str]:
    names = []
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [cli, "-o", str(Path(tmp) / "out.png"), str(score)], capture_output=True, timeout=600
        )
        for number, page in enumerate(sorted(Path(tmp).glob("out*.png")), start=1):
            target = OUT / f"{stem}-{number}.jpg"
            if _shrink(page, target):
                names.append(target.name)
    return names


STYLE = """
body { font: 15px/1.6 system-ui, sans-serif; margin: 0 auto; max-width: 1000px;
       padding: 24px; color: #1a1a1a; background: #fafafa; }
h1 { font-size: 22px; margin-bottom: 4px; }
h2 { font-size: 17px; margin: 30px 0 6px; }
p.lead, p.sub { color: #555; margin-top: 0; }
p.sub { font-size: 13px; }
img { display: block; width: 100%; border: 1px solid #e2e2e2; background: #fff;
      border-radius: 6px; margin-bottom: 10px; }
.chosen { background: #e8f4ea; color: #1c5c2c; border-radius: 4px;
          padding: 1px 6px; font-size: 12px; font-weight: 600; }
"""


def main() -> None:
    load_dotenv()
    cli = os.environ.get("MUSESCORE_CLI_PATH", "")
    name = sys.argv[1]
    folder = f"songs/{name}/"
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob(f"{name}-*.jpg"):
        old.unlink()

    sections = []
    for pdf in sorted(Path(path) for path in glob.glob(folder + "*.pdf")):
        if pdf.name.endswith(".render.pdf") or "_cleaned" in pdf.name:
            continue
        picked = pdf.name == (chosen_pdf(name) or "")
        badge = '<span class="chosen">the page this reference is checked against</span>' if picked else ""
        pages = pdf_pages(pdf, f"{name}-{pdf.stem}")
        sections.append(
            f"<h2>{html.escape(pdf.name)} &mdash; {len(pages)} pages {badge}</h2>"
            + "".join(f'<img src="{html.escape(page)}" alt="page">' for page in pages)
        )

    cleaned = sorted(glob.glob(folder + "*_cleaned.mscx"))[0]
    root = etree.parse(cleaned).getroot()
    found = implode(root, override_for(name), drop_rests_for(name))
    with tempfile.TemporaryDirectory() as tmp:
        score = Path(tmp) / "imploded.mscx"
        etree.ElementTree(root).write(str(score), encoding="UTF-8", xml_declaration=True)
        pages = engraved_pages(score, cli, f"{name}-imploded")
    staves = " &middot; ".join(html.escape(printed.label) for printed in found.printed)
    sections.append(
        f"<h2>the reference, imploded out of the cleaned score &mdash; {len(pages)} pages</h2>"
        f"<p class=\"sub\">grouping: {found.source} &mdash; {len(found.printed)} printed staves:"
        f" {staves}</p>"
        + "".join(f'<img src="{html.escape(page)}" alt="page">' for page in pages)
    )

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(name)} in full</title><style>{STYLE}</style></head><body>
<h1>{html.escape(name)}</h1>
<p class="lead">Every page of every PDF in the song's folder, and the whole
reference imploded out of its cleaned score. MuseScore breaks systems where it
likes, so the pages will not line up: judge the notes, the voices and the words.</p>
{''.join(sections)}
</body></html>"""
    target = OUT / f"{name}.html"
    target.write_text(page)
    print(target)


if __name__ == "__main__":
    main()
