# `omr_systems.py`: one printed system at a time

Part of this repository's agent guide; the index is [CLAUDE.md](../../CLAUDE.md). Moved out of it word for word (#513). These are bullets of the guide's section on the song web app, which is split over [song-app.md](song-app.md), [song-app-panels.md](song-app-panels.md), [omr.md](omr.md), [omr-systems.md](omr-systems.md) and [scan.md](scan.md), in that order; "above" and "below" in the text may point into a sibling file. Here: flattening, voice numbering, assembling, the meter plan, and the measurements behind them.

- `omr_systems.py` is added by this pull request, and it settles what the unit of work
  is: **one printed system, not one page.** Given a page, homr builds a part by taking
  staff index N out of *every* system it found, which assumes the score is a rectangle
  — the same staves, in the same order, in every system. Choral engraving is not a
  rectangle: a part that rests through a system is simply not printed, so a page really
  can be 2-3-2-3-3 staves. When the counts disagree homr deletes an edge system and,
  failing that, breaks every group into singletons, which is how B5 — four vocal staves
  — came out of a whole-page scan as one monophonic line of 70 bars. Given **one**
  system there is nothing to reconcile and the assumption becomes vacuous rather than
  wrong. That is why #105 was closed without a fork change: the defect does not occur
  on this input.
  Three calls, and the middle one is the idea. `read_systems` crops each band
  (`pdf_systems.crop_systems`) and reads it with `omr.read_page`, **one heavy slot per
  system** — the per-page lease taken one step further, which is if anything better:
  shorter holds, so a test suite waiting behind it waits less, and an
  interruption costs the band in flight rather than the page. `flatten` reads a
  system's MusicXML as an ordered list of **staves**, splitting a part on the `<staff>`
  its notes carry, so a fused two-staff "Piano" contributes two exactly where a pair of
  "Voice" parts would. **`part-name` is never read** — homr says "Voice" and "Piano"
  and means neither, and since the notes of a fused part are fully separable,
  grand-staff fusion is a labelling detail with no information loss *in flattening*. homr
  itself fuses braced staves before decoding, though, and on a crowded bar the fused pass
  can drop noteheads: before blaming the model for a lost note, re-read the band one staff
  at a time and check `--output-confidence`. `assemble` writes
  the systems out as one score, one part per staff column.
  **Repeat signs and volta brackets are the whole system's** (#312,
  `_system_barlines`): one staff reading one is written on every staff of that system,
  a left barline ahead of the bar's notes. homr reads a start sign at the head of a
  system on some staves and not others, and MuseScore 3 keeps a start repeat only
  when every part carries it — Kantajani bar 27, read on two staves of four, came out
  of the conversion with no repeat at all.
  **What flattening must not do is move the notes, and until this pull request it did**
  (#172). Splitting a part on its `<staff>` means the `<backup>` and `<forward>` homr
  wrote cannot be kept as they stand — they step between staves as well as between
  voices, and the other staves are about to go — so they were dropped and every voice was
  re-laid from the head of the bar. But that is exactly *how homr says when a note
  sounds*: a voice it wrote later in the bar slid to beat one, and the phrase under it
  came with it. Issue #166 measured it while answering a different question and it is the
  largest single loss that map found: **73.5% of the notes right where homr's own reading
  of the same crop scored 92.3%**, 43 of 61 systems losing notes, one going 100% to 0.0%.
  For scale, the per-system seam that card spent a corpus run measuring costs 1.2 points.
  So the bar is now **read by following homr's cursor** — a note moves it on, a `backup`
  winds it back, a `forward` moves it on — which gives every note the beat homr put it on,
  and the staff is written back out voice by voice with the steps that put each note back
  there. The two things the rebuild existed for survive: voices are still renumbered from
  1 (a voice number means nothing outside its part, and this staff is becoming one), and
  the two staves of a fused part still each start at the head of the bar — but now because
  the backup between them says so, rather than because everything was reset. A chord note
  is the one case that takes no step, since it sounds *with* the note before it rather
  than after it. `scripts/flatten_vs_reference.py` is the measurement, committed by this
  pull request because #166's own harness did not survive that session.
  **Which voice a note comes out on is decided once for the staff, and this pull request
  proposes that** (#187). Renumbering from 1 is still necessary and still done, but it
  used to be done again in every bar, from the order the notes were *written* in — which
  is homr's interleaving and not a fact about the music. Two singers therefore swapped
  places whenever homr happened to write the lower one first, and a bar where only one of
  them sang compacted whichever singer that was down to voice 1 and handed the part back
  afterwards. Every note was present, at the right pitch, on the right beat, in the wrong
  part: **no health check sees that** — both voices are well-formed and the bar adds up —
  and a singer meets it as somebody else's line in their practice track, which is #148's
  second-worst error kind in its most confusing form. #173 measured it on Kaksi laulua
  krapulasta p4 — three staves in all five systems — as **62.9% assembled against a
  flattened 99.7%, with 119 voice faults and zero pitch, size or timing faults**. Now the
  staff's voices are numbered once, in the order of **homr's own numbers**, and that page
  assembles at **99.7% with one voice fault**.
  **Every number in this paragraph was re-measured for #192 on the homr this host runs**,
  `main @ 6c3bbf4`, because #187's came off band parses read by `b1c9203` and the engine
  has moved since. Same corpus — #173's 63 bands over 14 pages of Herää Suomi, Kaksi
  laulua krapulasta 2 and Käyttäytymisohjeita — re-read at 200 dpi and scored by the
  fork's own `compare_output` at the same commit as the engine. Read as homr writes them
  the bands score **92.5%** (`b1c9203`: 90.3%); flattening's own cost is **0.4 points**
  and it adds **no** voice faults (49 before, 49 after); and the fourteen assembled pages
  go **62.2% with 257 voice faults** under the old per-bar rule to **69.1% with 40** under
  this one. So what #187 claimed still holds, and the engine's own reading of these bands
  has improved slightly rather than drifted. **These are #196's settled digits, not #192's**
  — that card scored them with a comparator that ranked a staff's voices off the first
  moment either was seen, which one invented and never-scored note can reverse (see the
  retraction below), and it published 91.1% / 93 on the bands and 68.2% / 67 assembled.
  #196 replaced the rule and re-scored the same cached parses, reproducing #192's figures
  exactly with the old rule first, so what moved is the scorer and not the run. Two figures
  above are **not** #196's and are marked as such wherever they are used: the `b1c9203`
  90.3% and the old per-bar rule's 62.2% / 257 both pre-date it and were not re-scored, so
  read those two gaps as large rather than as exactly 2.2 and 6.9 points.
  One trap is worth recording because it nearly produced a fake regression. `86d0f2a`
  (#153) writes a head drawn with two stems into **both** voices, as the page prints it,
  and the `b1c9203` scorer collapses a unison on the reference side only — so every
  recovered unison reads as "a different number of notes here". Scored that way today's
  parses come out 9.3 points *worse* with 49 of 63 systems apparently regressing and not
  one improving, which is the shape that gave it away. **Re-measuring an engine means
  moving the scorer with it.**
  **Ordering by which voice sounds higher was measured and refused, and nothing since has
  given a reason to reach for it.** It is the tempting rule — MusicXML's convention is that
  voice 1 is the upper line — and on `b1c9203`'s parses it scored about the same overall
  (68.7% against 68.3%). Re-measured for #192 it scores **67.7% with 84 voice faults against
  68.2% with 67**, and the only page it changes at all is Herää Suomi p3, where the two
  basses cross on the page and ranking by height swaps a column homr had numbered right —
  **95.8% and 3 voice faults down to 85.6% and 20**. Those four figures were scored by the
  comparator **#196 has since replaced** and were not re-derived, so read the p3 swap as
  large rather than as exactly 10.2 points. Both sides of each pair come off one rule, which
  makes them a fair comparison **under that rule**; it does not make the gap invariant under
  #196's, since a new ranking can move the two sides by different amounts, and that has not
  been measured. What does not move with them
  is the crossing-voices argument, which is a fact about p3's engraving and not about any
  score: where two voices cross, "voice 1 is the upper line" is false of the page itself.
  Homr's own numbering is also the better claim where the two disagree, putting the higher
  voice first in **341 of the 357 corpus bars carrying two against 316** for the order the
  notes happen to be written in.
  **#196 reached the same refusal from its own side of the fence, and that is corroboration
  rather than the same measurement.** Its question was which rule the *harness* should rank
  a staff's voices by — a different rule in a different program, see the two paragraphs
  below — and it measured mean height at **92.3% with 55 voice faults on the bands and
  69.1% with 40 assembled**, against **92.5% / 49 and 69.1% / 40** for counting which voice
  is the higher line in more of the staff's bars. It shipped the bar count, and what decided
  it was `heraa-suomi-final-s10`, where the two basses cross and the reference's own two
  voices sit **0.1 of a step apart** in mean height over 12 notes against 3: a mean picks a
  winner on nothing there and reports 6 voice faults on a system read note for note
  correctly. So crossing voices defeat a height rule on both sides of the boundary, for the
  same reason, on figures that are now settled rather than disputed.
  **Herää Suomi p1 is not evidence of anything, and this is a retraction** (#190, and the
  claim #194 merged here). Both this file and the two docstrings used to say that homr
  numbers that page's two upper voices one way round in systems 1 and 3 and the other way
  round in 2 and 4, at a cost of **30 voice faults** the assembler could not honestly fix.
  It is not so. Each of the four crops agrees with its own reference — **0, 0, 1 and 0**
  voice faults, system 4 note-for-note perfect — homr numbers voice 1 the higher line in all
  four, and the bars the page-level score faults are correct on both sides, printed out note
  for note. The 30 are the harness's: `fixturecheck.compare._voice_rank` ranks a staff's
  voices by which is **seen first**, and the first moment of staff 1 on that page is one note
  homr wrote a quarter early into voice 2 — a moment the reference does not have, so the
  comparison never scores it while it reverses the ranking of every bar that is scored.
  Replacing that one rule takes the page **44.5% to 70.9%** and its
  voice faults **30 to 1**, which is the same 1 #192 reached by swapping two systems' voices
  and reached by accident. **#196 is the fix and it has landed** (`eerovil/homr#37`, merged
  2026-09-06): the harness now ranks a staff's voices by which is the higher line in more of
  its bars, so p1 stands at **70.9% with 1 voice fault** and the corpus totals above are
  #196's rather than #192's. Mean height reaches the same p1 figure and was measured
  alongside it; the bar count won for the reason given two paragraphs up.
  **What is real there is small and is homr's**: in bar 1 voice 2 enters a quarter early,
  where the page prints the two in unison entering on beat 2. That is a rhythm misread, so
  under the #141 rule it is the fork's — its cost as a note error is one row, and its cost
  as a measurement error was the other 29.
  **None of this weakens #187's refusal to sort the assembler by height; it sharpens it.**
  The two rules are not the same rule and must not be run together in the prose. The
  *harness* needs a **whole-staff** rule, because it has to line one file's voices up against
  another's and first-seen is not a property of the music; #196 shipped counting bars, having
  refused mean height for the crossing-voices reason the assembler refuses it for. The
  *assembler* must not use a height rule at all,
  because ranking by height is a claim — that voice 1 is the upper line — which p3 shows is
  false wherever two voices cross, and because the assembler's job is to stop scrambling
  what homr already said rather than to say something of its own.
  **And voice assignment is no longer the largest remaining boundary-layer loss** (#192).
  It was, when #187 was written; it is now the smallest of the four fault kinds the harness
  counts. The corpus loses 23.4 points between a band as homr wrote it and the assembled
  page — 92.5% to 69.1% — and **0.4 of that is flattening**. All the rest is one thing, and
  it splits by page shape. Over the 10 pages every system of which prints the same number
  of staves, assembly costs **about nothing**: 91.9% per-system, 91.3% assembled, 18 voice
  faults. Over the 4
  where the staff count varies between systems it costs **65 points** — 93.5% to 28.1% —
  and 592 of those faults are `size`, against 22 voice. These are #196's settled figures,
  and the split #192 argued from survives its re-scoring intact: what the old ranking bug
  moved was the voice count, which is the small side of it either way.
  **That 65 points is what an unanswered file scores, and #195 measured how much of it is
  the file rather than the answer.** The harness scores `scanned.musicxml`, which is a
  positional intermediate — `_fill_column` fills a short system's staves from the top and
  says so, and naming the rows is the `--per-system` grid's job, which the harness does not
  run. So this pull request proposes reporting the same four pages with the grid **answered**
  the way an operator would, from the reviewed score's own per-band grouping: cleaned in
  per-system mode and imploded back to the page's printed shape, the pipeline the reference
  itself came out of. Same parses, same scorer, same references.
  `scripts/answered_vs_reference.py` is that measurement, committed for the same reason
  `scripts/flatten_vs_reference.py` was: it re-scores parses somebody else already made, so
  it needs no homr and the claim below can be checked rather than taken.
  **And the answer it was made with is frozen** (`fixtures/answered-pages.json`,
  `scripts/answered_pages.py`), which is the difference between a committed script and
  committed evidence. The grid answer used to be read live — the bands out of
  `.systems.json`, the grouping out of the reviewed cleaned score — and both are host state
  that has moved under earlier measurements on this map already, so the same script on the
  same cached parses could later answer a different question and print a number with nothing
  saying the question had changed. The manifest records each band's index, its printed bar
  range and the staves the grid was answered with, and the scoring path reads that and never
  a song. A page nobody froze is refused rather than read off the host, and a song that has
  moved under a frozen page **stops the run** naming the band and both readings; `--record`
  is the one way to write it and says what it changed.
  `src/clean_score/tests/test_answered_pages.py` pins the refusals — a grouping edited
  underneath a frozen run, a band dragged, a band inserted, a page never frozen — and that
  the grid handed to the rebuild comes off the manifest. It needs no songs, no homr and no
  MuseScore.

  | 4 varying pages | per-system | assembled | assembled, grid answered |
  | --- | --- | --- | --- |
  | | 93.5% | 28.3% | **77.2%** |
  | `size` faults | | 592 | **127** |

  Answering the grid returns **48.9 of the 65 points**, and the control says the pipeline is
  not what did it: over the 10 uniform pages the same round trip costs **1.0 point**
  (89.8% → 88.8%), so it neither flatters nor punishes. Corpus-wide, the fourteen pages read
  **84.4%** answered against 68.2% unanswered.

  **And the 16.3 points left are not row placement at all.** They are on two pages, and both
  are one thing: a crop that read **one bar more than the page prints** — Kaksi laulua
  krapulasta 2 p2 s9 and p3 s12 each read 5 bars of a 4-bar system, the only two bar-count
  disagreements in the corpus — after which the assembled score's bar numbers and the page's
  part company and every later bar is compared against its neighbour. Scored up to that
  point those pages are **95.5%** and **97.9%**, against per-system 96.0% and 98.3%: with the
  grid answered, assembly costs **nothing**. The other two varying pages, which have no extra
  bar, come back at 84.4% and 92.3% against per-system 86.3% and 94.4%. Under the #141 rule
  the extra bar is homr's — the parse disagrees with the page — but nothing in the app
  notices it, though `pdf_systems.label` already knows each band's printed bar range and
  `read_systems` could compare the two.
  **So `_fill_column` is not losing music, and it should not start guessing.** The tempting
  fix is to work out which voice went silent from the clef, the part's range across the join,
  or the row it held in the neighbouring systems. It would not help, because a short system
  is usually not a silent voice: of the 9 narrow systems on these four pages, **2** are a
  voice resting (Kaksi laulua p2 s8 and p3 s11, both tenors) and **7** are divisi printed on
  two staves in one system and combined onto one in the others — Käyttäytymisohjeita prints
  T1 and T2 apart in exactly one system of each page and together in the rest. There the
  narrow system's first staff carries *two* of the reference's rows, so no assignment of it
  to a single row is right, and no evidence in the pixels changes that. The judgement is
  which parts a staff carries, not which row it sits on, and the grid already asks for it.
  What the numbers above do settle is that **every assembled figure this map has quoted is a
  number about an unanswered file**, including the corpus 69.1% above.
  **Every figure in this #195 paragraph and its table was scored by the comparator #196 has
  since replaced, and none of them has been re-scored — the differences included.** They are
  quoted here as #195 measured them, so the two columns of the table are like for like and
  the 28.3% in it is #195's own rather than the 28.1% the paragraph above now carries.
  **The gaps are not exempt from that, and an earlier version of this paragraph said they
  were.** It argued that #196 could move the *level* of a score but not the *difference*
  between two scores made with one comparator. That does not follow: replacing the ranking
  rule can move the answered and the unanswered score by **different** amounts, in which
  case the gap between them moves too. Both sides sharing a comparator makes the 48.9 points
  the grid returns, the 1.0 point the round trip costs, and the 84.4% against 68.2%
  corpus-wide honest **historical** measurements under the superseded rule; it does not make
  them invariant under the new one, and nobody has checked. Herää Suomi p1 shows why the
  intuition is tempting and not why it is safe: its 30 spurious voice faults sat in the
  uniform control on both sides and cost that page 0.9 points either way — one page where the
  error happened to cancel, which is an example rather than a proof.
  So the qualitative finding — that answering the grid returns most of the 65 points, and
  that the pipeline round trip is not what did it — is **plausible and unverified**, which is
  why what is claimed above is that most of the 65 comes back rather than that the answered
  corpus is exactly 84.4%. Settling it takes one measurement and no argument: re-score #195's
  cached answered and unanswered outputs with #196's shipped rule and publish the new pair.
  That needs the fork's harness inside homr's venv and is not on this map yet.
  **Which voice is absent from a short system is not decided here**, because it is not
  recoverable from pixels — you need the words, the range, or the piece. Columns are
  filled from the top and the empty rows are measure rests; naming them is
  `clean_score`'s `--per-system` grid's job, and that grid already asks a person.
  Assembly closes the seams that exist only because each crop is its own document: one
  `divisions` for the score with every duration rescaled to it, continuous bar numbers
  instead of bar 1 five times over, a key or time signature written only where it says
  something that was not already true, and a `<print new-system="yes"/>` at each join so
  the grid cuts the score where the page is cut.
  **A slur or tie over a line break is joined here** (#318). Each crop is read alone, so
  homr writes it as a start in the system's last two bars and a stop in the next one's
  first bar, and keeps exactly those loose ends (eerovil/homr, `resolve_slurs`,
  `EDGE_BARS`; this module's `EDGE_BARS` must agree). `_join_slurs` pairs them within
  one staff column when both systems print the same number of staves: last note to
  first note at the same written pitch first, as a tie, then the rest in reading order.
  homr writes one arc mark per note, so a slur and a tie both ending on the next
  system's first note come back as two starts and one stop; the slur is given the
  tie's stop note and marked `⚠ slur?`, since that stop was inferred. A half with no
  partner is marked `⚠ slur?` -- never dropped on a guess that it was a tie, since the
  same pitch across the break may be another staff's; only a half on a note already
  tied that way goes quietly. Measured on the six songs of #274 (71 systems): 13
  slurs and 23 ties restored over breaks, 28 marks.
  **The meter is decided here, and this pull request proposes that** (#177). homr has no
  token for a numerator — its vocabulary holds only `timeSignature/<denominator>` — so the
  number of beats does not exist in what the model can emit and is inferred afterwards
  from how long its own decoded bars came out, one crop at a time. #174 measured what
  that costs and what it does not: forcing the correct meter into a re-read of the Virta
  venhettä vie m11–m14 crop moved `<beats>` and left every other byte of the MusicXML
  identical, so **the notes are already right and the label is the only thing wrong**.
  That is also why the correction lands here rather than in the fork under the #141 rule:
  what is missing is not a misreading but information the crop does not contain, created
  by a seam that is ours (#103). A crop holding 2/4, 2/4, 4/4, 4/4 has no median that is
  right about any of it, and `assemble` is the one place that sees the bars either side
  of a join and every staff at once.
  So `_meter_plan` takes the **numerator from the length of the bars each signature
  governs**, keeps the denominator homr actually read, and carries the meter across a
  seam rather than restating the crop's own fresh guess. It corrects a signature printed
  *inside* a crop too, because the number is inferred wherever it stands — m13's printed
  4/4 came back 3/4 — while a meter change the page really prints (B4's last system goes
  3/4, 5/4, 4/4) stays exactly where it is, and a resting column's measure rest is
  written to the reconciled length rather than to the crop's label.
  **What it refuses to do is as much the point.** A span needs `_MIN_BARS` (2) **bars**
  and a strict majority among them before it may overrule a declared number, so one voice
  short of a note cannot rewrite the meter around its own mistake — the bar stays one that
  contradicts its signature, which is what the health check reports. Bars and not staff
  copies of a bar: two staves of one bar are one reading, so counting them separately
  would reach the threshold inside a single bar and a duration misread the same way on
  both staves would carry it. For the same reason a bar whose staves disagree about its
  length is no observation at all. A whole-measure rest is not one either (homr writes one
  a whole note long whatever the meter, so counting it would drag every span with a
  resting staff towards 4/4), and a length no whole numerator fits is left with the
  signature it was given.
  **A signature only a minority of the staves read is dropped** (#480), before any of
  the above: one that fewer than half of the staves having the bar carry, that differs
  from the meter in force, over a bar whose staves agree it is as long as that meter.
  homr wrote a 1/1 on one of six staves of a 3/4 bar, and since a one-bar change needs
  only that bar as evidence, the assembled score carried 1/1 on every part there. The
  drop is logged (`Bar 5: dropped a 1/1 time signature only 1 of 6 staves read`). A
  minority signature over a bar that really changed length, or one half the staves
  read, is left to the rules below.
  **One bar is enough against a signature that only restates the meter already in force**,
  and that exception is what keeps the correction working at all. homr writes a signature
  at the head of every crop and again wherever its own decoding wobbled, and "the same as
  before" is not a reading of the page — Virta's m13 prints 4/4 and the crop restated the
  2/4 in force, over a bar both staves read as four quarters. A *change*, and the score's
  opening declaration, both still need two bars, so a one-bar meter change the page really
  prints survives being measured against the single bar it governs. Under the flat
  two-bar rule the fixture keeps its m13 fault and health reads 33 rather than 31.
  Measured on the fixture with the fork's `main` (`6c3bbf4`), all 15 systems re-read at
  200 dpi: the bars carrying a meter the page does not print go **4 → 1**. The one left
  is m10, where the page prints 2/4 and homr read two and a half quarters — a note error,
  which this card is explicitly not allowed to hide. Health stays at **31 findings**, and
  that is the honest result rather than a disappointing one: the four mislabelling
  findings at m12 are gone, and four at m10 have appeared because the wrong label there
  had been *exempting* the bad bar from the check (`unprinted-meter` skips a bar that
  declares a signature of its own).
  Two things this cost. **Bounds become a precondition**: `.systems.json` exists for 5
  of 48 songs and is made by a person dragging in the Systems viewer, or by an AI
  reading `page_images(grid=True)`. Nothing here detects them — #80 measured that and
  it failed badly — so `read_systems` refuses with no bounds and says where they come
  from. And **the crop's resolution turned out to matter**: at 300 dpi, the dpi the
  whole-page benchmark used, B4's first and fifth systems came back as one staff each
  when the page plainly prints two and three; at 200 dpi every system of all seven
  benchmark pages came back with the staves the page prints. `SCAN_DPI` is therefore a
  measured default (env `OMR_SCAN_DPI`), worth re-measuring rather than nudging if a
  page ever comes back short of staves.
  Measured on the frozen benchmark at 200 dpi, ~10s a system: B5 comes out **4-4-4**
  (against a 70-bar single-staff collapse), B4 comes out **2-3-2-3-3** with 18 bars
  (against 26 bars on two staves), and the five two-staff pages — the majority of this
  repertoire — come out of `clean_score` with **the same parts and the same bar counts**
  as the whole-page route. `scan.py` is what calls it; the panel is #116.
