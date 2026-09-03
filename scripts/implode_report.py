#!/usr/bin/env python3
"""Show every song's printed page beside the reference imploded out of its score.

The imploded score is what homr will be judged against, so it has to be judged
first.  Counting staves says the shape is right; only looking says the music is.

    .venv/bin/python scripts/implode_report.py [song ...]

Writes `implode-report/index.html`.  Needs poppler and the MuseScore CLI.
"""

import glob
import html
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.clean_score.implode import implode  # noqa: E402

MANIFEST = Path("fixtures/omr-songs.json")


def _reviewed(name: str) -> dict:
    if not MANIFEST.exists():
        return {}
    return json.loads(MANIFEST.read_text())["songs"].get(name, {})


def override_for(name: str) -> list[list[str]] | None:
    """The grouping a person read off the page, if one is written down."""
    return (_reviewed(name).get("grouping", {}).get("override") or {}).get("printed")


def drop_rests_for(name: str) -> list[str]:
    """Parts whose silence the page does not print."""
    return (_reviewed(name).get("grouping", {}).get("override") or {}).get("drop_rests") or []


def chosen_pdf(name: str) -> str | None:
    """The page a person said is the one this score was made from."""
    return (_reviewed(name).get("source_override") or {}).get("pdf")

OUT = Path("implode-report")
#: Wide enough to read a notehead, small enough to open on a phone.
WIDTH = 1500


def made_a_video(folder: str) -> dict | None:
    """What says this song was carried all the way to a practice video.

    Only a song somebody has actually sung from is worth judging homr against:
    it has been through the fixing, the lyrics and a listen.  A video may not
    still be on disk -- an uploaded one is often deleted -- so an upload or
    having reached the record stage counts too.
    """
    videos = glob.glob(folder + "media/video/*.mov") + glob.glob(folder + "media/video/*.mp4")
    state = Path(folder) / ".song.json"
    stage, uploads = "", 0
    if state.exists():
        saved = json.loads(state.read_text())
        stage = saved.get("stage", "")
        uploads = len((saved.get("record") or {}).get("uploads") or [])
    if not (videos or uploads or stage in ("record", "upload")):
        return None
    return {"videos": len(videos), "uploads": uploads, "stage": stage}


def printed_pdf(folder: str, chosen: str | None = None) -> Path | None:
    """The scan the song was made from, not one of the app's own renders.

    The app caches MuseScore renders of the cleaned score beside the source as
    `*.render.pdf`, and one of those sorts first often enough to be picked by
    accident -- which makes the "printed page" a picture of the reference, and
    the whole comparison a score against itself.
    """
    if chosen:
        picked = Path(folder) / chosen
        return picked if picked.exists() else None
    pages = [
        Path(path)
        for path in sorted(glob.glob(folder + "*.pdf"))
        if not path.endswith(".render.pdf") and "_cleaned" not in Path(path).name
    ]
    return pages[0] if pages else None


def songs() -> list[tuple[str, Path, Path, dict]]:
    """Every song with a printed page, a cleaned score and a practice video."""
    found = []
    for folder in sorted(glob.glob("songs/*/")):
        cleaned = glob.glob(folder + "*_cleaned.mscx")
        pdf = printed_pdf(folder, chosen_pdf(Path(folder).name))
        made = made_a_video(folder)
        if cleaned and pdf and made:
            found.append((Path(folder).name, pdf, Path(sorted(cleaned)[0]), made))
    return found


def _shrink(source: Path, target: Path) -> bool:
    """One page as a grey JPEG: MuseScore's PNGs are transparent and huge."""
    result = subprocess.run(
        ["magick", str(source), "-flatten", "-colorspace", "Gray",
         "-resize", f"{WIDTH}>", "-quality", "88", str(target)],
        capture_output=True,
    )
    return result.returncode == 0 and target.exists()


def printed_page(pdf: Path, target: Path) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        stem = Path(tmp) / "page"
        subprocess.run(
            ["pdftoppm", "-r", "150", "-png", "-f", "1", "-l", "1", str(pdf), str(stem)],
            capture_output=True,
        )
        pages = sorted(Path(tmp).glob("page*.png"))
        return bool(pages) and _shrink(pages[0], target)


def engrave(score: Path, cli: str, target: Path) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        rendered = Path(tmp) / "out.png"
        subprocess.run(
            [cli, "-o", str(rendered), str(score)], capture_output=True, timeout=300
        )
        pages = sorted(Path(tmp).glob("out*.png"))
        return bool(pages) and _shrink(pages[0], target)


STYLE = """
body { font: 15px/1.6 system-ui, sans-serif; margin: 0 auto; max-width: 1250px;
       padding: 24px; color: #1a1a1a; background: #fafafa; }
h1 { font-size: 22px; margin-bottom: 4px; }
h2 { font-size: 17px; margin: 30px 0 2px; }
p.lead, p.sub { color: #555; margin-top: 0; }
p.sub { font-size: 13px; margin-bottom: 8px; }
.pair { display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
        background: #fff; border: 1px solid #e2e2e2; border-radius: 8px; padding: 12px; }
.label { font: 600 11px ui-monospace, Menlo, monospace; letter-spacing: .05em;
         text-transform: uppercase; color: #777; margin: 0 0 4px; }
img { display: block; width: 100%; border: 1px solid #eee; background: #fff; }
.guess { background: #fdefd8; color: #8a5a10; border-radius: 4px;
         padding: 1px 6px; font-size: 12px; font-weight: 600; }
.recorded { color: #1c5c2c; font-size: 12px; }
.video { color: #26379a; font-size: 12px; }
"""


def main() -> None:
    load_dotenv()
    cli = os.environ.get("MUSESCORE_CLI_PATH", "")
    OUT.mkdir(exist_ok=True)
    wanted = set(sys.argv[1:])
    sections = []
    for name, pdf, cleaned, made in songs():
        if wanted and name not in wanted:
            continue
        root = etree.parse(str(cleaned)).getroot()
        found = implode(root, override_for(name), drop_rests_for(name))
        with tempfile.TemporaryDirectory() as tmp:
            score = Path(tmp) / "imploded.mscx"
            etree.ElementTree(root).write(str(score), encoding="UTF-8", xml_declaration=True)
            drawn = engrave(score, cli, OUT / f"{name}-imploded.jpg")
        shown = printed_page(pdf, OUT / f"{name}-page.jpg")
        badge = (
            '<span class="guess">grouping guessed</span>'
            if found.inferred
            else f'<span class="recorded">grouping from the score\'s own {found.source}</span>'
        )
        staves = " &middot; ".join(html.escape(printed.label) for printed in found.printed)
        evidence = (
            f"{made['videos']} video(s) on disk"
            if made["videos"]
            else (f"{made['uploads']} uploaded" if made["uploads"] else f"stage {made['stage']}")
        )
        sections.append(
            f"""<h2>{html.escape(name)}</h2>
<p class="sub">{badge} &mdash; {len(found.printed)} printed staves: {staves}
 &mdash; <span class="video">{html.escape(evidence)}</span></p>
<div class="pair">
  <div><p class="label">the printed page</p>
    {f'<img src="{name}-page.jpg" alt="printed page">' if shown else "<p>no page</p>"}</div>
  <div><p class="label">imploded out of the cleaned score</p>
    {f'<img src="{name}-imploded.jpg" alt="imploded score">' if drawn else "<p>not engraved</p>"}</div>
</div>"""
        )
        print(f"{name}: {'guessed' if found.inferred else found.source}, {len(found.printed)} staves")
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Imploded references</title><style>{STYLE}</style></head><body>
<h1>Imploded references</h1>
<p class="lead">Each song's printed page beside the reference imploded out of its
cleaned score &mdash; the voices the app split apart put back on the staves they
were printed on. Only songs carried all the way to a practice video are here:
those are the ones somebody has fixed, lyricked and sung from. This is what homr would be judged against, so it has to be right
first. Where the score did not record which voices shared a staff, the grouping is
<span class="guess">guessed</span> and should be checked hardest.</p>
{''.join(sections)}
</body></html>"""
    (OUT / "index.html").write_text(page)
    if not wanted:
        # A song that has dropped out of the set must drop out of the folder
        # too, or the page beside it is one nobody is judging any more.
        keep = {f"{name}-page.jpg" for name, *_ in songs()}
        keep |= {f"{name}-imploded.jpg" for name, *_ in songs()}
        for stale in OUT.glob("*.jpg"):
            if stale.name not in keep:
                stale.unlink()
    print(OUT / "index.html")


if __name__ == "__main__":
    main()
