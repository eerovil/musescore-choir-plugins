# Tests

Part of this repository's agent guide; the index is [CLAUDE.md](../../CLAUDE.md). Moved out of it word for word (#513). The rules for running tests, then what each test module pins.

### Tests

**While working, run only the tests related to what you changed.** The whole suite
is CI's job, not yours:

```bash
.venv/bin/python -m pytest src/song_app/tests/test_publish.py -q   # one module
.venv/bin/python -m pytest src/score_export/tests/ -q              # one package
```

`.github/workflows/ci.yml` runs everything on every pull request and every push to
`main`, in two parallel jobs (the suite, and the browser tests on their own), and
finishes in a few minutes. The same run locally is serial and slower. Running it
before every PR was the habit from before CI existed (added 2026-08-25); there is no reason to keep paying it.

So: **verify your change with its own tests, push, and read CI.** Cite the CI run in
the PR body rather than a local "full suite passed" count.

Two things follow from that, because **nothing is gated**. `main` has no branch
protection and no required checks, and `scripts/deploy-song-app.sh` runs no tests, so a
merge is on the live app within two minutes. The deck merges a pull request by itself
once its review passed and every GitHub check is green on its head (agentdeck#3269), so
**a red or unfinished CI run is what holds a merge**. CI is the only thing that ran
the suite; a person's `/merge` or Merge press goes through the same gates.

The full run is still the right thing before a release you are nervous about, or when
you have touched something with reach (`lyric_txt.py`, `main.py`, `publish.py`):

```bash
.venv/bin/python -m pytest src/clean_score/tests/ src/song_app/tests/ src/score_export/tests/ -q
# fewer passed            — without Playwright the browser tests skip; without
#                           poppler the pdf_systems and bounds tests skip; without
#                           a MuseScore CLI the MuseScore tests skip too
```

**A poller verification job leaves the browser tests to CI** (agentdeck#2709). Submit
the full run with `-m "not browser and not omr"` and let it run on this host:

```bash
agentdeck job submit --verification ... -- .venv/bin/python -m pytest src/clean_score/tests/ src/song_app/tests/ src/score_export/tests/ -q -m "not browser and not omr"
```

The Playwright tests are the slow part, and CI's `Browser tests` job already runs them
on the same commit. The poller's release waits for every GitHub check on the head it
merges, and a red one gets a repair turn, so CI covers them; the local job does not
need to.
`not omr` keeps homr off this host: the `omr` tests run it on real scans, and CI,
which has no homr, skips them too.

One trap when timing or trusting it: the tests that call the MuseScore CLI do so
under a timeout, so anything else heavy running on the host at the same time makes
them fail for no reason of their own. Failures chased in this way have turned out to
be a concurrent job starving them; they pass in seconds alone.

The six extra are **browser tests** (`src/song_app/tests/test_ui_flow.py`, Playwright),
marked `browser`. They need a two-step install, and the module skips unless **both**
steps are done — the pip package alone is not enough, so a half install still skips
rather than erroring:

```bash
.venv/bin/pip install pytest-playwright
.venv/bin/playwright install chromium      # ~95 MB, into ~/Library/Caches/ms-playwright

.venv/bin/python -m pytest ... -m "not browser"   # skip them even when installed
```

`pyproject.toml` sets `log_cli_level=DEBUG` and registers the `browser` marker.
Key test modules:

- `test_lyric_txt_spanner.py` — asserts lyric export→import round-trips back to
  the original XML (the real behavioral coverage), driven through `export_lyrics` /
  `place_lyrics`.
- `test_json_staff_mapping.py` — lyric **routing**: builds a synthetic score, places
  a PDF-derived line, and reads back which output staff got the words (printed
  staff+position, per-system map, part names, explicit `parts` override).
- `test_lyric_diagnostics.py` — the structured `Mismatch` fields, measure_start
  inference, and the editor-grid → cells → blocks → import → editor-grid round trip.
- `test_lyric_hyphenation.py` — a word split by a barline stays one word. The JSON
  import cuts a line into per-measure chunks and writes each back out as text in
  between, and that text could say "carries on into the next measure" but not
  "carries on from the previous one", so every chunk starting mid-word was read as a
  fresh word. Four of the five fail without the fix; the fifth is the guard that a
  word inside one measure is untouched.
- `src/clean_score/tests/scorebuilder.py` — the shared synthetic-score helper those
  two use (not a test module).
- `test_simple1_split.py` — a **golden-file snapshot** test: it runs the split
  pipeline on `test_files/<name>_input.mscx` and compares the element-tag
  sequence against `<name>_output.mscx`. If you intentionally change pipeline
  output, regenerate the goldens by copying the freshly produced
  `<name>_test_output.mscx` over `<name>_output.mscx`. The comparison is
  shallow (tags only, not text/attributes).
- `test_per_system.py` — drives the per-system module's own interface against the
  real `laulun_aika.mscx` fixture (systems, layout, part order, per-system pull,
  tuplet survival, line breaks, lyric map/metaTags, carried-forward answers, answer
  persistence) and pins both assignment adapters — the CLI prompt and grid answers —
  to the same rebuild.
- `src/song_app/tests/test_omr.py` — added by this pull request. Most of it drives a
  stub standing in for homr, because what `omr.py` owns is the boundary: where the
  binary is found, that the MusicXML lands where the caller asked and the teaser
  litter does not follow it, that `--gpu no` is actually on the command line (and
  `--gpu force` once the venv says it has the GPU build, #418), that
  both streams reach the log, and that each way of failing says something — a bad
  exit, a clean exit with no file, a wedged run, a PDF handed in by mistake, homr not
  installed. It also pins the heavy slot: a page is read under one, held across the
  run rather than asked for and dropped; the label names the page; `queue=False`
  genuinely does not ask; and losing the lease mid-page stops the page *and* takes
  homr with it. The last test is the card's own acceptance and the only one that runs
  the real thing: a page of the scanned fixture rasterised at 300 dpi goes in and parseable
  MusicXML with parts, measures and notes comes out (~50s). It skips without homr or
  poppler, the same way the MuseScore-CLI and Playwright tests skip.
  A third half pins the **engines**: one install with no checkout beside it is one
  engine; a working copy and each of its git worktrees are engines labelled with the
  branch they have out now; a directory that is not a homr checkout is not one; there
  are no checkout engines without an install to borrow dependencies from; an engine
  that is not there is refused rather than quietly becoming the default; the weights
  are linked rather than downloaded again and a real file is never replaced; and the
  chosen engine's argv *and environment* are what read the page. `test_install_homr.py`
  pins the other end — the one venv, what it says about itself, and an explicit
  `HOMR_SOURCE` being its own label.
  The **slurs** half added for #113 moved to the fork with the repair (#144); what is
  left pins that the app passes homr's slurs through untouched and that a parse with
  nothing to change comes back byte for byte as homr wrote it.
  A third half is added for #164: the **whole-measure rests**. Most of it is little
  measures written note by note — a shared rest moves out, the notes behind it move back
  into the room it was taking (12 divisions, not 16, because the lost quarter rest is not
  invented back), a rest alone in its voice is left alone, a rest that is not a whole rest
  is left alone, a second shared rest gets a voice of its own too, the spare voice is
  reused from one bar to the next because MuseScore holds four to a staff, a chord moves
  with the note it is stacked on, and a measure with a `direction` among its notes is left
  alone. The acceptance runs on **a committed copy of the real parse**,
  `tests/test_files/shared_whole_rest.musicxml` — #130's bar, kept beside the test rather
  than read out of `songs/`, which is live and changed under that card once already. It
  asserts the bar stops overfilling and the bars either side of it are untouched. Then the
  seam: a parse with nothing to move comes back byte for byte, moving one twice finds
  nothing the second time, and `read_page` applies it, says so in the log, and hands the
  moves back to a caller that asked.
- `src/song_app/tests/test_omr_systems.py` — added by this pull request. Two halves.
  **Flattening** is tested with little documents in the shapes homr actually produced
  on the benchmark — four one-staff "Voice" parts, two two-staff "Piano" parts, and a
  mixture — so a fused grand staff still counts as two staves, the pair in the middle
  stays in the middle, the name is never read, and a staff comes out with its own clef,
  its own notes and its voices renumbered from 1. A group of them is about **where the
  notes land**, rewritten by this pull request for #172: two voices homr started together
  still start together, a voice homr wrote later in the bar stays later, a voice entering
  mid-bar enters mid-bar, a chord stays stacked on the note it shares a beat with, and the
  two staves of a fused part each start at the head of the bar. The last of those five is
  what the old rebuild was for; the other four are what it cost. Beside them is the
  acceptance on a **committed copy of a real parse**,
  `tests/test_files/voices_across_the_bar.musicxml` — one printed system of Herää Suomi as
  homr read it, kept beside the test rather than read out of `songs/` — asserting every
  note of it comes out on the beat homr put it on, and naming the bar the old rebuild
  moved a whole phrase in. A third group is added by this pull request for #187, about
  **which voice a note comes out on**: a voice homr wrote first does not become voice 1
  for it, a bar only the lower voice sings does not promote it, and a voice numbered 5 is
  still compacted, because compacting was never the defect. Beside them is that card's
  acceptance on two more **committed copies of real parses**,
  `tests/test_files/voice_rank_join_{first,second}.musicxml` — the two printed systems
  either side of one join of Kaksi laulua krapulasta 2, the join #173 measured losing 21
  points with every note present — asserting the higher singer holds voice 1 in every bar
  of both, and again once they are assembled into one part across the join. **Assembling** is
  tested on the seams: continuous bar numbers, a break at each join and none at the
  start, one `divisions` with the durations rescaled to it, a repeated key dropped and a
  changed one kept, a meter change inside a crop left alone, a resting column given the
  system's own bar length, and every part opening with something to read. The last test
  is the acceptance minus homr and the only one needing MuseScore: systems of 2, 3 and 2
  staves survive assembly, conversion and `per_system.system_layout` as those systems
  with those staves. Nothing here runs homr — whether it can read music is homr's
  business, and the card's own numbers came off the frozen benchmark, which is host
  state.
- `src/song_app/tests/test_scan.py` — added by this pull request, for the scan stage.
  Cropping and reading are stubbed (they belong to `pdf_systems` and `omr_systems`, and
  are pinned there), so what is under test is what this stage adds: that the band is
  padded and the padding is clamped to the page, that a failed band and a lost lease each
  cost only the band in flight while the rest are kept and the song stays on `scan`, that
  filling the hole re-reads only the hole, and then the whole invalidation chain — a
  moved band discarding its own fragment and answers and nobody else's, an inserted band
  making every later fragment fail to match the geometry now at its index, a re-read that
  came out different discarding that system's answers and the reviewer's approval, a
  re-read that came out the same discarding nothing, and a song that never scanned
  deriving nothing from any of it. Flattening and assembling are *not* stubbed: they are
  cheap, need no binary, and stubbing them would leave the seam a hole slips through.
  It also pins **leaving the stage** (#281, which removed the approval gate #116 added): a
  finished scan moves the song to Clean, a hole keeps it on Scan, a re-read that came out
  different leaves a song further on where it is (while still lapsing Review's approval),
  a re-read that leaves a hole sends it back, a song the old gate left on Scan moves on
  when read, an unmarked page is refused before any band is read while a missing poppler
  is not, and a hole has no fragment to render.
  It also gains the **record of a moved whole-measure rest** (#164): one is written down
  as an outstanding free-text fix naming the system, the bar and the voices; re-reading the
  same system does not say it twice; a re-read that moved nothing takes the sentence away;
  a sentence somebody typed is never touched; a system that could not be read records
  nothing; and a `fixes.json` broken by hand costs the record rather than the reading. The
  repair itself is not tested here — it belongs to `omr.py` and is pinned there.
  `test_fix_panel_ui.py` carries the browser end: the sentence a real scan would leave,
  written by `record_scan_repairs` rather than typed, showing on the Fix stage.
- `src/song_app/tests/test_scan_panel_ui.py` — added by this pull request: the Scan panel
  in a real browser, which is where most of #116 actually lives. It opens on the Systems
  editor with the Scan button waiting for the bands; a hole shows what homr said, offers a
  retry of its own and keeps the song on Scan; a whole reading moves the song to Clean with
  nothing to approve; and it all fits 390x844.
  Nothing here runs homr, poppler or MuseScore — the fragments are written into the song
  the way a scan would leave them, **with real band stamps**, or the app discards them all
  on the next read, which is the invalidation rule working rather than a test detail.
  It also pins the engine picker: hidden when one homr is installed, and when two are, the
  engine chosen in the browser is the binary `scan.run` is actually handed. And that a
  system which read fine can be read again — from the panel and from its compare row,
  each re-reading only the system named and saying what a changed reading costs.
- `src/song_app/tests/test_health_summary.py` and `test_health_findings_ui.py` — what
  is left of #170's verdict tests after #356 removed the verdict: the Review health row
  is a count and a warning with no verdict beside it and never a gate, a score with
  findings on most bars shows no banner on Review or Fix, the approve button still
  approves, and the Scan panel names the systems the findings fell in.
  `test_scan.py` carries the attribution itself — the bar-to-system mapping, a collapsed
  row shared over the bars it names rather than landing on the first, and the three ways
  of refusing to attribute at all.
- `src/song_app/tests/test_system_finder.py` — the grouping-rule tier moved to the fork
  with the rule (#144). **homr** (marked `omr`, ~70s): the acceptance,
  against the bands a person drew — the fixture's 15 across four pages and both Herää
  Suomi scans, each boundary within 0.02 of the hand-drawn one. The route and the button
  are pinned where they live: `test_bounds_api.py` (a proposal saves nothing, a page
  homr could not read is a 400 saying so) and `test_ui_flow.py` (the editor fills with
  the proposal, unsaved, and the song still holds what it held).
- `src/song_app/tests/test_benchmark.py` — added by this pull request, and the first
  thing in the suite that meets a **real scan**. Three tiers, so each dependency buys
  something and none is required. **No dependencies**: the manifest's files are on disk
  and its bounds are ordered non-overlapping bands whose bars run on; the truth table is
  re-derived from the hand transcription bar for bar; the page boundary is read off that
  transcription's own breaks; B2 is the song fixture's page rather than a second copy;
  and no PNG has crept in. **poppler**: each page crops into the bands its bounds name,
  each band wider than it is tall and holding a plausible amount of ink. **homr**
  (marked `omr`, ~3 minutes): both Herää Suomi scans and the Virta venhettä vie page
  read back as the staves the page prints and the bars the bounds declare — B1b at 150
  dpi with dropout has to match B1a — and B1a assembles into one 7-bar score with a
  break at each join. The last tier skips without homr or poppler, the same way the
  MuseScore-CLI and Playwright tests skip; CI has no homr and should not grow one.
  `benchmark.py` beside it is the manifest reader, not a test module.
- `src/song_app/tests/test_clean_flow.py` — the song-app path: grid answers →
  `save_system_answers` → headless `run_clean` → rebuilt parts + lyric routing.
- `src/song_app/tests/test_ui_flow.py` — the **SPA itself**, in a real browser: it
  starts the actual server on a free port with its own `songs/` folder and answer
  file, then walks New song → per-system grid → clean → manual lyric entry → import,
  and asserts the mismatch is attached to the cell that caused it. A second test pins
  the grid's answer rules (blank inherits, `-` clears, both flagged before cleaning).
  Score previews are switched off (`MUSESCORE_CLI_PATH` points at nothing) — the
  renderer is not under test and a real MuseScore run would make it slow and
  host-dependent. Both were verified by sabotage: breaking the cell attachment or the
  `-` rule in `app.js` fails the matching test.
- `test_shared_rests.py` / `src/song_app/tests/test_musescore_check.py` — added for
  #235. The first: a rest shared at the end or in the middle of a bar reaches both
  voices, a gap the other voice sings through or that its rests do not cover exactly is
  left, a rest in a triplet is not copied, and a second run changes nothing. The
  second: a reset bar is one whole-bar rest of the bar's own length naming the notes it
  held, the other staff and bars are untouched, a tie or slur into it is cut; the real
  MuseScore (skips without it) names a bar it refuses and accepts it once reset; no
  MuseScore is "not checked"; the sentence is written once, replaced on a re-clean,
  removed when the clean passes, and never touches a typed one.
- `test_cross_voice_slurs.py` / `test_long_bars.py` / `src/song_app/tests/test_clean_marks.py`
  — added for #238. A slur from one voice into the other loses both halves, is
  reported as one slur, marks both bars and gives the lower voice its syllable back,
  while a slur inside one voice is untouched. A bar an eighth too long comes back 4/4
  with the cut notes named in its mark; a note across the barline is shortened, a
  triplet across it goes whole, a voice off the beat grid is left short rather than
  padded wrong, a tie into the cut-away part goes, and short bars, bars printing their
  own signature and the bars either side are untouched. Then the marks: red, `⚠`,
  listed by health until deleted, said in the clean's log (no longer copied into
  `fixes.json`, #347), put on a bar MuseScore rejected, and absent from the
  publish copy.
- `test_missing_ties.py` — a tie is copied onto a voice singing the donor's rhythm,
  not onto an ostinato on the same pitch (#284), not past a rhythm that differs
  before the tie ends, and not between two pitches; any matching donor will do.
- `test_missing_tuplets.py` — the dropped-tuplet cross-voice auto-fix (mirror
  within/across staves; well-formed and donor-less voices left untouched).
- `test_revoice.py` / `test_interactive.py` — the re-voicing plan and the
  non-interactive anomaly reduction.
- `src/song_app/tests/test_repeat_question.py` / `test_repeat_question_ui.py` — added
  for #312: an end repeat with no start is asked about once, with the printed systems
  in between as its choices; a pick puts a start sign on every staff and comes back on
  a re-clean; **a** leaves the score alone and stays answered; the `repeat` kind
  refuses a bar that already opens one. The browser half is the card on a phone.
  `test_omr_systems.py` carries the assembly half (a sign read on some staves written
  on all, and MuseScore keeping it).
- `src/song_app/tests/test_volta_question.py` / `test_volta_question_ui.py` — added for
  #319: an end repeat with no bracket over it is asked about once, offering a "1."
  bracket of 1-4 bars (never reaching the repeat's own first bar) and "2." over the bar
  after; a pick writes both and comes back on a re-clean; **a** stays answered; the
  `volta` kind writes the same elements MuseScore wrote for Shakkitarina's hand-made
  brackets, refuses what it cannot draw, and (with MuseScore) exports as real endings.
- `test_unslur_tie_duration.py` — added for #340, on Annin laulu's own bars: a
  slur taken out loses both halves and gives the syllables back (a half written ahead
  of its chord too), a tie lands on both notes with MuseScore's own offsets and takes
  the held note's syllable, a double dot gives the 11/16 bar back its 3/4 on every
  staff while ties out of it keep their notes, a length no signature prints refuses,
  and (with MuseScore) the bar it had refused opens once the dot is back. `untie`
  (#342) takes both halves out across or inside a bar, gives the syllable back,
  leaves other ties alone, and replays on a rebuild.
- `test_dashed_arcs.py` — added for #482: a dashed tie or slur in a sung part is marked
  and kept, a solid or dotted one and an instrument part are not, a second clean marks
  once, and `untie` / `unslur` answer the mark while a new `tie` does not; taking out a
  solid tie, or one dashed tie of two, keeps it, and so does a part sharing the staff.
- `test_delete_mark.py` — added for #352, on the shapes of Mieslaulu bar 13 and Annin
  laulu bar 19: the named fermata goes and the chord, its words and the bar's other
  fermata stay; every listed kind and `subtype`; each refused kind names its tool; a
  red mark points at `unmark`; strict `from`; the other staff's fermata said; replay
  on a rebuild; and (with MuseScore) the MIDI shortens only once both fermatas are out.
- `test_delbar.py` — added for #346: the bar goes on every staff, the "1." and "2."
  brackets close up round it, a slur across it keeps both notes, fixes count bars in
  file order, `from` is strict, a bar with music or a spanner end in it refuses, the
  per-system lyric map and the removed-slur records lose the bar, `insbar` puts an
  empty bar in with the same care (Kristallen's insert-then-delete in file order), the entry replays on a rebuild, and a pick recorded after it survives the next clean.
- `test_chord_notes.py` — added for #358: `dropnote` takes one note off and keeps the
  chord's length and words, a tie into or out of the note loses both halves, the last
  note refuses; `addnote` lands in pitch order spelt by an octave, the key or `tpc`, and
  ties nothing; both are strict about `from`, replay on a rebuild, and (with MuseScore)
  the score still opens.
- `test_duration_back_steps.py` — added for #344, on the shapes of Gute Nacht bar 6
  and Annin laulu bar 10: a `duration` fix that restores the bar takes the back-step out
  of the voices it squeezed, a tie after the step still reaches its note, a voice the
  step did not squeeze to the bar, a forward gap and another voice's tie are left
  alone, a `bar` fix writes over a gap (Integer vitae T2 bar 9) and still has to fill
  the bar, and (with MuseScore) every voice of the bar opens.
- `test_overlong_voice_bar.py` — added for #350, on Lasinkuultava laulu T1 bar 9: a
  voice false triplets made 7/6 long in 4/4 comes back from one `bar` fix filling the
  bar, the other tenor untouched; a total that fills neither refuses naming both, and
  (with MuseScore) the bar it refused opens.
- `test_timesig_fix.py` — added for #353: a `timesig` fix takes the signature off
  every staff and leaves the notes, writes 6/8 over 3/4 without 3/4's beaming, refuses
  a staff reading otherwise (changing none), bar 1, a missing `from` and a change of
  bar length, counts bars after a `delbar`, and replays on a rebuild.
- `test_unvolta_barlen.py` — added for #378, on Suomalainen rukous's shape: the
  invented bracket and repeat go, both halves, and the endings come back "1." over one
  bar and a 6/4 + 4/4 "2." closing the score; each kind's refusals; replay on a
  rebuild; and (with MuseScore) the export plays one repeat with those endings.
- `test_read_bar.py` / `src/song_app/tests/test_record_slur.py` /
  `test_slur_panel_ui.py` — added by this pull request for recording a missing slur
  from the app. The first pins that reading a bar and writing a fix agree: the index
  shown is the index the slur lands on, the token shown is the token an `append`
  entry would carry, and a note is named the way the page spells it (Eb, not D#).
  The second pins the pair — entry written *and* score changed, the bar losing a
  syllable, the entry replayed by `apply_recorded_fixes` on a rebuild — and that a
  refusal (no reason, a span past the bar, a slur already there) writes neither. The
  third is the browser: the bar shown as its own notes, the cost said before the
  write, the warning when lyrics are already imported, and that it fits a phone.
- `test_rhythm_fix.py` / `src/song_app/tests/test_bar_readings.py` /
  `test_reading_picker_ui.py` — added for #269. The first pins the `rhythm` fix on
  Legenda bar 25's bass: the picked lengths and triplet brackets, pitches and lyrics
  untouched, ties following their notes, the red mark gone, refusals. The second pins
  where an offer lands (bar number across systems, an octave-shifted tenor, doubled
  voices one staff each, a changed bar not offered), the pick and its replay, "none of
  these", and a pick lapsing when its system is read again. The third is the browser:
  options drawn, one tap picks, and it fits a phone.
- `src/song_app/tests/test_problems.py` / `test_problem_list_ui.py` — added for #290.
  The first pins the rows (one per bar and part, a mark and its health row said once,
  a dismissed mark staying hidden, a removed slur asked once on its first bar), the
  `pitch` pick and its replay (an octave-shifted tenor, a rhythm pick on the same bar
  not losing the pitch offer), and the slur answers (drawn across the barline, marks
  off, back after a re-clean, "no slur" adding nothing, refusals). The second is the
  browser: one card for a bar with both kinds of doubt, a slur answered in words, and
  a phone. A tap keeps the reader's place (#329): the next card lands where the
  answered one stood, desktop and phone, since a redraw otherwise left the panel at
  its bottom — including when a `state` ping redraws it again while or after the
  tap's list loads, which the file watcher sends after a pick some of the time. #295 rewrote their reading half around whole bars: the ranking, the second
  reading always **b**, picking it writing the bar afresh and coming back on a rebuild,
  an octave-shifted tenor, an earlier pick counting as decided, and in the browser six
  shown with the rest behind "More". `test_bar_fix.py` pins the `bar` kind itself.
- `src/clean_score/tests/test_lyric_boxes.py` / `src/song_app/tests/test_lyric_boxes_api.py`
  / `test_lyric_editor_ui.py` — added for #397, the by-system editor. The first pins the
  model on little scores: an import changes only the boxes it sends, an emptied box
  takes its words out for that verse and voice only, a verse lands on its own `<no>`,
  a voice-2 box exists only where that voice has notes, voice 1's words go above the
  staff only where voice 2 has words, `e-lei- _ _ son` keeps `son` as the word's end,
  a word open at a system's end is continued by the next system imported alone, a
  long line runs into an empty system and stops at one with words, and a paste
  carries verses and second voices. The second is the routes on the Virta fixture
  (warnings kept per box, the staff positions found once per crop, "what landed"
  drawn from those bars). The third is the browser: one system with each staff's
  boxes under it, Next importing first and a warning keeping you there, verse tabs,
  the voice-2 box, "what landed", the list when staves do not match, and a phone.
- `src/song_app/tests/test_state_race.py` — added for #252. The file watcher used to
  save the whole song state it had loaded, so a lyric import that saved while the
  watcher was checking health was silently undone. It drives both interleavings (a
  copy loaded before the import, and the import landing mid-check) and asserts the
  import's `lyrics` record and stage survive. The rule it pins: `_rescan` writes only
  health and the fingerprint, onto the state re-read under `state.song_lock`, and
  every `Song.save` takes that lock and writes by rename.
  **A save merges, and the lock reaches other processes** (#498, `test_state_merge.py`). A
  clean held its start-of-run copy for minutes and saved it whole, erasing the publish record
  `sing_song.py --publish` wrote meanwhile. `state.load` remembers what it read (`_base`), and
  `Song.save` re-reads the file under the lock and writes only the top-level records this copy
  changed since then, whole; `publish`, `publish.sites`, `scan.systems` and
  `scan.answered_against` (`state.MERGED`) are merged a key deeper, so the panel's settings and a
  CLI's choir record both survive, and a scan's band save keeps a grid answer stamped meanwhile.
  `GET /systems` returns each system's reading (`stamps`) and the grid's `PUT` sends them back:
  answers about a system read again since the grid loaded are refused (409). The grid counts systems from 0 and `.systems.json`'s bands from 1. `_assemble` saves, with the score, which band each grid row came from and that band's reading (`scan.assembled_order`, built on first read for a song assembled before it); the grid's stamps, the answer stamps and the drop of a row's answers read that list, never today's bands, so removing two bands at once drops both rows' answers, and answers for a row whose band was read again since the assembly are refused until a Scan assembles it (409, "Press Scan"). The PUT checks, saves the stamp and writes the answers in one `song_lock` hold, stamp first. `.persystem_cache.json` holds every song's answers, so `per_system.update_answers` writes it under an `flock` on `.persystem_cache.json.lock`, re-read inside the lock, through a temp file and `os.replace`, and refuses (`AnswerFileError`) a file that does not parse rather than overwrite it. Take another copy's
  data with `song.adopt(fresh)`, never `song.data = fresh.data`, which drops the base and saves
  whole. `song_lock` is re-entrant and also takes an `flock` under
  `~/.local/state/musescore-choir-plugins/song-locks/` (the home from `pwd`, not `$HOME`), so
  `sing_song.py` and `publish._move_records` wait for the app and the other way round.
- `src/song_app/tests/test_score_file.py` / `test_score_file_ui.py` — added by this
  pull request for taking the score away to MuseScore and bringing it back (#216).
  The first is mostly about what must **not** be replaced: a PDF, a transfer that
  truncated, MusicXML rather than MuseScore, a score with no music in it, a `.mscz`
  with nothing inside — each refused with the score on disk untouched — plus nothing
  replaced underneath a running job. Then the half that has to happen: the file comes
  down under its own name, a `.mscx` and a `.mscz` both land, the health record is
  re-taken against what arrived, and an approval given against the old score lapses.
  The second is the browser: the download really saves under the score's name, a
  picked file reaches disk and the panel still says so after its own refresh, and a
  refusal is said in the panel rather than only to a log nobody has open. Neither
  needs MuseScore. Screenshots go to `EVIDENCE_DIR` when the run names one, so none
  is committed here.
