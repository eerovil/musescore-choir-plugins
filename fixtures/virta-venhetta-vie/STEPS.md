# How this fixture was produced

A log of each step, what produced it, and what it assumed. Steps 1–3 were done
without human intervention: the score was OCR'd, cleaned by the pipeline, and the
lyrics were transcribed from the scan by an AI (Claude) reading the four PDF pages.
Step 4 is where that stops, and why.

Steps 2 and 3 are reproducible with `build.py`. Step 3's *text* is not — it is a
committed artifact, because regenerating it means asking a model to read the scan
again and the result would drift.

---

## Step 1 — Register (`00-registered/`)

The scan was run through **Soundslice**'s OCR and exported as MusicXML, then
registered in the app as a normal-mode song. Both files are committed as they came
out; nothing was tidied.

What the OCR got right: pitches, rhythms, all five meter changes, the triplets, both
staves' two-voice texture, and the system breaks (15 of them, matching the printed
page).

What it got wrong or dropped, and this is the useful part:

- **all lyrics** — there is not one `<lyric>` element in 265 KB of MusicXML
- **m26 arrives over-full**, carrying an explicit `len="9/8"` where the meter is 3/4
- **melisma slurs**, in quantity — see step 3

## Step 2 — Clean (`10-cleaned/`)

`pipeline.run_clean(..., per_system=False)`, i.e. exactly the app's Clean button.
Two OCR staves become four: T1, T2, B1, B2, named by `detect_part_types` from clef
and pitch range (G8vb / F, tenor range 53–70, bass 34–61).

Worth knowing what fired along the way:

- `find_reversed_voices_by_staff_measure` flagged **m1 on both tenor staves** — the
  stems are engraved the other way round there, and the splitter picked the correct
  voice per measure because of it.
- `fix_spurious_timesigs` left **all five meter changes alone**. Each one's measure
  content matches its declared signature, so they are genuine, and the pass
  correctly declined to "repair" them. A useful negative case: this fixture proves
  the pass does not fire on a score full of real meter changes.
- `preprocess_corrupted_measures` logged **"Measure 25 is not possible to fix"**
  (0-indexed; printed m26).
- `add_missing_ties` logged three near-misses
  (`Note durations do not match parent pair: 32, 16 != 64, 64`) and mirrored nothing
  there — correctly, since the durations do not line up.

**Result: one open health issue**, `malformed-m26-s1-v0` — "voice 1 fills 1 of 9/8".
It is real: the measure is genuinely over-full in the OCR and no automatic pass can
know what the engraver meant. Fixing it needs the score open in MuseScore, which is
step 4.

## Step 3 — Lyrics (`20-lyrics/`)

The OCR supplied no lyrics, so this is the full round-trip the Lyrics panel's "Paste
from AI" mode exists for: prompt (`lyric_json_prompt.txt`) + the four scanned pages
→ `lyrics.json` → `place_lyrics`.

### Reading the page

The hard part is not the words, it is **which voice each printed line belongs to**.
This edition puts up to four lyric lines around two staves, and the two inner lines
sit in the same gap between the staves. In the imitative *levollisesti* section
(systems 7–8) three lines cluster under the tenor staff, which cannot be right —
there are only two tenor voices.

It resolves by **continuity across the system break**: each voice's text must join up
into a sentence. Only one assignment does that, and it makes the second inner line
belong to the *bass upper* voice rather than a third tenor line:

| voice | system 7 | system 8 |
|---|---|---|
| T1 | "… Ei" | "tie-dä si-tä ih-mi-sis-tä ken-kään." |
| T2 | "… vir-ta ven-het-tä" | "vie, mi-hin päät-ty-vi tie? …" |
| B1 | "… Vir-ta ven-het-tä vie, mi-hin päät-ty-vi" | "tie, ei tie-dä si-tä ken-kään." |
| B2 | "… vir-ta ven-het-tä" | "vie, mi-hin päät-ty-vi tie?" |

All four join cleanly; every other assignment leaves at least one voice with a
fragment that continues into someone else's phrase. The same rule was applied
throughout, and step 3 was written from it.

**Assumption, flagged:** in systems 4, 5, 6, 10 the engraver prints the text **once,
between the staves**, and the bass has no line of its own. It is read as shared by
all four voices, encoded as `"parts": ["T1","T2","B1","B2"]`. The syllable counts
came out right for all four voices in every one of those systems, which is decent
evidence, but it is still an inference from layout.

### The import, and what the mismatches mean

First pass: 37 lyric lines, **16 mismatches**. Four were resolvable from the slot
counts alone — in system 2 the score has 11 eligible notes under T1 where the
printed page shows only "tie?", because both tenors converge on "Lyö kuo-hut pur-ren
puu-ta ja tal-kaa." after the imitative entry. Adding that tail made T1 fit exactly
(11 = 11), and the same reading resolved B1 (11) and B2 (13, taking the extra
"Mi-kä" that is printed once, under the lower voice). That left **12**.

The remaining twelve were all `too_few` — the score offering *more* eligible notes
than the page shows syllables — and were first written off as OCR-dropped melisma
slurs. Re-reading the scan at 400 dpi (`pdftoppm -r 400`, cropped per system; the
whole-page renders used earlier are far too coarse to see a slur) showed that was
mostly wrong. The dominant cause is **text sharing between staves**:

> A voice whose own printed line covers only *part* of a system sings the other
> staff's words for the rest of it.

It is visible directly — in m38–40 the bass staff has two measures of notes with no
text under them before "Vai-ko val-het-ta…" begins — and it checks out
arithmetically every time:

| | slots | reading | total |
|---|---|---|---|
| m31–34 B1 | 15 | the whole tenor line | 15 |
| m38–40 B1 | 21 | the whole tenor line | 21 |
| m38–40 B2 | 20 | tenor line to "koi;" (12) + its own "Vai-ko val-het-ta, val-het-ta," (8) | 20 |
| m41–43 B1 | 18 | tenor line to "hen-kää," (10) + its own "Vai-ko val-het-ta lie? Vai-ko" (8) | 18 |

All four land exactly, and the placed result reads correctly back out of the score:
B1 sings the tenors' words through m41, then diverges at "hen-kää, Vai-ko"; B2
diverges a measure earlier, at m39. Encoding that dropped the count from **12 to 8**,
and the notes left without a syllable from **87 to 30**.

The earlier reading was not applied widely enough: text sharing was assumed only for
systems 4, 5, 6 and 10, where the bass has *no* printed text at all. The rule is
more general — a bass line can share for two measures and then break away.

### The second pass, with the crops

The first eight were re-read from `pdf_systems` crops at 400 dpi -- the tooling
built for exactly this -- against the score's own per-measure note counts. Five
more resolved, all the same rule, and the counts landed exactly every time:

| | slots | reading | total |
|---|---|---|---|
| m8–10 B1 | 15 | the tenor line (its notes are `[4,7,4]`, identical to theirs) | 15 |
| m27–30 T1 | 15 | its own line (10) + "Me-ri, tai-vas ja" (5) | 15 |
| m27–30 B1 | 13 | its own line (8) + the same five | 13 |
| m27–30 B2 | 12 | its own line (7) + the same five | 12 |
| m44–46 T1 | 13 | T2's "hen-kää, ja" (3) + its own line from m45 (10) | 13 |
| m50–52 B1 | 14 | the tenor line (13), one note over | 13 |

Two things the crops made obvious that the page renders had not:

**The upper bass voice doubles the tenor rhythm.** In m8–10 and m50–52 the bass
staff's upper voice moves in the tenors' dotted-eighths and triplets while the
lower voice carries the printed bass line on long notes. So B1 sings the tenors'
words and B2 the printed ones -- visible at a glance in the crop, invisible at page
scale, and confirmed by B1's note counts matching the tenors' exactly.

**The choir converges in unison at "Me-ri, tai-vas ja"** (m30), printed once on the
T2 line. Every voice has exactly 5 notes in that measure. Three lines were short by
exactly 5.

That took the mismatches from **8 to 3** and the notes without a syllable from
**30 to 3**.

### What is left, and why

```
[too_few] m8-10   B2:  10 syllables for 11 slots     gap +1
[too_few] m31-34  B2:  17 syllables for 18 slots     gap +1
[too_few] m50-52  B1:  13 syllables for 14 slots     gap +1
```

Three lines, each exactly one note over. That is the shape of a **single dropped
melisma slur** per line, and in m8–10 the slur is plainly visible in the crop: a
curve over the lower bass half-note running into the 2/4, on the "ih - " of
"ih - mi-nen". The OCR lost it, so the note counts as a syllable slot.

Nothing here is a lyric error any more. The text is right; the score is missing
three slurs. Fixing it means writing spanners into the `.mscx`, not editing
`lyrics.json` -- see step 4.

## Step 4 — Where the AI stops

The song stays at stage `fix` with 3 lyric mismatches and 1 health issue.

- **m26 (`len="9/8"`)** — the measure is over-full. Which note is spurious is a
  judgement about what the engraver wrote; a pass that guessed would corrupt scores.
  It has not been read at 400 dpi yet, and probably could be.
- **the three dropped slurs** — at 400 dpi a slur *is* legible, so this is no longer
  beyond an AI. It needs the slur written into the .mscx as a spanner, which is a
  different kind of edit from placing text.

Fix either and re-run `build.py`; the counts should fall to zero. The fixture is set
up so that costs nothing to try and nothing to undo.

## A note on method

The single biggest lever in this whole exercise was **resolution**. Reading the
score as whole rendered pages produced a confident, tidy and substantially wrong
conclusion -- "all `too_few`, therefore missing slurs" -- when the real cause was
mostly voices sharing the other staff's words. The same score cropped per system at
400 dpi overturned it in minutes, and a second pass with the crops resolved five
more lines that page-scale reading could not.

Two rules worth carrying to any scanned score:

* **Crop and zoom before concluding anything.** A whole A4 rendered small enough to
  look at cannot show a slur or a notehead.
* **Let the arithmetic check the reading.** Every correction above was predicted by
  the slot counts before it was encoded, and landed exactly. A reading that needs the
  numbers bent to fit is wrong.
