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
- **m26 arrives with one voice short of the others** — three voices read as 9/8,
  the tenor as 8/8, and an explicit `len="9/8"` on the measure
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

**Result at the time: one open health issue**, `malformed-m26-s1-v0` — "voice 1
fills 1 of 9/8". It is now repaired automatically; see step 5.

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
[too_few] m31-34  B2:  17 syllables for 18 slots     gap +1
[too_few] m50-52  B1:  13 syllables for 14 slots     gap +1
```

A third residual turned out **not** to be a slur at all. m8–10 B2 had 11 slots for
its 10 printed syllables, and the obvious reading was a lost melisma — but the XML
shows the tie is present and intact:

```
m9:  quarter/p48  eighth/p46  eighth/p45  half/p46+Tie
m10: quarter/p46+Tie  rest/eighth  eighth/p46
```

The uncounted note is the lone eighth *after the rest*, aligned with where the
tenors sing "ja" — the convergence into the next shared phrase, not a melisma.
Adding "ja" made it 11 for 11. **Check the XML before calling something a missing
slur**; a gap of one looks the same either way from the counts alone.

The two that remain are melismas, and both are visible as such:

* **m31–34 B2** — from m32 both bass voices move with the tenors, and B2 has three
  notes against their four, one of them a half-note sustained while the others move.
* **m50–52 B1** — one note more than the tenor line it doubles, in m50. m51 has its
  slurs; m50 does not.

Neither is a lyric error. The text is right and the score is missing two slurs, so
the fix is a spanner in the `.mscx` — by hand in MuseScore, or written in — not an
edit to `lyrics.json`.

## Step 4 — Where the AI stops

The song stays at stage `fix` with 2 lyric mismatches and 1 health issue, and both
are now localised to single notes:

- **m26** — fixed, see step 5.
- **two dropped slurs** — m31–34 B2 (the sustained note in m32) and m50–52 B1 (in
  m50). Writing a slur into the score is the first edit here that changes the
  *music* rather than the text about it, which is why it stopped for a decision
  rather than proceeding.

## A note on method

The single biggest lever in this whole exercise was **resolution**. Reading the
score as whole rendered pages produced a confident, tidy and substantially wrong
conclusion -- "all `too_few`, therefore missing slurs" -- when the real cause was
mostly voices sharing the other staff's words. The same score cropped per system at
400 dpi overturned it in minutes, and a second pass with the crops resolved six more
lines that page-scale reading could not.

Three rules worth carrying to any scanned score:

* **Crop and zoom before concluding anything.** A whole A4 rendered small enough to
  look at cannot show a slur or a notehead.
* **Let the arithmetic check the reading.** Every correction above was predicted by
  the slot counts before it was encoded, and landed exactly. A reading that needs the
  numbers bent to fit is wrong.
* **Read the XML before blaming the OCR.** The one gap confidently called a missing
  slur had its tie present all along.


## Step 5 — m26, and a repair that went wrong first

The health check's one issue. The reading that looked obvious was that m26 is a
plain 4/4 bar — the page shows dotted-quarter, eighth, quarter, two beamed eighths,
which is exactly 8/8 — and that the OCR had added an eighth to three voices in three
different ways: a trailing rest (T2), a repeated notehead (B1), and a `location` gap
(B2). A pass was written to strip all three.

**It deleted a real note.** The lyric import said so immediately: B1 went from
fitting its line exactly to one syllable over. That voice sings six syllables the
page prints — "vie, mi-hin päät-ty-vi" — and it has six notes to put them on.

The consistent reading is the opposite one. **The bar really is 9/8.** Three voices
were independently read as 9/8 and only the tenor came out short, which is what the
health check said in the first place. The repair is to pad the short voice, and
`fix_overfull_measures` does exactly that and nothing else — it only ever adds rests.

Two things worth keeping from the wrong turn:

* **The lyric arithmetic caught it.** Nothing else would have: the score was
  well-formed after the bad repair, the health check was satisfied, and the deleted
  note was musically plausible. The syllable count was the only witness.
* **Length has to be measured the way MuseScore measures it.** The first version
  ignored `location` gaps, so it saw a voice as complete when the file had it
  occupying an extra eighth — which is how it came to look for a culprit note at all.

The fixture now cleans to **no health issues**.
