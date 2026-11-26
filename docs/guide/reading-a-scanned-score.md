# Reading a scanned score

Part of this repository's agent guide; the index is [CLAUDE.md](../../CLAUDE.md). Moved out of it word for word (#513).

## Reading a scanned score (playbook)

Hard-won in the session that built `pdf_systems`, where reading whole rendered pages
produced a confident, tidy and substantially wrong conclusion. If you are an agent
about to read a score off a PDF, start here.

**Crop before concluding anything.** A whole A4 rendered small enough to look at
cannot show a slur or a notehead. Use `pdf_systems.crop_systems` (400 dpi, one band,
~0.9s) and read a system at a time. `page_images(grid=True)` overlays a labelled
percentage scale for reading system boundaries off — never estimate them by eye, that
is how a crop ends up clipping the lyric line under the bottom staff.

**Let the arithmetic check the reading.** `lyric_txt.slot_counts(root)` gives
`[staff][measure] = notes that take a syllable`. Every correct correction predicts
its slot count *before* being encoded and lands on it exactly; a reading that needs
the numbers bent to fit is wrong. `place_lyrics` returns the same numbers as
`Mismatch` records.

**The direction of a mismatch says what kind of problem it is:**

| | means |
|---|---|
| `too_many` | the **reading** is wrong — too many syllables for the notes |
| `too_few`, by a lot | a voice **sharing** another staff's words (below) — **or** a **runaway slur** swallowing the slots (next paragraph) |
| `too_few`, by exactly one or two | usually a **dropped slur** — a melisma the OCR lost |
| all `too_few`, never `too_many` | do **not** conclude "missing slurs" from this alone; that inference was made once and was mostly wrong |

**Look at the slurs before you conclude "sharing".** The two readings of a large
`too_few` want opposite responses — sharing is correct engraving and is written down
in the lyric JSON, a runaway slur is a defect in the score and has to come out of it —
and they are easy to confuse, because a runaway is invisible in the numbers and points
straight at the wrong answer. What tells them apart: sharing shows up as a voice whose
per-measure note counts match another voice's exactly, while a runaway shows up as
**one bar losing most of its slots to a slur crossing several barlines**. Look for the
long slur first, since that is a single check: `lyric_txt.syllable_slots(root, staff,
measure)` says note by note which are being swallowed. homr now pairs its own slurs
(eerovil/homr#62), which leaves a score scanned with an older homr, or one from another
OMR tool, as the cases to watch for.

**Voices sing words that are not printed under them.** Older choral engraving prints
a text once and expects more than one voice to use it, so notes with no text beneath
are the norm, not an anomaly:

- Text set *between* the staves usually serves both.
- A voice's own line can start part-way through a system; before that it sings the
  other staff's words (bass sharing the tenor line for two measures, then breaking
  away at its own entry).
- A voice whose per-measure note counts **match another voice's exactly** is very
  likely singing that voice's words — in the fixture the upper bass doubles the tenor
  rhythm throughout.
- A measure where *every* voice has the same note count and one text is printed is a
  unison convergence: all of them sing it.

**Check continuity across system breaks.** Each voice's text must join into a
sentence from one system to the next. When it is ambiguous which staff a line between
two staves belongs to, this decides it — only one assignment leaves every voice with
a sentence.

Worked through twice, with the wrong turn left in, in
`fixtures/virta-venhetta-vie/STEPS.md`.
