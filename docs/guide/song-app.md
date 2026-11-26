# The song web app: state, pipeline, health, server, publish

Part of this repository's agent guide; the index is [CLAUDE.md](../../CLAUDE.md). Moved out of it word for word (#513). These are bullets of the guide's section on the song web app, which is split over [song-app.md](song-app.md), [song-app-panels.md](song-app-panels.md), [omr.md](omr.md), [omr-systems.md](omr-systems.md) and [scan.md](scan.md), in that order; "above" and "below" in the text may point into a sibling file. This file also holds the last bullets of the section: MuseScore's check after a clean, Check again, Undo last clean, the `WRITERS` job list.

## The song web app (`src/song_app/`)

A local **FastAPI** web app (launched by `./song.py`, served at `localhost:8000`)
that unifies the workflow behind one state-aware door. It is a **thin frontend
over the existing scripts** — it adds no musical logic; it shells out to
`clean_score` (`main()`) and `lyric_txt` (`import_file`), drives the MuseScore CLI
for previews and the publish exports, and opens MuseScore via `open -a`. Full rationale and the
state model are in `DESIGN.md`.

- **Before working a song, check its source.** Song input scores come from Soundslice plus
  hand fixes, so a reference built from a cleaned score is not ground truth: treat any
  homr-vs-reference disagreement as a candidate and check it against the printed band.
  Check that the input in `.song.json` is the raw import ("Track N" part names, crowded
  voices, partial lyrics), not a finished score beside it; cross-check it bar by bar
  (allowing a small offset) against another copy in `songs/`; and assume OCR lyrics are
  mojibake and read the words off the page.
- **A Song** = a folder `songs/<slug>/` plus `.song.json` (the state file *is* the
  UX). `state.py` owns the slug, the human display name, the stage machine
  (`register → scan → clean → fix → lyrics → review → publish`), and file
  fingerprints. The folder is a slug; the display name lives in the JSON.
- `pipeline.py` is the glue: `convert_to_mscx` (mscx as-is / mscz unzip / xml via
  MuseScore CLI), `run_clean` (calls `main(..., interactive=False)`; per-system runs off
  the answers the **grid form** recorded via `save_system_answers`), and
  `run_lyric_import` (calls `import_file` and returns its `LyricImport` — the
  mismatches come back as records, nothing is scraped from stderr). `system_grid` / `save_system_answers` /
  `has_system_answers` / `system_ranges` are one-liners over the per-system module's
  interface (`layout_for_file`, `save_answers`, `has_answers`, `system_ranges`) —
  the grid is an adapter, not a second implementation.
  `render_score_pdf` exports a `.mscx` to PDF via the MuseScore CLI (cached by
  mtime) so scores can be shown in-browser next to the original PDF — it renders
  from a temp copy with the staff size (`<Spatium>`) shrunk by `SPATIUM_SCALE`
  (env `RENDER_SPATIUM_SCALE`, default 0.65) so the score's own system breaks fit
  the page instead of MuseScore adding extra ones. For the cleaned previews it
  also **puts the printed line breaks back** (`line_break_measures` reads them off
  the converted input, `_apply_line_breaks` writes them onto the top staff), because
  normal-mode cleaning strips them and the preview otherwise reflows into
  MuseScore's own systems and cannot be read against the page. On the fixture this
  renders the fixture's systems exactly as printed. Breaks alone are not enough to
  guarantee that: at full staff size a system too wide for the page is split anyway
  and MuseScore adds a break the page never had — the fixture's lyric-applied score
  comes out as 17 systems instead of 15. So the render **tries the scales in
  `BREAK_SCALES` (0.85, 0.75, 0.65) largest first and checks the result**, using the
  biggest staff that keeps the printed system count. Lyrics matter to that: they
  widen the spacing, so a score that fits at full size without them may not with
  them. Not every
  source has breaks; without them the render is unchanged. They are applied by
  measure index, so nothing is applied unless the score is long enough, and the
  two variants cache to separate files (`.render.pdf` / `.breaks.render.pdf`);
  **The breaks are moved into the cleaned numbering** (`cleaned_line_breaks`, #354):
  the converted input still counts a bar a `delbar` took out, so applied as read,
  every system after it started a bar late. The render's cache is keyed on the breaks
  too (a `.key` file beside it, with `RENDER_VERSION`), since a recorded fix moves the
  breaks without the cleaned score's mtime saying so.
  `strip_lyrics_copy` writes a lyrics-removed copy (cached) so the "Cleaned MSCX"
  (no-lyrics) view always reflects the live structure rather than a stale snapshot.
- `pdf_systems.py` cuts the **original PDF** into one image per printed system, so
  the score can be read at a resolution where a slur or a lyric line under the lower
  staff is actually visible; a whole A4 rendered small enough to look at is not.
  Shells out to poppler (`pdftoppm`, `pdfinfo`) — not a pip dependency, so the
  Systems tab and the lyric crops are simply unavailable without it (tests skip).
  **Where the boundaries come from is deliberately not decided here.** Detecting
  them from the image was tried and removed: staff-line detection died at 0.5° of
  skew and 20% ink dropout, and grouping staves into systems relied on a
  left-margin bracket only some editions print — across nine real songs it agreed
  with the score twice. Instead an AI reads them off the page
  (`page_images(grid=True)` overlays a labelled percentage scale, which turns
  estimating coordinates into reading them) and a person corrects them by dragging
  in the **Systems** viewer tab; `system_finder.py` (below) can now propose them
  from homr's own staff detection, and it proposes into that same editor rather
  than writing anything. They live in `.systems.json` beside the song as
  fractions of page height, so they survive any change of resolution, and the app
  and an agent read the same file. `crop_systems` rasterises **only the band**
  (`pdftoppm -x -y -W -H`): a page at 400 dpi takes ~7s, one system 0.9s, and this
  is on the path where someone clicks a lyric cell and waits. A page's pixel size
  is read **with its rotation flag applied** (`_page_info`), because `pdfinfo` reports
  the stored size and `pdftoppm` renders the turned page: a landscape-stored page
  flagged 90° was cropped by the wrong height and homr read half of two systems
  (#272). `crop_version` adds a suffix to the crop and scan stamps of such a PDF only,
  so its old crops and fragments are discarded while every other song's stay. `label()` attaches
  each band's measure range from a score that still has its line breaks — the
  converted input, since normal-mode cleaning strips them — and **refuses when the
  counts disagree**, because a silently wrong alignment puts lyrics on the wrong
  measures while a missing one is visible at once. The server, never the browser,
  assigns indices and labels on save.
- `health.py` is **validation only** (never mutates): per voice it sums note/rest
  durations as exact whole-note `Fraction`s (so tuplets don't round-off) and flags
  `malformed-measure` (voice doesn't fill the bar), `extra-voices` (a staff
  measure with >1 note-bearing voice), and `unprinted-meter` — a bar every voice
  agrees on, at a length no printed time signature gives. **That last one is the
  only check that does not compare the score against itself**, and it exists
  because everything that does can be satisfied by a self-consistent wrong answer:
  a repair pass once "fixed" a 4/4 bar by padding every voice to 9/8, and health,
  the lyric arithmetic and the tests were all happy. Measure 1 is exempt (an
  anacrusis prints no signature), and so is a last bar that makes one full bar with that
  anacrusis (#353: Kesäaamu opens on a sixteenth in 6/8 and closes on 11/16) and an already-uneven bar is left to
  `malformed-measure` rather than reported twice. It also stays out of music with
  no meter to violate: a score carrying an oversized nominal in place of a
  signature (one here declares 16/2 — eight whole notes — for music printed
  without a meter). Across the 35 cleaned songs in `songs/` it reports ~35
  bars, concentrated in five scores; spot-checked, they are real — including a
  mixed-meter piece that silently changes bar length 16 times, i.e. dropped time
  signatures.
  **A score where most bars declare their own length used to switch the meter rule
  off, and this pull request proposes that it collapse the finding instead** (#124).
  The escape is not wrong to exist — Venematka overrides 20 of its 25 bars and would
  otherwise be reported 66 times for being what it is, which is the noise that trains
  an operator to ignore a check. But "carries a length override" is also what a badly
  parsed score looks like: `fix_overfull_measures` writes one onto every bar whose
  content contradicts the running signature, so the check turned itself off exactly
  when the score was worst. Benchmark page B6 crossed the 50% line **by being more
  wrong** — misreading an opening 5/4 as 3/4 needed an override on bars 1 and 2 of all
  four staves, and those eight carried it from 50% to 56% — and reported 3 issues
  where the same score judged on the same rules has 32. A score bought silence by
  being worse, which is this project's named failure occurring inside the checker.
  So the share now decides only **how the finding is said**: above the line the bars
  are counted into one `meter-collapsed` finding naming how many bars, the first one
  to look at, and the share itself. Venematka's 66 become one line, B6's 32 stay 32,
  and the two sides of the line are comparable rather than one of them blank. Nothing
  to count is nothing to say — a free-metered score that agrees with itself is still
  clean. The threshold stays at 0.5: across `songs/` every score's override share is
  ≤0.19 except Venematka's 0.76–0.80, so the line sits in an empty gap, and crossing
  it now costs presentation instead of silence. The summary's id carries the count
  (`meter-collapsed-66`), so dismissing "4 bars disagree" does not cover "6 bars
  disagree".
  **Collapsing the presentation is the point; collapsing the number would be the same
  bug one level up**, so the row carries `collapsed` as an integer and every count a
  person reads goes through `health.finding_count`, which weighs a row by what it
  stands for: the library badge (`state.to_summary`), the "N issue(s) to review" line
  the clean finishes on (`server._run_clean`), and the Review stage's health result
  (`verification.summary`, which also puts `open_count` / `row_count` /
  `collapsed_count` on the wire and says in its detail how many findings are behind
  the one line). Counting rows would have put B6 straight back where it started — "4
  open issue(s)" next to a per-system parse's 28. The cost is that Venematka's Review
  stage reads 81 rather than 16; that is the true number, and its detail says 66 of
  them are one line in the Fix panel. Missing notes that still fill the bar (a
  half-rest standing in for lost notes, e.g. the m18 case) are **not**
  tick-detectable — they surface as lyric syllable overflow at import. Missing
  slurs are undetectable and stay manual. `merge_issues` carries over `dismissed`
  status and marks vanished open issues `fixed` across re-scans (ids are stable:
  `malformed-m18-s2-v1`).
  **There is no verdict on the parse as a whole any more** (#356). #170 added one — "this
  parse looks unusable" once findings landed on more than a fifth of the bars — on the
  walk's 60-finding song. Once homr's `⚠` doubt marks (#245) were listed as findings it
  went off on most scanned songs whose notes were right: homr marks a bar it is merely
  unsure of, on purpose, so `slur?`, `tie?` and the rest filled the share (Nälkämaan
  laulu 69%, all slur questions). The owner had it removed outright rather than
  reweighted: the count, the Fix rows and the Scan panel's attribution below say enough.
  **At the Scan stage health is not knowable**: it needs a cleaned score, which is two
  stages along. What the scan stage gets instead is **attribution**: once a song has been cleaned, `scan.findings_by_system`
  maps each finding's bar to the printed system it fell in, and the panel names the
  worst systems directly above the buttons that re-read one. It answers `None` — not
  zeros — whenever the numbering cannot be trusted, because a wrong system number sends
  somebody to re-read music that was read correctly: no clean yet, a hole, fragments
  whose bar lengths no longer add up to the cleaned score, or a health record checked
  against an older one. That last is the same `checked_against` test `verification`
  calls stale, and it is not covered by the bar count — editing a score in MuseScore
  changes what is in the bars and not how many there are — so without it the panel
  would send somebody back to a system they had just repaired.
- `server.py`: REST routes under `/api/songs/...`, a per-slug WebSocket (`/ws/{slug}`)
  for streamed progress logs + `state` pings — `hub.emit` **never raises**, because a
  job runs for minutes in a worker thread while the browser may come and go, and a
  closed loop surfacing there used to abort work that was going fine — long tasks
  (scan, clean, check, publish) run in a thread executor with a thread-safe
  `hub.emit`, one at a time per song (`job_state`). The song's display name is
  editable on the Start panel (`POST /rename`); the folder slug never changes, and a
  song already on the site shows the new name there once it is published again.
  **Delete** (#428, `DELETE /api/songs/{slug}`, the Start panel's *Delete song*) moves
  the folder to `songs/.trash/<slug>-<time>/` (`state.trash`) rather than removing it:
  moving it back restores the song. Refused (409) while any job or a scan runs; a
  published song is only named in the reply, since the site keeps its own copy.
  `import_legacy` skips dot folders so the trash is never adopted as a song.
  A request that loaded a song before it was deleted (and perhaps re-created under
  the same slug) must not touch it: `Song.check_still_here` compares the loaded
  copy's `created_at` with the file, under `song_lock`, and every save and every job
  start (`state.start_job`, song lock then job lock) goes through it; `create`
  claims a slug under the same lock. A mismatch is `SongGone`, a 404.
  **Publish** (`publish.py`, `POST /publish`, body `{choir, settings}`) is where a
  song ends: it sends the song to the Cloudflare stemmanauhat site
  (eerovil/stemmanauhat-cf), which draws the score with OpenSheetMusicDisplay and
  plays its MIDI. It uploads `score.musicxml`, `score.mid`, `timing.json` and
  `manifest.json`, all off one `score_export.score.prepare` copy so the parts, the
  MIDI and the clock agree; the clock is MuseScore's `.mpos` bar order plus its MIDI
  tempo map, cross-checked. Files go to R2 under a fresh version prefix, then one D1
  row per (choir, slug) is upserted, with the composer, lyricist and arranger. The
  site's migrations own the `songs` table; publishing creates nothing. **Tempo marks go
  up unprinted** (#409, `publish.hide_tempo_marks`): OpenSheetMusicDisplay puts a
  tempo mark above the staff without making room, so it landed on the lyrics of the
  system above. MuseScore exports the marks `prepare` adds as visible whatever the
  score says, so the exported MusicXML gets `print-object="no"` on every
  `<metronome>` and tempo `<words>`; the speed stays in `<sound tempo>` and the MIDI.
  `scripts/hide_published_tempo_marks.py` did the same for the songs already up, by
  copying each version into a fresh prefix (the site caches a version's files as
  immutable, so nothing can be changed in place). It refuses a
  score Review has not approved, and needs `CLOUDFLARE_ACCOUNT_ID`,
  `CLOUDFLARE_API_TOKEN`, `STEMMANAUHAT_D1_DATABASE_ID` in `.env`; the contract
  with the site is `docs/stemmanauhat-site.md`. The **Publish panel** holds what the
  site plays and shows a song with, saved under `publish` in `.song.json` by
  `POST /publish-settings` (and by Publish itself, before it runs): the opening BPM
  (only for a score with no tempo marking), the tempo changes, and the **credits**,
  prefilled from the score's own `composer`/`lyricist`/`arranger` metaTags until
  saved (`publish.score_credits`), then kept as `publish.credits` — an emptied one
  stays empty. Each choir's publish is recorded under `publish.sites`.
  **The choirs are the site's own** (#408): `publish.choirs` reads the site's D1
  `choirs` table (`GET /api/choirs`), keeps the last list in `songs/.choirs.json`
  for when Cloudflare is out of reach, and a publish is refused (502) until a live
  read succeeds. There is no list in the code, and the choir is no longer guessed
  from the song's voicing; `public` is the one id every site has. A choir marked
  `staging` is the site's test list (#422): publishing there removes the song from
  no other choir, while publishing to a choir still removes it from the test list.
  Publishing to a choir leaves every other choir's listing alone (#450: the old
  one-private-choir rule took songs shared by `joy` and `joy-alumnit` off one of them).
  **Part names** (#402) are set there too: cleaning renames every part by guessing
  from pitch unless the song has a voicing, so older songs reached the site with
  tenors in a women's choir. `publish.part_names` keeps one name per singing part,
  top staff first, and `score.prepare` writes them into the publish copy's
  `trackName` and instrument long/short names, so the MIDI tracks, the MusicXML and
  the database agree; the cleaned score is never renamed.
  **New part names go into the site's voice tree** (#430, stemmanauhat-cf#107): the
  site carries a singer's part between songs by a tree of voices in D1 and refuses
  a name in neither `voices` nor `unplaced_parts`. `POST /api/voice-tree` says which
  names are new and offers the places; the panel asks, per name, another name for a
  place, a new place under one, or no place, saved as `publish.places`, and
  `publish.place_statements` writes those rows before the upload. Never guessed: an
  agent publishing asks Eero. A site without the tables is published to as before. Empty or repeated names
  are refused (the site tells parts apart by name), and a re-clean that changes
  the number of parts makes Publish refuse until they are checked
  (`server._publish_parts` says so on the panel).
  **The video stages are gone** (#400): the scrolling renderer, the macOS screen
  recorder, YouTube upload, playlists, freeing local videos and the old site's refresh
  were all removed, and with them `record` and `upload` in `state.STAGES`. A song saved
  on either is read as `publish` (`state._upgrade`), and its `record.bpm` /
  `record.tempo_changes` are copied to `publish`; the rest of `record` stays in the
  file untouched. Videos already on YouTube, the old GitHub Pages site and media
  files on disk are left alone.
  Legacy `songs/<name>/`
  folders from the old CLI workflow are adopted by `import_legacy()` (run on
  startup and via `POST /api/import` / the Library's "Import existing" button): it
  infers a `.song.json` from the files present — input score, `*_cleaned.mscx`
  (+ health scan), PDF, `lyrics.json`, and per-system mode from the answer cache —
  and sets the stage accordingly (cleaned→review, input→clean, PDF→scan). It's
  idempotent and skips
  folders that already have a state file. There is also a **file watcher**
  (`watchfiles.awatch` on `songs/`) that re-runs the health check when a
  `*_cleaned.mscx` is saved in MuseScore (guarded by fingerprint so our own writes
  don't loop). Static SPA is mounted at `/` (so `/api/*` wins).
  **Freshness.** The app sends `Cache-Control: no-cache` on
  every file it serves — the SPA shell, `app.js`/`style.css` (via
  `RevalidatingStaticFiles`), the PDFs and the system crops. Sending
  no `Cache-Control` at all is not the same as forbidding caching: a browser
  given no instruction invents a freshness lifetime from `Last-Modified`, and
  Chrome on Android invents a generous one, so a phone kept serving old code
  off its own disk until the browser was restarted (#34). `no-cache`
  is not `no-store` — the copy is kept and `FileResponse`'s ETag turns an
  unchanged file into a small 304, so this costs a round trip, not a download.

- **Every clean asks MuseScore whether it would open the result** (#235). MuseScore
  checks a score as it opens it (`Score::sanityCheck`: voice 1 of each staff must fill
  the bar exactly, no other voice may run past it) and calls one that fails
  "corrupted" -- on Sangerhilsen the app crashed instead. Only the app runs that check;
  a command-line export does not, which is why every render went through and nobody
  heard of it until a person opened the file. The one MuseScore 3 export that does run
  it is `-o x.mlog`, so with MuseScore 3 `pipeline.musescore_check` asks MuseScore
  itself. **MuseScore 4 writes no `.mlog`** and refuses such a file outright when it
  loads it, writing the reason only to its own log file (#481). So with MuseScore 4 the
  check is the app's copy of that rule, `clean_score/utils/overlong_voices.py` (a voice
  running past its bar), and the record says which ran (`verification.musescore.by`).
  The raw scan is converted and rendered with `-f` so MuseScore 4 lets it in, and the
  Clean panel lists those bars off `GET /systems`'s `overlong` (read off the converted
  file on every load, so a reload shows them too) and the clean's log names them; the
  cleaned check and publish never use `-f`. When a MuseScore 4 run fails,
  `musescore_cli.failed_load_reason` reads the reason off its log (lines stamped during
  the run, and only a refusal naming the file this run passed, since two songs may convert
  at once) and the error names the bar
  instead of blaming `MUSESCORE_CLI_PATH`; a MuseScore that does not start at all is
  named once, in `musescore_cli.run`, so every render and export says so. Without any MuseScore the song records
  "not checked". Note MuseScore fills *gaps* with rests while reading, so a
  short voice passes; what fails is a voice that runs past the bar, typically a
  misread triplet. `check_opens_in_musescore` runs after the recorded fixes: each
  staff-bar MuseScore rejects is reset to a whole-bar rest (`rejected_bars.clear_bar`,
  which also cuts a tie or slur reaching into it), the notes taken out are written into
  `fixes.json` as a `text` entry (`source: "musescore-check"`, replaced on every clean
  so a bar a better reading fixed stops being listed), and the score is checked again.
  Anything still refused becomes a `musescore-corrupt` health row
  (`server._health_scan`). The outcome is kept as `verification.musescore` and shown as
  the Review stage's **Opens in MuseScore** row. Without a MuseScore it says "not
  checked", never "fine". The reset loses notes on purpose: there is no reading of the
  page left in such a bar, and a file that will not open blocks the one route a person
  has for putting the right notes back.
- **Check again and Undo last clean** (#391). `POST /recheck` (job `check`) runs the
  clean's note comparison, MuseScore's check (3 or 4) and health on the cleaned score as it
  is, and records them against its fingerprint. It **only reports**: unlike a clean it
  resets no bar MuseScore refuses (those become `musescore-corrupt` health rows), since
  the score may be a person's own edit. Before a clean replaces the score,
  `clean_backup.stash` copies it and the records made against it (`verification`,
  `health`, `lyrics`, `stage`) to `backups/<time>/` (last 10 kept; dropped if the clean
  fails); `POST /undo-clean` puts the newest back. The stash records the fingerprint
  the clean made (`mark_made`), and Undo refuses (409) once the score differs from it,
  since anything done after the clean would be lost. `fixes.json` is not restored.
  A new job that writes the song shares the busy guard with every sibling job, both ways:
  it refuses while they run, and they refuse while it runs. The list is one tuple,
  `server.WRITERS` (`scan`, `clean`, `check`, `publish`, `lyrics`, and `fix` for the short
  routes that rewrite the score: an upload, a recorded slur, a picked reading or answer,
  Undo, each run through `_fix_job`). Add its kind there, start it with
  `state.start_job(song, kind, WRITERS)`, and test both directions. `_publish_busy` reads
  the same tuple. `homr_install.SONG_JOBS` is a separate list (the jobs a homr install
  waits for). Its undo refuses once the score has changed since.
- **Hazards guarded:** re-cleaning warns it discards manual edits (the Clean
  button label changes once a cleaned file exists); lyric import uses `--replace`.
  No automatic LLM (users have no API key) — the lyrics stage supports either a
  copy-paste round-trip (copy prompt → user's own AI + PDF → paste JSON back) or
  direct per-system entry.

There are not yet pytest tests for `song_app`; it was smoke-tested end-to-end
(create → per-system grid → clean → health → lyric import overflow warnings)
against the `laulun_aika.mscx` and `simple_1` fixtures.
