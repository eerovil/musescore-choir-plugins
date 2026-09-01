# Fixture: the OMR benchmark's public-domain slice

Real scanned pages the scanning code can be tested against, with the one page that
has **note-level ground truth**. Small enough to commit: the PDFs travel and the
tests rasterise, the way `pdf_systems` already shells out to poppler.

## Why it is here

Everything the OMR map (#92) concluded — the candidate comparison, the staff-position
work, the nine-page run, the slur measurements — rested on `~/omr-benchmark/` on one
host. That folder is not in the repository, so a fresh clone had none of the evidence
and CI could not run against real music. #111 tested flattening against synthetic
little documents in the shapes homr produced, which was the right call with nothing
else available, but it meant nothing in the suite ever met a real scan.

Three of the seven benchmark pages are public domain. Those three are here. The rest
are personal copies of in-copyright editions (Fazer, Breitkopf, Fennica Gehrman,
Sulasol) and **stay on the host**, along with their parses and the benchmark's `out/`.
The host copy remains the fuller set and is still where judging happens; this is the
slice that can travel.

## What travelled

`pages.json` is the file of record — ids, page numbers, printed staff counts and the
system bounds, as fractions of page height, in the same units `.systems.json` uses so
they crop correctly at any resolution. `src/song_app/tests/benchmark.py` reads it; no
test knows its layout.

| id | file | what it is |
| --- | --- | --- |
| **B1a** | `B1a-heraa-suomi-p420-287dpi.pdf` (377 KB) | *Herää Suomi!* printed p. 420, bars 11–17, TTBB. A clean 287 dpi scan. The only page with note-level ground truth. |
| **B1b** | `B1b-heraa-suomi-p420-mrc.pdf` (54 KB) | The **same printed page** at 150 dpi with JBIG2/CCITT segmentation and visible dropout. Same music, same truth, worse image — so scan quality is isolated from everything else. |
| **B2** | *(no file)* | *Virta venhettä vie* printed p. 119, bars 15–30. Page 2 of `fixtures/virta-venhetta-vie/00-registered/` **is** this page; `pages.json` points at it rather than committing 188 KB of the same paper again. |

Plus the ground truth, which is the part worth having:

- `B1-heraa-suomi-p420-truth.csv` (836 B) — per bar, per staff, per voice: chords,
  notes, chords with more than one notehead, rests. 120 note events over bars 11–17.
- `B1-heraa-suomi-hand-transcription.mscx` (323 KB) — the transcription the table was
  derived from, entered with the print's own line and page breaks.

**No PNGs.** A 300 dpi render of one of these pages is 1.0–1.7 MB on its own, and
`test_the_committed_slice_stays_small` fails if one appears.

Two things about the truth worth knowing before scoring against it. Bar 15 in the
bass is **one voice, not two**: the two basses are in unison there and the print has a
single line, so a scoring rule has to allow for it. And the page boundary — why this
page is bars 11–17, and why its systems are 11–13 / 14–15 / 16–17 — comes from the
transcription's own page and line breaks rather than from someone counting bars off a
scan. The test asserts that, so the two cannot drift apart.

## What the tests do with it

`src/song_app/tests/test_benchmark.py`, in three tiers so each dependency buys
something and none of them is required:

1. **No dependencies.** The truth table is re-derived from the transcription and must
   agree bar for bar, and the page boundary is read off the transcription's breaks.
   This is the tier CI runs, and it is what makes the table evidence rather than a
   claim.
2. **poppler.** Each page crops into the bands its bounds name.
3. **homr** (`-m omr`). `omr_systems.read_systems` on a real scan comes back with the
   staves the page prints and the bars the bounds declare, and B1a assembles into one
   score. Skips without homr, the way the MuseScore-CLI and Playwright tests skip —
   CI has no homr and should not grow one.

```bash
.venv/bin/python -m pytest src/song_app/tests/test_benchmark.py -q               # all tiers
.venv/bin/python -m pytest src/song_app/tests/test_benchmark.py -q -m "not omr"  # seconds
```

The homr tier is ~3 minutes on this host, at roughly 10 seconds a system.

## Rights

*Herää Suomi!* and *Virta venhettä vie* are both 19th/early-20th-century Finnish
works whose composers and poets died well over 70 years ago, so the compositions and
the texts are in the public domain. The committed files are scans of old printings.
As the song fixture's own README notes for the same case: the work is PD, but if a
particular printing turns out to be a recent reprint its typographical arrangement
could carry a separate short-term right in some jurisdictions. Nothing here depends
on which printing it is — swap the scan if that ever matters.
