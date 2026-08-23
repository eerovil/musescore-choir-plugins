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

### What is left, and why

```
[too_few] m8-10   B1:  10 syllables for 15 slots     gap +5
[too_few] m8-10   B2:  10 syllables for 11 slots     gap +1
[too_few] m27-30  T1:  10 syllables for 15 slots     gap +5
[too_few] m27-30  B1:   8 syllables for 13 slots     gap +5
[too_few] m27-30  B2:   7 syllables for 12 slots     gap +5
[too_few] m31-34  B2:  17 syllables for 18 slots     gap +1
[too_few] m44-46  T1:  10 syllables for 13 slots     gap +3
[too_few] m50-52  B1:   9 syllables for 14 slots     gap +5
```

All eight are `too_few`, so nothing is silently dropped — every gap is notes left
without a syllable, never text thrown away.

**Missing slurs are real, but they are the smaller cause.** The m8–10 crop shows a
slur the OCR plainly lost, under the bass half-note approaching the 2/4, and its
text is fully printed there so sharing cannot explain the gap. Those two lines are
genuinely melisma damage.

**m31–34 B2** is the one line where the two causes meet. Its 18 notes fall 5/3/5/5
across the four measures: m31 matches its own printed line exactly (5) and m33–34
match the tenor's (5/5), so it is sharing — but m32 has one note fewer than the
tenor's four. The repeated "kaik-ki" was trimmed to fit rather than mangling the
hyphenation across the barline, leaving one note unset. The likeliest truth is a
melisma there.

**m27-30, m44-46, m50-52** were not re-read at 400 dpi. They may be either cause.

## Step 4 — Where the AI stops

The song stays at stage `fix` with 8 lyric mismatches and 1 health issue.

- **m26 (`len="9/8"`)** — the measure is over-full. Which note is spurious is a
  judgement about what the engraver wrote; a pass that guessed would corrupt scores.
  It has not been read at 400 dpi yet, and probably could be.
- **the dropped slurs** — at 400 dpi a slur *is* legible, so this is no longer
  obviously beyond an AI; it needs the slur written into the .mscx as a spanner,
  which is a different kind of edit from placing text.
- **the three unexamined systems** — cheap to settle, just not done.

Fix any of these and re-run `build.py`; the counts should fall further. The fixture
is set up so that costs nothing to try and nothing to undo.

## A note on method

The single biggest lever in this whole exercise was **resolution**. Reading the
score as whole rendered pages produced a confident, tidy and substantially wrong
conclusion; the same score cropped per system at 400 dpi overturned it in minutes.
Any future AI step against a scanned score should crop and zoom first.
