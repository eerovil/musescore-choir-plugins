# The prototyping fixture and `fixes.json`

Part of this repository's agent guide; the index is [CLAUDE.md](../../CLAUDE.md). Moved out of it word for word (#513). Every kind of recorded fix (`undot`, `slur`, `bar`, `delbar`, `timesig` and the rest), what `GET /bar` returns, and the Virta venhettä vie fixture.

### The prototyping fixture

`fixtures/virta-venhetta-vie/` is a real public-domain song (Kuula/Leino, TTBB,
scanned + OCR'd) kept at three stages, so a change can meet real OCR damage
immediately instead of a synthetic score. **Not** a unit-test fixture in the usual
sense — though `test_pdf_systems.py`, `test_bounds_api.py` and the browser tests do
read it — and it is free to change shape as the app does.

`10-cleaned/fixes.json` carries score edits a person authorised — things the
automatic passes refuse to guess at (`utils/score_fixes.py`). Each entry names a
staff, measure and chord, and says **why**. Applying is strict: an entry that no
longer matches raises, so a pipeline change that moves the note fails the build
instead of quietly leaving the defect in. Fix a song's defect from the printed page into
that song's `fixes.json`, with the reason; do not add a deterministic repair pass for it.

**Every song gets this, not just the fixture.** `run_clean` applies
`<song dir>/fixes.json` right after cleaning (`pipeline.apply_recorded_fixes`), so a
recorded edit survives a re-clean. Before that it did not: cleaning rebuilds from the
source, so a hand edit made afterwards vanished the next time anyone cleaned, and the
same three page-verified rests were typed into Kaksi laulua krapulasta twice in one
session. Replaying a recorded fix is not the pipeline guessing — the judgement was
already made and written down. A fix that no longer matches fails the clean with the
entry named, rather than being skipped.

Three kinds: `undot` and `slur` name a chord, and `append` works on the end of a bar
(`drop` takes off the rest a scan padded with in place of notes it lost). Appending
rather than rewriting keeps what the tokens cannot carry — a triplet bracket, a tie —
and every kind checks what the bar reads **now** (`from`) before touching it, tuplet
brackets included. A note's spelling is derived from its pitch: the first fixes to
carry one by hand got three of four wrong.

Three more were added for fixing a song from its page with an LLM (#340), because an
agent on Annin laulu had to fake each of them and the fakes damaged the score.
`unslur` (staff, measure, `index` of the chord the slur starts on) takes out a slur the
scan invented or pinned on the wrong voice, both halves, wherever the end half sits;
before it, the only way was to rewrite the bar twice with `bar`, which moved fermatas
and left a slur half stored on a chord in another bar (`rejected_bars._cut_spanners`
now cuts those too). `tie` (`index`, `pitch`) joins that note to the same pitch in the
next chord, in this bar or the next, so playback holds it — a slur in its place lands
the words right and still sings the note twice. `duration` (`index`, `to`, a length
like `quarter..`) gives one chord another length, double dots included, and when that
makes the voice fill the time signature the bar gets that length back **on every
staff** (its `len` goes and a whole-bar rest is lengthened with it); any other total
refuses. When it does, the **back-steps** cleaning used to squeeze the other voices into
the short bar (a negative `<location>`) go too, wherever that voice's own notes fill the
restored bar exactly (#344): otherwise MuseScore's check, which runs after the fixes,
reads those voices as too long and resets them to rests nothing recorded could undo —
Gute Nacht bar 6 and Annin laulu bars 9, 10 and 21. And a `bar` fix no longer refuses
a voice with a gap in it: it takes the gap out and writes the bar afresh, which then has
to fill the bar's own length (Integer vitae T2 bar 9, Jouluyö's last bar). It may
also fill the bar's own length when the scan made the voice longer than the bar (#350):
false triplets left Lasinkuultava laulu's T1 bar 9 7/6 long in 4/4, which no writable
lengths add up to. All three take a `from` and refuse without one. `GET /bar` now also returns
the bar's `from` (rests included), and the lyric import's reply carries `mismatches`.

`GET /bar` answers the questions agents writing fixes kept getting wrong (#357). Each
chord carries `at`, its place in `from`, and `items` pairs every `from` token with the
chord `index` it has (`null` for a rest or bracket), since `index` counts chords only;
a wrong `index` is refused with the chords listed. Each note in `pitches` gives its MIDI
`pitch` and whether a tie leaves it (`tied_to_next`) or reaches it (`tied_from_prev`).
A bar MuseScore's check reset carries `reset`: the bar as the recorded fixes left it,
kept in its `musescore-check` entry (`scanned_from`), which is the `from` a new fix for
it needs, because fixes replay **before** the check. An `append` spells what it adds by
the key in force (flats in a flat key). And cleans take turns (`pipeline._CLEAN_LOCK`):
`clean_score`'s `GLOBALS` is one per process, and two songs cleaning at once emptied
each other's tables — a clean failed with the bare error `"3"` (`KeyError: 3`); a failed
job now names an unexpected error's type.

**A bar a fix has answered stops showing red** (#347). homr marks a printed staff, so
on a staff two parts share the `⚠` mark lands on the first part while the red notes stay
on both. `unmark` and the word-striking picks (`pitch`, `rhythm`, `bar`) therefore look
for the mark on every part that printed staff became in that bar (`score_fixes._siblings`,
read off `lyricsSystemMap` / `lyricsStaffMap`); `tie`/`untie` answer `tie?`,
`slur`/`unslur` answer `slur?` and `duration` answers `rhythm?`; and after any fix, a bar
with no mark left on any of those parts loses its red notes (`_settle`). `voice?` and
cleaning's own sentences still need an `unmark`, as #290 decided. Cleaning no longer
copies the marks into `fixes.json` as `clean-marker` text entries — the Fix panel reads
them off the score — and removes copies an older clean left.

`untie` (#342, `index`, `pitch`) is `tie` taken back: it takes out the tie that starts on
that note, both halves, in this bar or across the barline, so playback sings the note
again. Strophic songs print **dashed** ties that belong to a later verse only, and homr
reads them as real ties, so verse 1 loses a syllable (Gaudeamus igitur bars 6 and 8).
Strict about `from` like the others, and replayed on every clean.
A homr that reads the dash (eerovil/homr#100) writes `line-type="dashed"` on the tie or
slur, MuseScore keeps it as `<lineType>2</lineType>`, and cleaning marks the bar `⚠ dashed
tie?` or `⚠ dashed slur?` on a sung part (`utils/dashed_arcs.py`, #482) and leaves the arc:
whether verse 1 sings it is the person's call. `untie` and `unslur` answer the mark once
no dashed arc of that kind is left in that part's bar (a part sharing the staff keeps its own); an
assembled tie or slur over a line break is dashed when either half was. homr finds a dash
chain of four or more, so a short dashed tie can still come through solid and unmarked.

`delete` (#352, `index`, `what`, optional `subtype`, `from`) takes a mark the scan invented
off one chord — a fermata (Mieslaulu bar 13 has two where the page prints staccato
dots), an articulation, an arpeggio (#366: a sharp read as one), a breath mark, a staff text, a tempo or a rehearsal mark. Only
those: a slur, tie, note, red mark or the words are refused naming the kind that does
it, and a clef, key, meter or triplet bracket because it changes the bar itself.
MuseScore holds a beat for **any** staff's fermata, so the log says when another staff
still carries one at that beat. `GET /bar` lists each chord's `marks`.

`delbar` (#346, `measure`, `from`) takes out a bar the scan invented, on every staff —
Kun poijat ne raitilla's scan put an empty bar between the "1." and "2." endings, so
the "1." bracket covered two bars and the track played a bar of silence. A volta, slur
or tie reaching across the bar is shortened by one, both halves, a "1." bracket that
ends on the barline before it keeps its length, the removed-slur records (`removedSlurs`) move up with their bars, and a per-system lyric map loses the
bar too; a spanner starting or ending in it, any volta starting in it, a repeat sign
(the refusal names the `unrepeat` entry to put first, #480: a scan can read a double bar as a
repeat), or a clef/key/meter change refuses. Fixes apply **in file order**: an entry before a
`delbar` counts bars with the invented one still there, an entry after it without, so
neither has to be renumbered; a Fix-panel pick is relocated (`relocate_picks`) in that numbering too, and the bars homr offered readings of are numbered off the scan, so `offers` maps them past the moved bars (`score_fixes.after_moves`). The printed-system bar labels (`.systems.json`) and the
line breaks the previews take off the converted input still count the invented bar; the
lyric import and editor map the labels through the moves (`pipeline.moved_system`, #446).

`insbar` (#346, `measure`, `from`) is the other way round, for a barline the scan
lost (Kristallen den fina squeezed printed bars 2-4 into cleaned bars 3-4): an empty
bar, a whole-bar rest in the meter in force, goes in **after** bar `measure` on every
staff, for `bar` fixes later in the file to write (a `bar` fix now writes a bar that
holds only a whole-bar rest, and a `bar` rewrite now keeps a volta bracket's halves in the bar). `from` is what bar `measure` reads now. A slur or volta
across that barline is lengthened, a volta ending on it keeps its length, a tie across
it refuses, and the metaTags move as for `delbar`. Both count in file order, and the
Fix panel maps bars through both (`score_fixes.bar_moves`) — homr's offers, picks, and the slur answers it matches by bar-numbered id (`problems._slur_decisions`).

`dropnote` and `addnote` (#358, `index`, `pitch`, `from`) take one note off a chord or
put one on, and leave its length, words and other notes alone. Pages print an optional
note in brackets — a low octave, a divisi — and homr reads it as a real chord note, so
the track sang both; **the default is to sing the main note only**, so the bracketed
head is dropped and an optional note the scan missed stays out (owner's call on #358).
`dropnote` also takes out a tie on the note, both halves, and refuses a chord's only
note; `addnote` spells the note from `tpc`, an octave in the chord, or the key in force.

`unvolta` (#378, `measure`, optional `text`) and `unrepeat` (`measure`, `which`: `end` or
`start`) take out a volta bracket or a repeat sign the scan invented, on every staff, both
halves of a bracket; each refuses when there is nothing to take. Suomalainen rukous came
back with the organ's "1." ending and end repeat copied onto an extra bar, and the scroll
render refused it. `volta` takes `second`, the bars of the "2." ending (a bracket may now
close the score), and `barlen` (`measure`, `from`, `to`) gives a bar every staff rests
through another length (`len`), for a 2nd ending printed 6/4 that cleaning cut to 4/4.

`voice2` (#431, `staff`, `measure`, `from`, `to`) writes a second line into one bar of a
part, as that part's voice 2, for a part that splits on the page for a bar or two (Butterfly's
Sopraano 1 holds an E flat over "Love me" in bar 61). Cleaning gives a staff whose voice 2
appears anywhere a part of its own for the whole song, which moves every later staff and makes
the song's other fixes miss, so the line goes in after the clean instead. `to` is a `bar`
fix's notes; the line must fill the bar, its silences written as rests (the singer reads the
voice alone and would sing a note early after a gap). `from` is voice 2 as it reads now, `[]`
for none (`GET /bar` returns it as `voice2`); writing it again replaces the line, keeping its
words. Its words go in through the lyric editor's voice-2 box. Health does not list such a bar
as `extra-voices` (it reads `fixes.json` beside the score), and the publish copy keeps both lines.
Put the upper line in voice 1: sing_song's low line reads voice 2. `tie` and `untie` take `"voice": 2`
(#434), with `from` and `index` read off voice 2, so the line can hold a note into the next
bar's voice 2 (Butterfly bar 84 into 85); the tied-into note takes no syllable. List them after
the `voice2` entries they tie.

`timesig` (#353, `measure`, `from`, `to`) takes a time signature the scan invented off
every staff (`"to": null`) or writes another in its place (`"to": "6/8"`). Kesäaamu is
printed in 6/8 and homr read a 3/4 at bar 22; the notes fit both, so `spurious_timesigs`
cannot see it. A change that keeps the bar's length only relabels (3/4 and 6/8, or a
removal where the meter in force is that length). A change of length is allowed only where
every bar the signature governs, up to the next one, already has the new length on every
staff (a `len` override), and those overrides then come off (#480: homr put a 1/1 over a
bar three quarters long); otherwise it refuses naming the first bar that does not fit.
Bar 1's signature may be replaced, never removed. It counts bars in file order like
`delbar`.

`rehearsal` (#480, `measure`, `text`) puts a rehearsal mark on a bar: homr reads none at all,
so a scanned score reached the site without the letters or numbers singers count bars from. The
mark goes on the top staff of a part publish keeps (a rest-only click staff or a percussion part is passed over, by publish's own `score.part_is_silent`), in the first voice ahead of the bar's first note, where MuseScore keeps
it, and the publish copy carries it into the MusicXML. A bar that already has one, on any staff,
refuses. There is no Fix panel button for it (nor for most kinds): it is written into `fixes.json`.

A further kind, `text`, is just a sentence (`{"kind": "text", "what": "..."}`), because
most edits are none of the other three — turning a bar-length rest into a whole-bar
rest came up on one song in one sitting and could not be written down at all. Nothing interprets it: `apply_fixes` steps over
it and `score_fixes.free_text` hands the sentences back, so cleaning logs them as still
outstanding and the **Fix** panel lists them (`pipeline.free_text_fixes`, read live off
the file, so writing one shows at once and applying it stops showing). Applying one is
a person's job, or an agent asked to do it. Refusing to clean would make the file a
hostage; skipping in silence is the failure it exists to prevent.

Writing an entry has meant hand-editing the JSON, which means knowing the token
grammar and the staff/measure/index numbering — and nobody does that from a phone.
This pull request proposes that the **Fix** panel record a `slur` without any of
that: pick the part, the bar and the two notes, say why. The judgement still has to
come from a person reading the page — a slur joins different pitches, so nothing
upstream will guess one back — but the recording no longer does. `score_fixes.read_bar`
reads the bar back out in the same numbering and the same tokens a fix writes, so what
is shown and what is recorded cannot drift apart, and `pipeline.record_slur_fix`
appends the entry *and* applies it to the cleaned score. Both, because either alone
fails: recorded-not-applied leaves the score looking unrepaired until the next clean,
and applied-not-recorded is the hand edit `fixes.json` exists to replace. Only the new
entry is applied — `slur` is not idempotent, so replaying the file would double every
earlier one. The wrong repair people reach for instead is an empty syllable (`_`) in
the lyric editor, which patches the words while the score still says two attacks: the
site's player lights the second note, the grid keeps offering the slot, `slot_counts` keeps
counting it, and none of it survives a re-clean.

```bash
fixtures/virta-venhetta-vie/reset.sh        # drop it into songs/ at the furthest stage
fixtures/virta-venhetta-vie/reset.sh 00     # or: just registered, ready to clean
.venv/bin/python fixtures/virta-venhetta-vie/build.py   # regenerate the derived stages
```

Stages are overlays holding only what they add, so the 745 KB scan is stored once.
It now runs clean the whole way: **no health issues, no lyric mismatches**, and it
reaches stage `review` — which means every automatic check is satisfied, not that
the song is right. Nobody has compared it against the page end to end, the words and
their alignment are unverified, and it has never been published. See STEPS.md, "What clean does not mean". Two things needed a person to read the page, and both are
recorded in `fixes.json` with their reasoning — a dot the OCR invented in m26, and a
slur it dropped in m50. A third case needed no score edit at all: in m32 the bass
lower voice holds one syllable while the others sing two, which is a rhythm
difference and was fixed in the text. `STEPS.md` records how each stage
was produced, including a wrong conclusion and its correction — worth reading before
trusting a tidy-looking diagnosis of a scanned score.

Before writing a pitch fix from a scan, crop the bar from the PDF at 300 dpi
(`pdftoppm -r 300`), compare the glyph with other accidentals on the same page, and
confirm which voice it belongs to.
Before reporting that a part sounds an octave off, check the staff's clef (treble-8 or
plain treble) and the pitches stored in the score: a tenor line in treble-8 sounds an
octave below what it looks like, so it is usually right.
