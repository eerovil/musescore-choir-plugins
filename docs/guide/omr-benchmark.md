# The OMR benchmark's public-domain slice

Part of this repository's agent guide; the index is [CLAUDE.md](../../CLAUDE.md). Moved out of it word for word (#513).

### The OMR benchmark's public-domain slice

`fixtures/omr-benchmark/` is added by this pull request: three real scanned pages the
scanning code can be tested against, and the one page with **note-level ground truth**.
Everything #92's map concluded rested on `~/omr-benchmark/`, which is host state — a
fresh clone had none of it and CI could not run against real music, so #111 tested
flattening against synthetic documents in the shapes homr produced. Three of the seven
benchmark pages are public domain and those travel; the in-copyright ones (Fazer,
Breitkopf, Fennica Gehrman, Sulasol), their parses and the benchmark's `out/` stay on
the host, which remains the fuller set and where judging happens.

`pages.json` is the file of record — ids, page numbers, printed staff counts, and
system bounds as fractions of page height, the same units `.systems.json` uses.
`src/song_app/tests/benchmark.py` reads it so no test knows its layout. **B2 has no
file of its own**: page 2 of `fixtures/virta-venhetta-vie/00-registered/` *is* that
page, so the manifest points at it rather than committing the same paper twice. B1a and
B1b are the same printed page at 287 dpi and at 150 dpi with dropout, so scan quality
is isolated from everything else. **Only PDFs are committed** — the tests rasterise with
poppler, and a 300 dpi PNG of one of these pages is 1.0–1.7 MB on its own.

Two things about the truth, both pinned by tests. It is **derived, so it is
re-derived**: `test_the_truth_table_is_what_the_transcription_says` rebuilds the CSV's
120 note events out of the hand transcription bar by bar, which is what makes it
evidence rather than a claim. And the page boundary — bars 11–17, systems 11–13 /
14–15 / 16–17 — comes from the transcription's own page and line breaks, not from
counting bars off a scan. One exception a scoring rule has to allow for: in bar 15 the
two basses are in unison and the print has a single line, so the truth is one voice.

See `fixtures/omr-benchmark/README.md`.
