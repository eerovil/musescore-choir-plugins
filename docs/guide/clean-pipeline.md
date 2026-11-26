# The voice-splitting pipeline

Part of this repository's agent guide; the index is [CLAUDE.md](../../CLAUDE.md). Moved out of it word for word (#513).

## How the voice-splitting pipeline works (`src/clean_score/main.py`)

`main(input_path, output_path, add_staffs=None)` parses the
`.mscx` XML and transforms it in passes:

0. `fix_missing_tuplets` (`utils/missing_tuplets.py`) repairs OCR measures where a
   tuplet bracket was dropped from one voice but a parallel voice (any staff, same
   measure index) kept it. It only touches a voice whose ticks don't add up *and*
   where a donor tuplet matches by tick position + base duration + note count, then
   copies the tuplet onto the run and pads the leftover with a rest. Never guesses a
   tuplet without a donor. Runs first (before any split/rebuild), all modes.
0b. `fix_spurious_timesigs` (`utils/spurious_timesigs.py`) removes OCR TimeSig changes
   contradicted by the note content — a change to e.g. 2/4 whose measure actually
   holds 4/4 (matching the *prevailing* meter) is dropped from every staff. It keeps
   genuine changes (content matches the declared sig) and never touches the first
   signature or a measure whose content matches neither. Exact-Fraction durations
   (tuplet/dot-aware). Runs before any split/rebuild, all modes; fixes the per-system
   case where a stray 2/4 made ~18 measures render over-full.
1. `preprocess_corrupted_measures` fixes measures with bad tick totals, then
   `fix_overfull_measures` (`utils/overfull_measures.py`) handles what it declines.
   That pass is all-or-nothing — it shortens the final rest of every over-long voice
   and gives up if any of them ends on a note — so a measure that is consistent
   *except for one voice* stays broken. The second takes the **prevailing meter** as
   the target, requires a voice that already fills it as a witness, and strips only
   what is **not music**: a `location` gap or a trailing rest, and only when it
   accounts for the overrun exactly. It never removes, shortens or adds a note; a
   voice that would need one is left for the health check. The one thing it writes
   rather than removes is the length of **silence**: a voice resting through the bar
   was written to the length the override declared, so once the override goes it
   overruns the corrected bar and MuseScore plays the measure longer than it is
   engraved. No health check sees that — it showed up as a scrolling video the
   (since removed) renderer refused, 92% of the played notes having no highlight — so such a voice
   becomes a measure rest of the real bar. Lengths count `location`
   gaps, or a voice looks complete on its notes while the file has it occupying more
   of the bar. The fixture's m26 cost two wrong versions before that shape — see its
   STEPS.md.
1b. `share_rests` (`utils/shared_rests.py`) gives both voices of a staff the rest the
   page prints once for the two of them. The scan writes it into one voice only, which
   is right on a shared staff (MuseScore fills the other voice's gap as it reads) and
   wrong the moment the split puts each voice on its own staff: the other voice's bar
   ends early. A gap is filled only with copies of the other voice's rests, and only
   when they cover it exactly; a gap the other voice sings through is a missing note
   and stays for the health check. On Sangerhilsen this was 19 of 29 findings (#235).
   Runs in both modes, since the per-system rebuild pulls one voice at a time too.
2. Decide which staves actually contain 2 voices; only those get split.
   Staff ids are renumbered to leave a gap after each split staff
   (split staff `n` → `n` and `n+1`), tracked in `GLOBALS.STAFF_MAPPING`.
3. Split multi-staff `Part`s so each `Part` owns exactly one `Staff`, then
   duplicate parts/staves for the split.
4. `find_reversed_voices_by_staff_measure` detects measures where voice
   stem direction is reversed, so the correct voice is kept per measure.
5. `handle_staff(staff, "up"|"down"|None)` keeps the matching voice, deletes the
   other, normalizes TimeSig/KeySig/Clef, forces stems up, strips dynamics,
   hairpins, articulations, tempo, harmony, layout breaks, and lengthens
   fermatas (`timeStretch=3`).
5b. **Voicing decides the part names.** A song records `voicing` ("men"/"women"/
   "mixed") when it is created, and `detect_part_types(root, voicing)` uses it
   instead of guessing from clef and pitch range. It has to: a male-choir score is
   written in treble sounding an octave down and editions routinely leave the 8 off
   the clef, so its tenor line reads as 66–82 — squarely soprano — and no pitch rule
   can tell the two apart. men → every treble staff is a Tenor and is **marked
   G8vb**; women → the treble staves split Soprano/Alto; mixed → each clef splits
   into the two voices it carries (S/A, T/B). Marking the clef is not enough on its
   own: a plain-G staff that turns out to be a tenor part was read an octave high,
   so its pitches are moved down twelve semitones (`octave_down`) or the practice
   track sings the line an octave above the men. Staves with no notes are skipped —
   the recording spacer is one, and counting it shifts the split. With no voicing
   recorded the old guess still runs, so existing songs clean as before.
6. `add_missing_ties` recovers OCR-dropped ties by mirroring them from a parallel
   voice that kept the tie at the same tick span. Same pitch is **not** enough on its
   own: an ostinato strikes the pitch a held line ties on the same beats, as separate
   notes (Vieläkö huvittaisi's A1 got 23 ties the page does not print, #284). So the
   target must also sing the donor's rhythm across the bar the tie starts in, and in
   the next bar up to the note it ends on — not after it, since voices that move
   together into a held note often part straight after. Measured over `songs/`: 117
   ties added before, 73 after — 52 stopped and 8 gained (every matching donor is
   tried now, not only the last). Of the 52, 31 are wrong by the page or the lyrics,
   9 were probably real (an overfull bar, a pickup voice) and 12 are unclear.
   Slurs are **not** auto-mirrored: a slur connects different pitches, so it
   can't be pitch-checked, and mirroring one voice's slur onto another produces false
   positives (e.g. copying a bass melisma onto the tenors) — slurs are fixed by hand in
   the score. Then `detect_part_types` (clef + pitch-range heuristics name parts
   S/A/T/B and set clefs), apply names/clefs, strip brackets/barLineSpan.
7. `--add SSAA` appends new empty staves (rests) with the right clef per letter.
8. `mark_scan_damage` runs last, in both modes, and takes out two things a scan gets
   wrong that nothing can repair from the score (#238). A **slur joining two singers**
   (`utils/cross_voice_slurs.py`): homr pairs a slur by the staff's number, so on a
   shared staff it can start in one voice and stop in the other, and after the split
   each half points at nothing, the end half costing that singer a syllable. Both halves
   go. A **bar longer than its time signature** (`utils/long_bars.py`): a misread
   rhythm leaves `len="9/8"` under 4/4 and every part plays an extra eighth; each voice
   is cut at the barline, a note across it shortened or taken out, a tuplet across it
   taken out whole, rests put back only where they spell the gap exactly. Neither
   guesses the right music back. Instead each bar it changed gets a **red mark**
   (`utils/problem_marks.py`): a staff text starting with `⚠`, saying what was taken
   out. The app's clean also marks each bar the MuseScore check resets. Deleting a mark
   in MuseScore is how a person says the bar is fixed: until then health lists it
   (`marked-problem`) and the Fix panel reads it off the score (until #347 a copy went
   into `fixes.json` as a `clean-marker` text entry). A **dashed** tie or slur on a sung
   part (`utils/dashed_arcs.py`) is marked `⚠ dashed tie?` / `⚠ dashed slur?` and kept. The publish copy strips marks
   (`score_export/score.prepare`), so a forgotten one never reaches the site.
9. `centre_measure_rests` (`utils/measure_rests.py`, #298) runs last in both modes: a
   rest that alone fills its bar becomes a bar rest (`durationType` `measure`).
   MuseScore 3's MusicXML import writes an ordinary whole rest even for
   `<rest measure="yes"/>`, and MuseScore draws that at the start of the bar rather
   than centred. Only the length changes; a rest that does not fill the bar exactly,
   a dotted one, one in a tuplet, or one a `location` shifts off beat one is left alone.
10. `fix_staff_display` (`utils/staff_display.py`, #354) runs after it, in both modes,
   and changes only what is drawn. A rest the page prints once for two voices is
   written hidden into the second (homr's `print-object="no"`), right on a shared
   staff and missing from the picture once that voice has a staff of its own, so a
   bar with one voice shows its rests. A barline with music after it in its voice moves
   to the bar's end (MuseScore puts a barline where the cursor stands, and a voice
   that stopped early drew a fake bar). A double or repeat barline (never a final one) goes on
   every staff of the bar that has none, since the split left it with the upper
   voice. A plain barline on the last bar goes, since it overrides the final barline
   MuseScore draws there. The same pass runs on the copy every cleaned preview
   (`render_score_pdf(tidy=True)`) and every publish (`score_export/score.prepare`)
   is made from, so a song cleaned before it is drawn right without a re-clean,
   which would cost its lyrics. `omr_systems.flatten` also steps to the bar's end
   before a right barline now, so new scans do not write the fake bar at all.
11. `set_voice_instruments` (`utils/voice_instruments.py`, #481) runs last, in both
   modes: every singing part gets a voice instrument id from its name (`S1` →
   `soprano`/`voice.soprano`, A, T, B, `Bar` → baritone, any other name → `voice`/
   `voice.vocals`). MuseScore 4 reads a part with no id, or MuseScore 3's `piano`, as a
   piano and puts a new dynamic **below** the staff, where the words are; on a voice it
   goes above. Only a part with no id or a piano id changes (any other id is someone's
   choice), parts named as instruments (`Piano RH`, `Violin`, `Urut`), percussion and
   rest-only staves are skipped, and the `Channel`/`program` is left alone, so the
   sound and the MIDI do not change. Dynamics already in a score keep their side:
   MuseScore 4 writes the side out when it converts an older file.

Voice-count anomalies run first: a measure with >2 voices is beyond the splitter
(which makes an upper/lower pair) and is either an OCR glitch or a real multi-way
split. Default (TTY) path = interactive **re-voicing** (`utils/revoice.py`):
`establish_baseline` asks the user to name the normal voices once (e.g. T1,T2,B,
mapping each name to a source staff); `capture_revoice_plan` then prompts per
anomalous measure for a per-voice name list, keeps the voices named for that staff
(reordered to baseline order so the split sees a clean pair), and captures the rest;
after the split, `apply_revoice_plan` routes captured voices — a **new** name gets a
new staff (rests elsewhere), a name belonging to **another** part is **moved** into
that part's output staff (resolved via `printed_to_output`), blank = dropped.
`--no-interactive` (or non-TTY) instead calls `resolve_voice_anomalies`
(`utils/interactive.py`), which reduces to the modal voice count and warns.
Note: which kept voice becomes upper/lower is still decided by the split's
stem/pitch logic, not strictly by the typed order. `≤2`-voice divisi is left alone.

`--per-system` (`utils/per_system.py`) is a separate opt-in mode for scores where the
physical staves change role per printed system (the Laulun aika fixture). It bypasses
the normal split entirely, and the module owns the whole assignment-to-score behavior
behind one entry point, `clean_per_system(root, input_path=..., answers_from=...)`:
it cuts systems at line breaks, describes each system's note-bearing staves
(`system_layout` / `layout_for_file` → `SystemLayout`/`StaffRow`), resolves the
answers, rebuilds the score as one staff per named part (sorted S<A<T<B, then by
number) pulling each part's notes from the declared `(staff, voice)` per system and
filling measure-rests where absent, re-adds the original line breaks on the top staff,
runs the post-rebuild cleanup (`add_missing_ties` + the same decoration strip the split
does), and writes the lyric-routing metaTags. Old Parts/Staves are removed (that's how
part deletion happens). Same name on two staves in a system → first wins.

Assignments are an `Answers` mapping (`{system_index: {staff_id: "T1,T2"}}`) produced
by either of two **adapters at the same seam**: the terminal prompt
(`utils/per_system_prompt.prompt_for_answers`, passed to `clean_per_system` as
`answers_from`) and the web grid (`song_app.pipeline.system_grid` →
`save_system_answers`, after which the rebuild reads them back from the store).
A staff left blank in a system inherits its previous system's answer (`-` =
`per_system.CLEARED` declares nothing and stops that inheritance); the
prompt offers the recorded answer as a `[default]` (Enter reuses it). A part named as
another part plus one lowercase letter (`S1b`, `A1b`) **falls back** to it (#293):
every bar the rebuild would fill with a rest because nothing feeds it — the system
does not name it, or names it on a staff that prints one unstacked line there — gets
the base part's bar instead, and in a system that leaves it out the lyric map sends
the base part's words to it too, whichever lane of the printed staff the base is on
(a per-system `follow` entry beside `map`, so the printed grouping is untouched), unless
the lyric block gives the b-part words of its own. The Review stage's note check (`verification.compare_notes`)
counts those borrowed bars as copies of the base part's notes, not as a difference.
A rest the scan wrote in its own voice or staff stays. Naming the part that way is the
person's reading that the single line is unison, which is what the rebuild otherwise
refuses to guess. **A system with no voices** (#482) is answered `{"*": "rest"}`
(`per_system.ALL_REST`): `_answers_in_force` skips it without touching the carry, so
it declares nothing, every part gets measure rests there, the next system inherits the
names from the one before it, `dropped_voices` reports nothing in it, and its lyric map
entry is `"map": {}` with `"rest": true` (also written for a system with no staves).
**A line left unnamed is said out loud** (#330): the rebuild takes one name per
line, top first, so a two-voice staff answered with one name keeps the upper line and
loses the rest — usually an answer typed once in system 1 and carried into a system
where the page prints two lines there (Lemmen nosto lost ~150 alto notes that way).
`per_system.dropped_voices` finds each such voice with notes; the grid marks the cell
and asks before cleaning, the clean logs it, and `pipeline.record_dropped_voices`
lists it in the Fix panel (`source: "per-system-dropped"`, replaced on every clean).
A chord is different: **one voice may sing a chord**, so notes of a stacked chord
past the last name stay in the lowest named part's chord (`A2, A2b` on a three-note
chord gives A2b the bottom two). They are only logged — the grid warns about written
voices (`StaffRow.lines`), not noteheads (`StaffRow.voices`).
It warns and never blocks: leaving a line out can be the right reading. Answers are
recorded per input file (basename, no extension) in `.persystem_cache.json` at the repo
root (gitignored) via `save_answers`/`saved_answers`/`has_answers`; the file itself is
internal (swap it in tests with `use_answer_file(path)`). A complete
answer set lets per-system mode run **non-interactively** (no TTY) — that is how the web
app cleans headless after the grid is submitted, and how the tests drive it. `main()`
handles per-system mode in an early branch: it runs the OCR measure repairs
(`preprocess_corrupted_measures`, `fix_overfull_measures`), then calls
`clean_per_system` (handing it the prompt adapter only when there is a TTY) and writes
the file — the rebuild details and the metadata are the module's. The repairs used to
sit below the branch, so a per-system score kept every spurious `len` the scanner
wrote: on Kaksi-laulua-krapulasta a bar the page prints as 3/4 stayed 4/4 and ran a
beat long in the practice track, while an ordinary clean of the same file repaired it.
Voice-anomaly resolution stays out on purpose — the answers already say what each
voice is.

**Divisi written as a chord.** An engraver writes two singers holding a chord together
as one voice with the noteheads stacked, so a staff can carry two declared parts
without having two `<voice>` elements. `_max_voices_in_range` therefore counts a chord's
noteheads as parts, and the rebuild gives each declared part its own notehead (top
first; the lowest named part keeps every notehead from its own down, so a chord with
more notes than names stays a chord there). Copying the voice whole instead handed both notes to the upper part and left
the lower one silent — on Kaksi-laulua-krapulasta the lower bass lost the "duu" in m22
entirely, which no health check catches (a chord is well-formed and so is a rest). Where
the stack narrows to one notehead the parts converge in unison rather than one of them
falling silent.

Because the PDF's printed staff numbering
**shifts per system** as parts are omitted (e.g. with T3 absent, the bass becomes
printed staff 3), it writes a per-system `lyricsSystemMap` metaTag (JSON: per
measure-range, `printed_no -> [output staff ids]`) in addition to an identity
`lyricsStaffMap` fallback. The module builds it by grouping parts that
share a source staff into one printed staff (divisi: voice 0 → 'above', voice 1 →
'below') and ordering printed staves by **musical rank** (S<A<T<B, then number) — not
by the OCR's source-staff order, which can be shuffled. `lyric_txt.py` import reads it
(`_read_lyrics_system_map`) and resolves each JSON block via the map for the system
covering its `measure_start`. Tested against `tests/test_files/laulun_aika.mscx` (a
real converted score kept as a fixture).
**Bars added in MuseScore after the clean** (#482): the map's bar ranges were written at
the clean, so a bar added later shifted every later system's words. Every reader goes
through `per_system.read_system_map(root)` (lyric import, the lyric editor's system labels,
`bar_readings`, `implode` and `staff_groups`). When the map's last bar is not the score's
bar count and the score's own line breaks give as many systems as the map has entries, the
ranges are re-read off the breaks, in memory only (`MAP_RECOUNTED`); otherwise the map is
`MAP_STALE`, used as recorded, and the Fix panel shows one row (`problems.STALE_MAP`, kind
`stale-map`) asking for a clean or an `insbar`/`delbar` entry. A map that still ends on the
last bar is used as recorded, even when a break moved. On this host's songs: 6 stale, none
recounted. The Fix rows read their printed systems through `pipeline._printed_system_bounds`
too (`problems._bounds`), so a row's system and its staff come off one numbering, recorded
`delbar`/`insbar` moves included.
Caveat: the musical-rank ordering is wrong when an ossia/extra voice is *printed on top*
(e.g. T3 above T1/T2) — then the PDF's printed numbering doesn't match rank order, so a
staff_number-based JSON maps to the wrong voice. The robust fix is to address voices in
the lyric JSON **by part name** (`"parts": ["T3"]`), which bypasses the positional map
entirely; the staff_number/`lyricsSystemMap` path is the fallback for unlabeled scores.

State is passed between passes through the module-level `GLOBALS` singleton
(`utils/globals.py`) — `STAFF_MAPPING`, `REVERSED_VOICES_BY_STAFF_MEASURE`,
etc. `main()` resets these at the start of every run. **Be careful**: this is
mutable global state; don't rely on it across concurrent runs.

Note: lyric handling is **not** part of this pipeline. An older Gemini-based
lyric-fixing flow (and its `pdf_path` plumbing, `utils/gemini_api.py`,
`utils/lyrics.py`) has been removed — the project direction is the
`lyric_txt.py` txt/json flow instead. `main()` only restructures staves/voices;
it deletes any `Lyrics` elements on the staves it splits but does not author or
fix lyric text.
