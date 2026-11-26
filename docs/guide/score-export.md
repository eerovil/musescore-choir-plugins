# The publish copy and MuseScore's clock

Part of this repository's agent guide; the index is [CLAUDE.md](../../CLAUDE.md). Moved out of it word for word (#513).

## The publish copy and MuseScore's clock (`src/score_export/`)

What is left of the scrolling video renderer after #400: the parts Publish still
needs. Publishing (`song_app/publish.py`) makes one prepared copy of the cleaned
score and exports everything off it, so the parts in the MusicXML, the MIDI and the
timing are the same parts on the same clock.

- `score.prepare(mscx_path, work_dir, keep_silent=False, initial_bpm=None,
  tempo_changes=None) -> (path, dropped)` is the only edit made to the score before
  it is published, and it is made on a copy: the cleaned score is never touched, and
  when nothing needs changing the original is used as-is. It drops parts with nothing
  to sing (percussion, or a staff of only rests — the click track `add_rest_track.qml`
  adds) with the staves they own, strips the red marks and red notes cleaning leaves
  for a person, runs `fix_staff_display` (so a song cleaned before it is drawn right
  without a re-clean, which would cost its lyrics), and writes in the tempo:
- **An opening tempo** (`add_opening_tempo`), only for a score with none of its own
  (`has_opening_tempo`). Cleaning deletes tempo marks and a scan never had any. The
  Publish panel's BPM field is shown only then; `publish.DEFAULT_BPM` (80) fills it
  until someone sets one.
- **Tempo changes are the Publish panel's, not the score's** (#387,
  `score.add_tempo_changes`). A song that changes speed otherwise plays at one BPM.
  `publish.tempo_changes` (`[{"measure": 13, "bpm": 112}, {"measure": 23, "bpm":
  "start"}]`, bars counted from 1, bar 1 being the opening BPM) is checked by
  `server._tempo_changes` and written into the copy as hidden tempo marks at the start
  of each bar; `"start"` is the tempo the copy opens at. Kept beside the BPM rather
  than in `fixes.json`: it is a playback setting, a re-clean must not need it
  replayed, and changing it needs only a re-publish.
- **Above five parts, parts share staves in the picture** (#420, `staff_groups.py`,
  `score.merge_staves`). The site's score of a big song was too tall to read, so
  once the split score has more than `staff_groups.MAX_STAVES` (5) singing parts,
  every part goes back onto the staff the PDF prints it on (owner's rule: all of
  them, not just enough to reach five). The pairs come from `lyricsStaffMap`, or for
  a per-system song the pairs sharing a printed staff in the most bars (a part
  cannot move between staves on the site), or failing both a guess from the names
  (a b-part with its main part, then S1+S2). A `lyricsStaffMap` giving every printed staff one
  part (a score handed over one part to a staff, so the clean split nothing), or a
  `lyricsSystemMap` where no printed staff ever holds two, says nothing about the page
  (for shared staves) and goes to the name guess, and a b-part that only borrows its main part's notes
  (`follow`) sits on that part's printed staff (#482). The panel says "the page prints
  every part on its own staff" only when a record of the page found no pairs, and names pairs it refused for
  carrying too many voices. The upper part takes voices 1 and 3
  and the lower 2 and 4, since MuseScore stems 1/3 up and 2/4 down and a unison
  then shares a notehead; a pair needing more is left apart and logged. Only
  `score.musicxml` is merged: the MIDI and the timing come off the unmerged copy,
  and the manifest's parts carry `staff` and `voices` so the site knows where each
  part is drawn. The Publish panel's *Shared staves* field saves
  `publish.staff_groups` (null: automatic, `[]`: none). It is #246's video option
  brought back, which #400 removed with the videos.
- **A fermata holds one beat longer than written** (#380, `score.hold_fermatas`).
  Cleaning writes `timeStretch=3`, which held a dotted half for six seconds at 90 bpm.
  The copy rewrites every fermata's stretch so it adds one beat (a dotted quarter in
  6/8), worked out from the span MuseScore stretches: from the fermata's beat to the
  next note or rest on *any* staff. A fermata marked not to play is left alone.
- **A printed rit. or accel. is played** (#483, `score.gradual_marks`, `add_gradual`).
  MuseScore plays such a text at the speed before it, so a song slowing into its end
  played straight through, and so did the voices sung off its clock. The copy writes
  hidden tempo marks beat by beat, easing in a straight line to 75% of the speed
  (accel.: 125%) on the last beat before the ramp ends: at an "a tempo" or "Tempo I"
  text (back to the speed before the mark, or the opening one; a visible "a tempo" or
  "Tempo I" tempo mark is set to that speed rather than to what the scan wrote), a tempo mark (which
  keeps its own speed), a fermata (the speed before the mark comes back after it), the
  next rit./accel. (which starts from where this one left off) or the end. A beat inside a
  held note or rest still gets its mark, written at the voice's end behind a `location`
  that steps back to the beat (`_place_at`, which MuseScore plays on time); a mark with no beat left is logged and
  played as written. The spellings are in `_GRADUAL` (rit., ritard., riten., rall.,
  accel. and their long forms, also inside "dim. e rit."); "ritmico" is not one. The
  Publish panel lists the marks, each with where its ramp stops and why ("eases until bar 42
  (fermata)", `gradual_marks`' `until`/`until_why`, counting the panel's saved tempo
  changes), and saves `publish.gradual`
  (`[{"measure", "bpm", "off"}]`, matched to the first mark in that bar; a second mark in
  the bar is listed without controls and eases to 75%): a target BPM in place of 75%, or off. A saved setting whose bar has lost its mark is shown, dropped
  on save, and ignored by a publish.

`musescore.py` is the MuseScore 3 or 4 command line (`run_musescore`, bounded by
`CLI_TIMEOUT` because a wedged MuseScore otherwise hangs the publish) and the part
names a score carries (`part_names`), which the site matches to the MIDI tracks.
`engraving.py` is what every verovio toolkit in the app is set up with:
`RESOURCE_PATH`, because the native library does not carry its bundled fonts into the
executor thread, and `XML_ID_SEED`, which makes an engraving byte-for-byte
reproducible. The bar readings in the Fix panel and "Show what landed" use it.

**The clock is MuseScore's own** (`clock.py`), and that was the hard-won lesson of the
video renderer: verovio's timemap and MuseScore's playback are *different musics*.
Verovio ignores playback properties, above all the fermata stretch — on Hanget soi it
said 48.0s where MuseScore plays 59.6s. But MuseScore writes that stretch into its
MIDI export as tempo changes, so seconds come from the **MIDI tempo map**
(`TempoMap.from_midi`), and bar order comes from MuseScore's **`.mpos` export**
(`read_mpos`), which is written off the same repeat list playback uses. That is what
makes repeats, voltas and D.C./D.S. jumps come out right without a second copy of the
segno/coda/Fine rules here: verovio followed them only sometimes (it played
Jouluriemua's two D.C.s as two passes where MuseScore plays three, and a scan's "2."
bracket that opens and never closes made it expand no repeat at all).
`publish.timing` lays the bars end to end in MuseScore's play order, puts a point on
every beat, at every tempo change and at each bar's end, and checks each bar's start
on the MIDI clock against the `.mpos` within 10 ms, refusing to publish otherwise. A
bar count that differs between MuseScore and the score is refused too. The format is
in `docs/stemmanauhat-site.md`.

Tests (`src/score_export/tests/`): `test_score.py` pins which parts count as silent,
that dropping one takes its staff, that the original file is never modified, the
opening tempo, the fermata hold and the tempo changes, each only in the copy.
`test_clock.py` pins the `TempoMap` arithmetic (a 3x fermata window), reading the
`.mpos` in played order, and, with MuseScore (skips without it), which bars MuseScore
plays for `repeat`, `voltas`, `dal_segno` and `da_capo`, and that an opening BPM, a
fermata hold and a tempo change reach MuseScore's clock. `tests/test_files/fermata.mscx`
is `simple_1_output` with a `timeStretch=3` fermata in measure 1.
