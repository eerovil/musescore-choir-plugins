#!/usr/bin/env python3
"""The scan against the page, system by system, as a page you can look at.

`scan_vs_reference.py` gives the numbers; this puts the picture beside them,
because a system that agrees on staves, bars and notes can still be wrong about
every pitch, and nothing counted here would say so.  Each row is one printed
system: the band cropped out of the original PDF, and under it the same system as
homr read it, engraved.

Runs on whatever has been scanned so far, so it can be built while the rest is
still going.

    .venv/bin/python scripts/scan_report.py [<slug> ...]

Writes `scan-report/index.html`.
"""

import glob
import html
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.implode_report import _shrink  # noqa: E402
from scripts.scan_vs_reference import (  # noqa: E402
    SCRATCH,
    printed_staves,
    reference_systems,
    scanned_systems,
)
from src.song_app import pdf_systems, pipeline  # noqa: E402

MANIFEST = Path("fixtures/omr-songs.json")
OUT = Path("scan-report")

STYLE = """
body { font: 15px/1.6 system-ui, sans-serif; margin: 0 auto; max-width: 1100px;
       padding: 24px; color: #1a1a1a; background: #fafafa; }
h1 { font-size: 23px; margin-bottom: 4px; }
h2 { font-size: 18px; margin: 34px 0 4px; }
p.lead, p.sub { color: #555; margin-top: 0; }
p.sub { font-size: 13px; }
.sys { border: 1px solid #e2e2e2; background: #fff; border-radius: 8px;
       padding: 12px 14px; margin-bottom: 14px; }
.head { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap;
        margin-bottom: 8px; }
.who { font-weight: 600; }
.num { color: #555; font-size: 13px; }
.tag { border-radius: 4px; padding: 1px 7px; font-size: 12px; font-weight: 600; }
.same { background: #e8f4ea; color: #1c5c2c; }
.off  { background: #fdecec; color: #8a1f1f; }
.hole { background: #fff3cd; color: #7a5b00; }
img { display: block; width: 100%; border: 1px solid #eee; border-radius: 4px;
      margin-bottom: 6px; background: #fff; }
.cap { color: #777; font-size: 12px; margin: 0 0 8px; }
"""


def picture(name: str, source: str) -> str:
    """Shrink one image into the report folder; '' when it could not be made."""
    target = OUT / name
    return target.name if _shrink(Path(source), target) else ""


def main() -> None:
    load_dotenv()
    listed = json.loads(MANIFEST.read_text())["songs"]
    slugs = sys.argv[1:] or [n for n, e in listed.items()
                             if e["review"]["status"] != "excluded"]
    OUT.mkdir(exist_ok=True)

    sections, done, agree_staves, agree_bars, holes = [], 0, 0, 0, 0
    for slug in slugs:
        if not (SCRATCH / slug / ".song.json").exists():
            sections.append(f"<h2>{html.escape(slug)}</h2>"
                            f"<p class=\"sub\">not scanned yet</p>")
            continue
        song_dir = f"songs/{slug}"
        pdf = str(Path(song_dir) / listed[slug]["pdf"])
        bands = pdf_systems.load_bounds(song_dir)
        reference = reference_systems(slug, bands)
        printed = printed_staves(slug, bands)
        if printed:
            for want, count in zip(reference, printed):
                if want:
                    want["staves"] = count
        scanned = scanned_systems(slug)

        rows = []
        for band, want in zip(bands, reference):
            got = scanned.get(band.index)
            if got is None:
                continue
            done += 1
            crop = ""
            try:
                image = pdf_systems.crop_systems(pdf, [band], str(OUT / ".crops"))[0]
                crop = picture(f"{slug}-{band.index:02}-page.jpg", image.path)
            except Exception:
                crop = ""
            if got.get("error"):
                holes += 1
                rows.append(
                    f'<div class="sys"><div class="head">'
                    f'<span class="who">System {band.index}</span>'
                    f'<span class="num">page {band.page}, bars '
                    f'{want.get("measure","")}{want.get("bars","?")} in the score</span>'
                    f'<span class="tag hole">could not be read</span></div>'
                    + (f'<img src="{crop}" alt="printed system">' if crop else "")
                    + f'<p class="cap">{html.escape(got["error"][:300])}</p></div>')
                continue

            same_staves = want.get("staves") == got.get("staves")
            same_bars = want.get("bars") == got.get("bars")
            agree_staves += same_staves
            agree_bars += same_bars
            engraved = ""
            entry = json.loads((SCRATCH / slug / ".song.json").read_text())
            frag = entry["scan"]["systems"][str(band.index)]["musicxml"]
            try:
                png = pipeline.scan_system_render(str(SCRATCH / slug),
                                                  str(SCRATCH / slug / frag))
                engraved = picture(f"{slug}-{band.index:02}-scan.jpg", png)
            except Exception:
                engraved = ""

            tags = []
            tags.append(f'<span class="tag {"same" if same_staves else "off"}">'
                        f'{got.get("staves")} staves, page prints {want.get("staves")}</span>')
            tags.append(f'<span class="tag {"same" if same_bars else "off"}">'
                        f'{got.get("bars")} bars, page has {want.get("bars")}</span>')
            gap = (got.get("notes") or 0) - (want.get("notes") or 0)
            tags.append(f'<span class="tag {"same" if gap == 0 else "off"}">'
                        f'{got.get("notes")} noteheads, reference has {want.get("notes")}'
                        f'{"" if gap == 0 else f" ({gap:+d})"}</span>')
            rows.append(
                f'<div class="sys"><div class="head">'
                f'<span class="who">System {band.index}</span>'
                f'<span class="num">page {band.page}, bars {band.measure_start}'
                f'&ndash;{band.measure_end}</span>{"".join(tags)}</div>'
                + (f'<p class="cap">the printed band</p><img src="{crop}" alt="printed">'
                   if crop else "")
                + (f'<p class="cap">what homr read off it</p>'
                   f'<img src="{engraved}" alt="scan">' if engraved else
                   '<p class="cap">(could not engrave the parse)</p>'))
        sections.append(
            f"<h2>{html.escape(listed[slug].get('review', {}).get('notes') or slug)}</h2>"
            f"<p class=\"sub\">{html.escape(slug)} &mdash; {len(rows)} of "
            f"{len(bands)} systems read</p>" + "".join(rows))

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The scan against the page</title><style>{STYLE}</style></head><body>
<h1>The scan against the page</h1>
<p class="lead">Each printed system as it is on paper, and under it the same
system as homr read it. The counts are a first look and not a score: staves and
bars come off the score's own line breaks, noteheads off the imploded reference.
A system can agree on all three and still be wrong about every pitch, which is
what the pictures are for.</p>
<p class="sub">{done} systems read so far &mdash; {agree_staves} with the staves
the page prints, {agree_bars} with its bars, {holes} unread.</p>
{''.join(sections)}
</body></html>"""
    target = OUT / "index.html"
    target.write_text(page)
    print(target.resolve())


if __name__ == "__main__":
    main()
