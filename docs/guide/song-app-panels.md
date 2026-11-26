# The song web app: the browser side and its routes

Part of this repository's agent guide; the index is [CLAUDE.md](../../CLAUDE.md). Moved out of it word for word (#513). These are bullets of the guide's section on the song web app, which is split over [song-app.md](song-app.md), [song-app-panels.md](song-app-panels.md), [omr.md](omr.md), [omr-systems.md](omr-systems.md) and [scan.md](scan.md), in that order; "above" and "below" in the text may point into a sibling file. Here: slow pictures, layout, phone, the New song form, the Lyrics panel and paste, the clean grid, heavy slots, the Fix panel and its problem list, score download and upload.

- `static/` **slow pictures say so** (#303). A score tab is a MuseScore render and can
  take a minute: `mountPdf` shows a note with a running seconds count (`busyNote`) on a
  first build, keeps the old score under an "Updating…" badge on a rebuild, and says
  why when the server refuses. The engraved systems in Compare and Scan vs page are
  one MuseScore run each, so they wait in placeholders and are fetched by
  `slowQueue` in reading order, `SLOW_AT_ONCE` (2) at a time — asked for all at once
  they started a MuseScore per system and arrived at random. A system jumped to goes
  next. The queue belongs to the view and outlives a redraw (a scan redraws Scan vs
  page after every system it reads): a request in flight lands on the new placeholder
  rather than being dropped or started again, since dropping it would not stop the
  MuseScore run behind it. `test_loading_states_ui.py` pins it.
- `static/` **layout**: the page never scrolls — `html, body` are fixed to the
  window and every panel scrolls inside itself. `#app` takes what the header leaves
  (`flex: 1 1 auto; min-height: 0`) and the workspace grid fills it. It used to be
  `height: calc(100vh - 49px)`, a guess at the header's height, which overflows the
  moment the header is a pixel taller — a longer song name will do it — and then the
  whole page scrolls and the viewer is carried off-screen. Grid and flex items need
  the explicit `min-height: 0`, or they refuse to shrink below their content and
  `overflow: auto` never fires.
- `static/` **on a phone**: the three panes cannot share a 390px screen, so below the
  breakpoint the panel and the viewer are shown one at a time and a bar at the bottom
  switches between them (the current stage · Score). The stage list is the same
  left-hand sidebar as on desktop, slid in over the page by a ☰ in the header (#258);
  opening it adds a history entry, so Android's Back closes it instead of leaving the
  song. The bar, the ☰ and the drawer's backdrop are in the DOM at every
  width and the stylesheet hides them above the breakpoint — there is no width-sniffing
  in `app.js` that could disagree with the media query, and every mobile rule is
  additive, so the desktop layout is untouched.
  **The bar must never leave the screen**, and on an Android phone it did (#258) — not
  reproduced in an emulated phone, so the fix does not rest on one cause. The bar is
  pinned to the bottom of the window (`position: fixed`) with the workspace padded to
  leave room, rather than being the grid's last row; the page snaps back to the top if
  anything scrolls it (a field brought into view, the keyboard closing); the keyboard
  shrinks the page instead of covering it (`interactive-widget=resizes-content`); and
  the page cannot be pinch-zoomed below the breakpoint (`touch-action: pan-x pan-y`),
  since a zoomed page carries the bar off with it. The score zooms itself instead —
  pinch on it, or − / + / Fit in its tab row — redrawing the PDF at the new size
  (`zoomPdf`). Those buttons are deliberately not `.vtab`: `rendering_state.js`
  remembers a `.vtab` click as the document to reopen and replays it after every
  redraw, and its `MOBILE_PANES` list is the bar's tabs by position. The breakpoint is
  `max-width: 840px, max-height: 500px`; the second condition catches a phone held
  sideways, which is wider than the breakpoint but nothing like tall enough. Two
  things had to change beyond CSS: the viewer's "wait for layout" retry now stops
  while its pane is off-screen (an offscreen pane never gains width, so it was an
  endless `requestAnimationFrame` loop on a battery) and wakes via `_wake()` when the
  pane comes back; and the Systems editor's band drag moved from mouse events to
  **pointer** events with capture, so a finger can drag a boundary — that path is
  otherwise unreachable on a phone. `src/song_app/tests/test_mobile_ui.py` drives it
  at 390x844.
- `static/` is a dependency-free vanilla-JS SPA: a library view and a 3-pane
  workspace (stage rail · per-stage panel · document viewer, controls clustered
  left, previews right). The viewer tabs
  between **Original PDF**, **Original XML** (the OCR input), **Cleaned MSCX**
  (lyrics stripped) and **Cleaned MSCX with lyrics** — all but the first are
  MuseScore-rendered PDFs served by `/render?doc=original|cleaned_nolyrics|cleaned`,
  cache-busted by the cleaned fingerprint so they refresh after re-clean / lyric
  import. The viewer can **split into two independent side-by-side panes** (the
  ⇆ Split control), each picking any doc — e.g. Original PDF next to Original XML.
  PDFs are rendered with **pdf.js** (CDN, `renderPdf`) into our own scrollable
  `<div>` so **scroll position survives a re-render** (after a re-clean / lyric
  import the cleaned preview re-renders in place and restores `scrollTop`); falls
  back to a native `<iframe>` if pdf.js can't load (offline). **PDF measure-locating
  is page-level only** (no bounding boxes) — see DESIGN.md.
- The **New song form** is the front door of the whole scan route, and this pull
  request proposes opening it (#127). `POST /api/songs` has taken a name plus
  *either* a score *or* a PDF since #98, and a PDF alone starts the song at `scan` —
  but `newSongDialog` refused to submit without a score file, so none of the 48 songs
  on the host had ever been through scanning: the route was live on the server and
  unreachable from the phone. The Scan panel work (#115, #116) edited `app.js` and
  never this dialog. So the form now asks for **name plus at least one file**,
  matching the server, and **leads with the PDF** — a song made from a PDF is the
  ordinary case and a score file is the manual import (#86). It also says **where
  each door goes** before Create is pressed (`.routehint`, live as the files are
  chosen): a PDF alone starts at Scan, a score file starts at Clean and skips
  scanning. Discovering that from the stage rail afterwards is discovering it too
  late — the scan is the stage that reads the page, and a song that skipped it looks
  exactly like one that passed it. The two file inputs carry ids (`#f-pdf`, `#f-xml`)
  because the browser tests used to reach the score as "the first file input", which
  reordering would have silently repointed at the PDF.
- The Lyrics panel's **Type by system** mode shows **one printed system per screen**
  (#393's spec, built in #397). The systems are the *printed* ones from `.systems.json`
  (normal-mode cleaning strips the score's line breaks), or the score's own breaks for a
  song with no marked systems. The page picture of the system is cut just above each
  staff and the boxes for the parts printed on it sit under it: `GET /lyric-staves/{n}`
  (`pipeline.lyric_staves`) finds the staves in the crop with the app's own finder
  (`system_finder._staves`, no homr), cuts at the first blank row above each staff's
  own ink, and pairs them with the printed staves the clean's part-to-staff record
  names for the system (`lyric_txt.printed_staves`: all of them, or the ones that sing,
  since a page leaves a resting staff out). Positions are kept in
  `.pages/lyric-staves.json`, keyed by the crop's file name, so they are found again
  only when the crop changes. A system that does not match lists its boxes under the
  whole picture, so a box never lands under the wrong staff (measured on #393: 784 of
  853 systems match). The panel takes the viewer's width while it is open (`.ws.lyricwide`).
  One box is **one verse x one system x one part x one voice** (`lyric_txt.editor_grid`
  → `boxes`, keyed by staff id, `3` and `3@2`, since two parts can share a name); a
  voice-2 box exists only where that voice has notes.
  **Verse tabs** keep you on the system. The browser sends only the boxes changed,
  `{"verse": n, "boxes": {system: {key: text}}}` (`blocks_from_boxes`, addressed by
  staff id — a part may be named "1", or two parts the same: key every new box and part
  lookup by staff id, never part name, and run the round trip over every song in `songs/`
  before review), and an emptied box
  takes its words out. **← Previous / Next →** import first and stay on the system if
  the import warned; pressing again moves on. **Show what landed** swaps the picture for
  `GET /landed/{n}`: MuseScore exports the score and verovio engraves only that system's
  bars (`pipeline.landed_system_svg`), because a render of the whole score need not lay
  its systems out like the page (Kevät's has 18 for the page's 13, so cropping "system
  9" out of it showed bars 16-18). Warnings are kept per box: an import drops only the
  earlier ones its own bars answer (`server._kept_warnings`, read off
  `LyricImport.written`), and only those made against the score as it was before the
  import, so a re-clean's stale warnings are not carried on. The reply's `written` names
  every system an import wrote on, a line running on into the next one included, so
  the page refreshes those systems' pictures. The editor's import is written to `lyrics-editor.json`, so the
  paste box still opens on `lyrics.json`. Mismatches come back as **fields** (`kind`,
  `measure_start`, `measure_end`, `staff_ids`, `syllables`, `slots`, `verse`, `voice`,
  `message`) and are attached to the matching box by comparing those fields.
- **Paste from AI** keeps the prompt/JSON round-trip, and follows the same model: each
  line may say `"verse": n` and `"voice": 2`, one paste carries every verse, and it
  changes only what its lines name (an empty `text` clears that line). It is no longer
  a full replace of verse 1 (`run_lyric_import(replace=False)`).
  **The prompt names the song's parts** (#482): `GET /api/prompt?slug=` appends the cleaned
  score's part names, top staff first (`pipeline.lyric_prompt`, off `lyric_parts`), and
  `lyric_json_prompt.txt` asks for them in every line's `"parts"`; `staff_number` and
  `position` place a line whose `"parts"` is empty or names no part of the song. On a
  per-system score the printed staff numbering shifts where a part rests, and a name does
  not. A name two parts share, or a part with no name, is listed by its staff id. **A line
  that names its part fills only its own printed system and never runs on** (owner's call on
  #505): its end is cut at the next printed system (the caller's systems, else the score's
  own line breaks), an end the source gave included, and syllables past it are crammed and
  said as `too_many`. If its part has **no notes** there (a note held over by a tie counts)
  it is not placed and comes back as a `part_rests` mismatch naming the part and bars, since
  the name or the system is wrong. The by-system editor's boxes (by staff
  id) and the older cells editor (by name) also name their part, but carry `"box": true`
  and keep running on into an empty next system (#393). A
  `staff_number` line runs on as before. A named entry and a `staff_number` entry landing on
  one part, voice and verse in one block are not joined: the named one is used and the other
  is a `two_lines` mismatch (`_block_lines`), which names both lines, so pasting either again (the named one alone, the usual correction) answers it. A name no part carries, or one two parts
  share, is an `unknown_part` mismatch and the line falls back to `staff_number`, or is not
  placed (and says so) when it has none that is a staff of the score; a staff id in
  `"parts"` the score has not is unknown too (not checked under `--split`), and so is a
  number in quotes that is one part's name and another staff's id (a bare number is always
  a staff id, and a quoted number a part is named is that part); names are read like `lyric_parts` reads them (the instrument's
  name when the part has none). These three warnings are about a source line, named in their
  `lines` (`lyric_txt._line_ids`), and every import lists the lines it read
  (`LyricImport.lines`); a later import drops such a warning only when it sends that line again
  (`server._kept_warnings`), so pasting another part into the same system keeps it.
  **A line is the line as written** (#482, design v12): canonical JSON of its bar, verse,
  voice and `parts` exactly as given (types and list items kept, so `["2"]`, a part named
  "2", is not `[2]`, staff 2), or `staff`/`position` for a numbered one, so two
  entries naming one part in one block are one line and one warning, and a `two_lines`
  warning names the line set aside and the named one. A warning is answered by itself
  only when (a) that same line is sent again, or (b), for `part_rests` and `two_lines`,
  a box import writes the warning's staff, verse and voice over its bars (an emptied box
  counts). **A corrected name does not answer it**: a line with another name is another
  line. What the app cannot see answered, a person takes off with **Dismiss** (a button
  on every warning, in the paste list and in Type by system, where a warning about a
  staff with no box in that system is listed above the boxes): `POST
  /lyrics/warnings/dismiss {"id"}`, each warning's `id` being `lyric_txt.warning_id`
  (stored, or a hash of its fields for one saved before ids). Dismissing the last one
  moves a song on Lyrics to Review only when the words were imported against the score
  as it is and no import is running.
  **The import is a `lyrics` job** (#482): it writes the words into `<score>.lyrics-tmp`,
  compares the score's fingerprint under the song lock and moves the copy over with
  `os.replace`; a score saved meanwhile (MuseScore, an upload) is kept and the import
  answers 409. Health is written afterwards only if the score is still the imported one.
  Startup removes leftover `*.lyrics-tmp` files.
- The clean panel's per-system grid mirrors the backend's answer rules: a blank cell
  inherits the staff's previous answer (shown as a faint placeholder) and `-` marks the
  staff silent from there on, clearing the carry — both cleared and never-named slots are
  flagged `unset` and listed in the "will be DROPPED" confirm before cleaning.
  A printed system where every staff rests has no staves to name; the grid shows it as
  one line, "Every staff rests here; names carry on to the next system" (#482), rather
  than an empty block that read as a missing system. The owner chose not to number its
  resting staves off the scan.
  A system the page prints with staves but no singing (a piano interlude) is ticked
  **No voices here (piano or instruments only)** under its heading (#482): its rows are
  switched off and saved as `{"*": "rest"}` beside whatever was typed there
  (`per_system.ALL_REST`, read back by `per_system.parse_answers`), so unticking gives
  the names back. The clean rests every part through it and the names carry past it.
  The terminal prompt (`per_system_prompt`) keeps such a system, and the names saved beside the
  tick, and does not ask about it.
- **Heavy slots** (`heavy_slot.py`). A homr read or a homr install is every core the
  machine has, and so is an agent's test suite — several at once is all of them slow
  rather than any finishing sooner. AgentDeck owns that queue (a small pool of
  `flock`ed files under `/run/user/<uid>`), and its two existing enforcement points
  both work by classifying a command string in a shell the deck controls; the song app
  is a systemd service and has no such shell, so it asks over HTTP instead —
  `POST /api/heavy-slots`, heartbeat, `DELETE` — and the deck never learns what ran.
  Two things are deliberately backwards from an ordinary queue. **A lease has to be
  renewed**: a `flock` dies with its holder, so a killed job cannot strand a slot, but
  an HTTP lease can — hence the heartbeat thread, and hence a lease that expires. And
  **failing to get a slot is fail-open, losing one is not**. Unconfigured,
  unreachable, refused, or busy for half an hour all leave the work running unqueued
  with a line in the song's live log saying so: none of them has told us what else is
  running. A heartbeat answering **404** has told us — the lease is not held, so the
  cores may already have been promised to somebody else, and carrying on is the
  two-jobs-on-four-cores case the queue exists to prevent. So that stops the work
  (`SlotLost`). Stopping a call that lasts minutes needs somewhere to stop *at*:
  `Slot.guard` wraps the work's own log callbacks. (The scrolling video render was the
  first user and is gone. Publishing takes one too, around its MuseScore exports:
  `server.py`'s publish job runs under `heavy_slot`.) Run song_app tests that publish with
  `env -u AGENTDECK_URL -u AGENTDECK_API_URL`: a deck chat sets `AGENTDECK_URL`, so
  `heavy_slot` waits for a real host slot and the run hangs until its timeout.
  `src/song_app/tests/test_heavy_slot.py` pins those rules: every way the deck can fail
  to co-operate leaves the work running, a lease is renewed while the work runs and
  released when it raises, and a 404 heartbeat stops it.
- The **Fix** panel lists the health issues and the outstanding free-text fixes, and
  this pull request proposes it also **record a missing slur** — see the `fixes.json`
  notes in [fixes-json.md](fixes-json.md) for why that judgement can only come from a person. Two routes:
  `GET /bar` hands back the singing parts, the bar count and one bar's chords in the
  numbering a recorded fix uses (`pipeline.bar_for_fix`), and `POST /fixes/slur`
  appends the entry and applies it (`pipeline.record_slur_fix`), then claims its own
  write with `_rescan` so the file watcher does not read it as a MuseScore edit and
  re-check a second time. `GET /bar` answers with the choices even when no bar is
  named, because the panel needs the parts before it can ask about a bar and both come
  off one parse. Each note is shown with whether the lyrics land on it today, so the
  panel can say *before* the write that the bar is about to lose a syllable — and it
  warns outright when lyrics are already imported, because that line will come back
  one syllable too long. The cleaned system crop is shown alongside where one is
  available (`/compare` + `/cleaned-system/{index}`); it needs a MuseScore render, so
  not having it costs a picture rather than the feature.
- **The Fix panel offers homr's other readings of an unsure bar** (#269,
  `bar_readings.py`). For each bar homr doubted, homr writes the three likeliest
  readings that fill it (note lengths only) into the fragment's MusicXML
  (`identification/miscellaneous`, field `homr-bar-readings`, eerovil/homr
  `homr/bar_readings.py`). An offer is placed on the cleaned staff whose bar holds those
  notes at those lengths — matched by content, since cleaning renumbers parts, staves and
  voices — and shown under the page crop, each option engraved by verovio. A pick is a
  `rhythm` entry in `fixes.json`, applied in place (a re-clean would lose lyrics), which
  rewrites the lengths, re-brackets the triplets, moves ties and slurs with their notes
  and takes `rhythm?` off the red mark (#290: only what a pick answers comes off — a
  whole-bar pick takes homr's `rhythm?`, `pitch?`, `accidental?`, `notes?`, a pitch pick
  `pitch?` and `accidental?`; `voice?` and cleaning's own marks stay, except that `long_bars`' "was 9/8, cut to 4/4 in voice 1" mark names the voices it cut, and a `bar` fix, which writes voice 1 only, takes off a mark naming voice 1 alone once voice 1 fills the bar; a mark naming another voice, or from before marks named voices, needs an `unmark`, #482). It carries the fragment's content stamp, and
  `drop_stale_picks` removes it before a clean once that system has been read again
  differently. "None of these" is kept in `.song.json` (`readings.declined`).
- **Every problem is one list, each with its choices** (#290, `problems.py`, `GET
  /problems`, `POST /problems/pick`). The panel used to say one problem up to three
  times — a `fixes.json` sentence, a red-mark health row, the bar again under "Unsure
  bars" — and only the last offered anything to tap. Now a row is one bar of one part:
  everything wrong there, the page crop, and its a/b/c choices. Red marks are read
  **live off the cleaned score**, not off the `clean-marker` sentences, which are only
  rewritten at the next clean and so kept listing marks a person had already deleted;
  a dismissed health row still hides its mark. The choices: homr's lengths (#269);
  homr's other **pitches** for a note whose pitch or accidental it doubted (field
  version 2, key `notes`, eerovil/homr#91), recorded as a `pitch` entry; and for a slur
  cleaning took out because it ran between two singers, the slur back in either, both,
  or none — `cross_voice_slurs` keeps where both halves stood in a `removedSlurs`
  metaTag, a pick is `slur` entries (which may now reach into a later bar) plus
  `unmark` entries for both red marks. A song cleaned before this has no metaTag, so
  its slur marks are listed without choices until it is cleaned again. `voice?`, `notes?` and the other sentences are
  listed with nothing to pick.
  **A repeat with no start is asked about too** (#312, `problems.repeat_questions`): an
  end-repeat sign with no start sign since the previous end, because homr misses a
  start sign that opens a printed system and the track then repeats the wrong bars.
  The choices are the first bar of each printed system in between, as words; a pick
  is a `repeat` entry in `fixes.json` (a start sign on every staff), and **a**, "no
  start sign on the page", is kept in `.song.json` (`repeats.kept`) so it is asked once.
  **So is a repeat with no brackets** (#319, `problems.volta_questions`): homr reads no
  volta brackets at all — on Shakkitarina they stand above the chord names, past the
  room homr keeps above a staff — and a repeat without them looks the same in the
  score, so **every** end repeat with no bracket over it is asked once. The choices are
  "1." over the last 1-4 bars; a pick is a `volta` entry (`score_fixes._add_volta`,
  on the top staff, where MuseScore keeps voltas) and "2." is always one bar, since
  only the "1." length changes what is played. **a**, no brackets, is kept in
  `.song.json` (`voltas.kept`). Kantajani bar 18's "1. kerta / 2. kerta" is not a
  volta — it is two versions of one bar for one voice, printed as text.
  **#295 replaced the separate length and pitch choices with whole bars**, because
  picking them one after the other mixed them up (the pitch options were drawn with
  the old lengths). `bar_readings.whole_bars` pairs every reading of the lengths with
  every pitch for each unsure note, ranks them by homr's log-likelihood plus each
  pitch's log-probability, and offers at most `MOST` (18), `SHOWN` (6) before
  "More". **a** is the bar as read; **b** is homr's **second reading** when there is
  one (field version 3, key `second`: the crop read again at 80% read the bar
  differently — the `notes?` mark), whatever it would rank, since it is what catches a
  mistake the decoder was sure of. homr writes it only where the two readings line up
  voice for voice and both fill the bar. A pick is one `bar` entry
  (`score_fixes._replace_bar`): with the notes in the same places the lengths are
  rewritten as `rhythm` does and the pitches set, keeping ties, slurs and words;
  otherwise the bar is written afresh, ties and slurs reaching in are cut, and the words
  go back on its notes in order. `rhythm` and `pitch` entries already recorded still
  replay, and their bar shows as decided.
  **A bar a recorded fix already wrote is not offered** (#368). An offer lands on
  whichever part holds homr's notes *now*, so on Annin laulu bar 8, after page-checked
  `bar` fixes had un-swapped the basses, homr's B2 line was offered on B1 and picking
  "a" wrote the swap back; the clean then failed on the clash. So a bar a `bar`,
  `pitch`, `rhythm`, `duration`, `undot`, `append`, `drop`, `dropnote` or `addnote`
  entry (or an `unmark` of `notes?`/`rhythm?`/`pitch?`/`accidental?`) has written is
  decided as `{"answered": ...}` and listed as "answered by fixes.json" with the fix's
  `why` (`bar_readings.ANSWERING`, counted in file order past `delbar`/`insbar`). Each
  remaining choice names the bar and part in its title, marks an option that is another
  part's current line ("the line B2 has now — gives it to B1", `line_of`), lists what
  `fixes.json` already does to that bar on this part and the parts printed with it
  (`fixes`), and boxes the bar on the page crop: `GET /system/{n}/where` →
  `system_finder.bar_box`, which finds the staff by its lines and the bar by barlines
  that stand clear of noteheads **and** at the same x on every staff, drawn only when
  their count is the system's bar count (else the whole staff, dashed; nothing when the
  crop shows other staves than the score records). Measured on ten songs' crops, about
  60% of systems get the bar.
- **The score can be taken away and brought back**, which this pull request proposes
  (#216). Both editing routes the app had assumed MuseScore was on *this* host:
  `open-score` shells out to `open -a`, and the file watcher re-checks a score saved
  under `songs/`. From the phone the app is actually used from — or from any other
  computer — neither happens, so the Fix and Review stages could be read and never
  acted on. `GET /score-file` sends the cleaned `.mscx` as a download under its own
  name (with voice instruments written into the copy for a song cleaned before #481,
  `pipeline.score_for_download`; the stored file and its fingerprint are untouched), and `POST /score-file` puts the fixed one back: `pipeline.accept_uploaded_score`
  parses it first and only then moves it into place, so a refused upload leaves the
  score that is there alone — this is the one route that overwrites the file every
  later stage is derived from. `.mscz` is accepted as well, because that is what
  MuseScore's Save As writes by default. Landing it is deliberately **an edit and not
  a stage**: the route claims its own write with `_rescan` (so the watcher does not
  check the same file twice), the health record is re-taken against what arrived, and
  an approval recorded against the old fingerprint lapses by itself. Nothing is
  replaced while a scan, clean, check or publish is running — that would be read
  half-written — and the panel's confirmation survives its own refresh, or the only
  sign the file arrived would be the health rows quietly changing.
