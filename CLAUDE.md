# CLAUDE.md

Guidance for working in this repository.

## What this project is

A personal toolkit for producing **choir practice tracks** from MuseScore 3
sheet music. It has two halves:

1. **MuseScore QML plugins** (`plugins/`) that run *inside* MuseScore 3 to edit
   scores and export audio.
2. **Python scripts/packages** (`src/`, plus thin CLI wrappers in the repo root)
   that process MuseScore files (`.mscx`/`.mscz`/MusicXML) as XML, fix lyrics,
   and automate audio/video recording + YouTube upload.

The end goal: take a SATB-style score where multiple voices share a staff, split
it so each voice has its own staff, generate per-voice practice audio (each voice
louder than the rest), record a play-along video, and optionally upload to
YouTube.

MuseScore `.mscx` files are XML; almost all musical processing is `lxml` tree
manipulation. There is no database: persistent app state is JSON/files under
`songs/`. The local FastAPI server and vanilla-JS frontend in `src/song_app/`
orchestrate the same file-based tools.

## Layout

```
plugins/                 QML plugins for MuseScore 3 (install separately, see below)
song.py                  Launcher for the song web app (FastAPI; src/song_app/)
clean_score.py           CLI wrapper → src/clean_score/main.py (split voices into staves)
lyric_txt.py             CLI wrapper → src/clean_score/lyric_txt.py (lyrics <-> txt/json)
rename_parts.py          Standalone CLI: rename Part/Instrument names + add click staff
record_stemmanauha.py    CLI wrapper → src/stemmanauha (record practice video)
scroll_video.py          CLI wrapper → src/scrollvideo (render scrolling practice video)
src/song_app/            Local web app tying the workflow together (see DESIGN.md)
  state.py               Song state machine (.song.json), slug, stages
  health.py              Health check (malformed-tick / extra-voice scan; no mutation)
  pdf_systems.py         Crop the source PDF into one image per printed system
  pipeline.py            Glue: convert + clean (clean_score) + lyric import (lyric_txt)
  server.py              FastAPI routes, WebSocket progress, file-watch re-check
  static/                Vanilla-JS SPA (library + 3-pane workspace, PDF viewer)
src/clean_score/         Score-cleaning package
  main.py                Voice-splitting pipeline (single-staff/2-voice -> 2-staff)
  lyric_txt.py           Lyric export/import (txt + json formats), slur/tie aware
  utils/                 part_types, reversed_voices, missing_ties,
                         corrupted_measures, utils, globals
  tests/                 pytest
src/scrollvideo/         Scrolling practice video rendered from the score (no GUI)
  engrave.py             verovio: one continuous system -> SVG + timemap
  geometry.py            SVG -> note positions; SVG -> pixel strip (tiled)
  timing.py              MuseScore MIDI tempo map = the clock; note on/off events
  audio.py               per-voice mixes + MuseScore CLI calls
  video.py               frame compositing (scroll + highlight) -> ffmpeg
  build.py               orchestration (the public build_videos)
src/stemmanauha/         Audio/video recording automation (macOS, AppleScript + OBS/ffmpeg)
  create_video.py        Orchestrates mp3 export -> video record -> merge -> upload
  upload_to_youtube.py   YouTube Data API upload
  *.scpt                 AppleScript files driving MuseScore + QuickRecorder
fixtures/                In-repo prototyping song (see fixtures/*/README.md, STEPS.md)
songs/                   Per-song working dirs (gitignored, output lives here)
backup/                  Gitignored .mscz backups (created by backup.sh)
*.txt prompts            lyric_json_prompt.txt, lyrics_txt_prompt.txt (LLM prompts for lyric fixing)
```

## Environment & running

- Python 3.13, virtualenv at `.venv/`. Use `.venv/bin/python` directly.
- Install deps: `.venv/bin/pip install -r pip-requirements.txt`
  (lxml, pytest, dotenv, google-api-python-client, google-auth-oauthlib, pillow,
  pyautogui, fastapi, uvicorn, python-multipart; recording also needs
  `ffmpeg`/`ffprobe` on PATH, and macOS with MuseScore 3 + QuickRecorder).
- Config is via `.env` (falls back to `.env.default`). Keys:
  `MUSESCORE_CLI_PATH`, `MUSESCORE_EXPORT_PATH`, `VIDEO_EXPORT_PATH`,
  `YOUTUBE_CLIENT_SECRETS_PATH`. Never commit real secrets;
  `.env`, `client_secrets.json`, and `token.pickle` are gitignored.
- The CLI wrappers import the package via `from src.clean_score... import ...`,
  so **run them from the repo root** (e.g. `./clean_score.py ...`).
- `./song.py` prefers port 8000, scans the next 49 ports when it is occupied,
  and enables uvicorn source reload by default (watching only `src/`). Use
  `--port`, `--no-browser`, or `--no-reload` when needed.

### Common commands

```bash
# Split shared-staff voices into one-staff-per-voice. Accepts .mscz/.mscx/.musicxml/.xml or a dir.
./clean_score.py "path/to/score.mscz"
./clean_score.py songs/MySong --add SSAA            # also append empty Soprano1/2, Alto1/2 staves
# Output -> songs/<name>/<name>_cleaned.mscx

# Lyrics export/import (slur/tie aware; only first note of a slur/tie gets a syllable)
./lyric_txt.py export score.mscx -o lyrics.txt
./lyric_txt.py import lyrics.txt score.mscx -o score_updated.mscx
./lyric_txt.py import lyrics.json score.mscx --split 3,4   # json only: duplicate parts 3,4 into two staves each

# Rename parts from a part string (S/A/T/B/M/W) and ensure a click/rest staff
python rename_parts.py score.mscx SSAA -o score_renamed.mscx

# Record a practice video (macOS only; song must already exist in songs/<name>/)
./record_stemmanauha.py MySong --youtube --playlist <id>

# Render a scrolling practice video per voice, no GUI/screen recording involved
./scroll_video.py "songs/MySong/MySong_cleaned.mscx"
./scroll_video.py score.mscx -o out/ --parts S1 A1 --height 720 --no-audio

# Backup all .mscz files to backup/
./backup.sh
```

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
instead of quietly leaving the defect in.

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

A fourth kind, `text`, is just a sentence (`{"kind": "text", "what": "..."}`), because
most edits are none of the other three — taking one notehead off a chord and turning a
bar-length rest into a whole-bar rest both came up on one song in one sitting, and
neither could be written down at all. Nothing interprets it: `apply_fixes` steps over
it and `score_fixes.free_text` hands the sentences back, so cleaning logs them as still
outstanding and the **Fix** panel lists them (`pipeline.free_text_fixes`, read live off
the file, so writing one shows at once and applying it stops showing). Applying one is
a person's job, or an agent asked to do it. Refusing to clean would make the file a
hostage; skipping in silence is the failure it exists to prevent.

```bash
fixtures/virta-venhetta-vie/reset.sh        # drop it into songs/ at the furthest stage
fixtures/virta-venhetta-vie/reset.sh 00     # or: just registered, ready to clean
.venv/bin/python fixtures/virta-venhetta-vie/build.py   # regenerate the derived stages
```

Stages are overlays holding only what they add, so the 745 KB scan is stored once.
It now runs clean the whole way: **no health issues, no lyric mismatches**, and it
reaches stage `review` — which means every automatic check is satisfied, not that
the song is right. Nobody has compared it against the page end to end, the words and
their alignment are unverified, and **record** and **upload** have never been run on
it. See STEPS.md, "What clean does not mean". Two things needed a person to read the page, and both are
recorded in `fixes.json` with their reasoning — a dot the OCR invented in m26, and a
slur it dropped in m50. A third case needed no score edit at all: in m32 the bass
lower voice holds one syllable while the others sing two, which is a rhythm
difference and was fixed in the text. `STEPS.md` records how each stage
was produced, including a wrong conclusion and its correction — worth reading before
trusting a tidy-looking diagnosis of a scanned score.

### Working in a worktree (agents, read this first)

An issue worker gets a fresh worktree under `.worktrees/issue-N`, and a fresh worktree
has **no `.venv`, no `.env` and no `songs/`** — all three are gitignored and live only
in the main checkout at `~/musescore-choir-plugins`. Without them nothing runs: there is
no interpreter with lxml in it, `MUSESCORE_CLI_PATH` is unset, and the app has no songs.
Link them in before doing anything else:

```bash
for f in .venv .env songs; do ln -sfn ~/musescore-choir-plugins/"$f" "$f"; done
```

`songs/` is the **real** song folders, shared with the running app — a re-clean or a
rename inside a worktree changes the songs you actually sing. Read them freely; write
to one only when the issue is about that song, and say so in the PR. Tests never need
it: they build their own songs in a tmp dir and read `fixtures/` from the worktree.

### Tests

**While working, run only the tests related to what you changed.** The whole suite
is CI's job, not yours:

```bash
.venv/bin/python -m pytest src/song_app/tests/test_scroll_preview.py -q   # one module
.venv/bin/python -m pytest src/scrollvideo/tests/ -q                      # one package
```

`.github/workflows/ci.yml` runs everything on every pull request and every push to
`main`, in two parallel jobs (the suite, and the browser tests on their own), and
finishes in **2–3 minutes**. The same run locally takes **8+ minutes** on this host —
it is serial, and `src/scrollvideo/tests/test_preview.py` alone is over half of it
because each of its tests shells out to MuseScore. Running it before every PR was the
habit from before CI existed (added 2026-08-25); there is no reason to keep paying it.

So: **verify your change with its own tests, push, and read CI.** Cite the CI run in
the PR body rather than a local "full suite passed" count.

Two things follow from that, because **nothing is gated**. `main` has no branch
protection and no required checks — deliberately, this is a one-person project merged
by hand — and `scripts/deploy-song-app.sh` runs no tests, so a merge is on the live
app within two minutes. **Check CI is green before commenting `/merge`.** It is the
only thing that ran the suite, and nothing will stop you merging while it is red or
still running.

The full run is still the right thing before a release you are nervous about, or when
you have touched something with reach (`lyric_txt.py`, `main.py`, `build.py`):

```bash
.venv/bin/python -m pytest src/clean_score/tests/ src/song_app/tests/ src/scrollvideo/tests/ -q
# 467 passed              — with poppler, Playwright and MUSESCORE_CLI_PATH all set
# fewer                   — without Playwright the browser tests skip; without
#                           poppler the pdf_systems and bounds tests skip; without
#                           a MuseScore CLI the scrollvideo sync tests skip too
```

One trap when timing or trusting it: the scrollvideo tests call the MuseScore CLI
under a timeout, so anything else heavy running on the host at the same time makes
them fail for no reason of their own. Two `test_preview.py` failures chased in this
way turned out to be a concurrent job starving them; they pass in seconds alone.

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
- `test_missing_tuplets.py` — the dropped-tuplet cross-voice auto-fix (mirror
  within/across staves; well-formed and donor-less voices left untouched).
- `test_revoice.py` / `test_interactive.py` — the re-voicing plan and the
  non-interactive anomaly reduction.

## The song web app (`src/song_app/`)

A local **FastAPI** web app (launched by `./song.py`, served at `localhost:8000`)
that unifies the workflow behind one state-aware door. It is a **thin frontend
over the existing scripts** — it adds no musical logic; it shells out to
`clean_score` (`main()`), `lyric_txt` (`import_file`), and `record_stemmanauha`
(`create_video.run`), and drives MuseScore via `open -a`. Full rationale and the
state model are in `DESIGN.md`.

- **A Song** = a folder `songs/<slug>/` plus `.song.json` (the state file *is* the
  UX). `state.py` owns the slug, the human display name, the stage machine
  (`register → clean → fix → lyrics → review → record → upload`), and file
  fingerprints. Recording produces the per-voice videos; **upload** (YouTube) is a
  separate stage, so a song can be "recorded but not yet uploaded". The folder is
  a slug; the display name lives in the JSON.
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
  in the **Systems** viewer tab. They live in `.systems.json` beside the song as
  fractions of page height, so they survive any change of resolution, and the app
  and an agent read the same file. `crop_systems` rasterises **only the band**
  (`pdftoppm -x -y -W -H`): a page at 400 dpi takes ~7s, one system 0.9s, and this
  is on the path where someone clicks a lyric cell and waits. `label()` attaches
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
  anacrusis prints no signature) and an already-uneven bar is left to
  `malformed-measure` rather than reported twice. It also stays out of music with
  no meter to violate: a score carrying an oversized nominal in place of a
  signature (one here declares 16/2 — eight whole notes — for music printed
  without a meter), and a score where most bars declare their own length (one
  overrides 20 of 25). Across the 35 cleaned songs in `songs/` it reports ~35
  bars, concentrated in five scores; spot-checked, they are real — including a
  mixed-meter piece that silently changes bar length 16 times, i.e. dropped time
  signatures. Missing notes that still fill the bar (a
  half-rest standing in for lost notes, e.g. the m18 case) are **not**
  tick-detectable — they surface as lyric syllable overflow at import. Missing
  slurs are undetectable and stay manual. `merge_issues` carries over `dismissed`
  status and marks vanished open issues `fixed` across re-scans (ids are stable:
  `malformed-m18-s2-v1`).
- `server.py`: REST routes under `/api/songs/...`, a per-slug WebSocket (`/ws/{slug}`)
  for streamed progress logs + `state` pings — `hub.emit` **never raises**, because a
  render runs for minutes in a worker thread while the browser may come and go, and a
  closed loop surfacing there used to abort work that was going fine — long tasks (clean/record) run in a
  thread executor with a thread-safe `hub.emit`. Recording is guarded by a
  **lock file** (`.recording.lock`, holding the server pid) so a second start
  (e.g. after a page refresh) gets a 409 instead of clashing with the running
  recording; a lock from a dead/old process is treated as stale and cleared.
  The record endpoint takes `audio_delay_ms` (merge sync offset), `redo_mp3` /
  `redo_video` (re-export / re-record selectively), `merge_only` (re-merge
  existing media with a new offset, no recording), and `upload_only` (upload the
  already-merged videos — the Upload stage, no recording); outputs are listed via
  `/media` and streamed (range-capable) from `/media/{name}` for in-browser
  review. YouTube uploads report live percentage via a `progress` WS message,
  are recorded into `record.uploads` (title/id/url) for review + delete/re-upload
  (`/youtube-delete`), use the human song name for titles, and remember used
  playlists globally in `.playlists.json` (`/api/playlists`). The song's display
  name is editable on the Start panel (`POST /rename`); if videos are already
  uploaded, it retitles them (and the playlist) on YouTube in the background via
  `rename_uploads` (each upload stores its `part`, so titles rebuild as
  "<new name> <part>"). The folder slug never changes. All YouTube API calls go
  through `_with_retry`/`_execute` (`upload_to_youtube.py`): 429 / 5xx / rate-limit
  reasons are retried with exponential backoff + jitter (6 tries; the resumable
  upload's `next_chunk` resumes on retry), while a daily-quota 403 raises
  `QuotaExceeded` with a clear "try again after reset" message instead of looping.
  Legacy `songs/<name>/`
  folders from the old CLI workflow are adopted by `import_legacy()` (run on
  startup and via `POST /api/import` / the Library's "Import existing" button): it
  infers a `.song.json` from the files present — input score, `*_cleaned.mscx`
  (+ health scan), PDF, `lyrics.json`, merged `media/video/<name> *.mov` outputs,
  and per-system mode from the answer cache — and sets the stage accordingly
  (recorded→upload, cleaned→review, input→clean). It's idempotent and skips
  folders that already have a state file. There is also a **file watcher**
  (`watchfiles.awatch` on `songs/`) that re-runs the health check when a
  `*_cleaned.mscx` is saved in MuseScore (guarded by fingerprint so our own writes
  don't loop). Static SPA is mounted at `/` (so `/api/*` wins).
  **Freshness.** This pull request proposes sending `Cache-Control: no-cache` on
  every file the app serves — the SPA shell, `app.js`/`style.css` (via
  `RevalidatingStaticFiles`), the PDFs, the system crops and the videos. Sending
  no `Cache-Control` at all is not the same as forbidding caching: a browser
  given no instruction invents a freshness lifetime from `Last-Modified`, and
  Chrome on Android invents a generous one, so a phone kept serving old code and
  old video off its own disk until the browser was restarted (#34). `no-cache`
  is not `no-store` — the copy is kept and `FileResponse`'s ETag turns an
  unchanged file into a small 304, so this costs a round trip, not a download.
  Videos additionally carry a `?v=<mtime>-<size>` stamp in the URL
  `_media_list` hands out, because re-recording a part rewrites the same file
  name and an identical URL is the one thing revalidation cannot save you from
  once a range request is already cached.
- `static/` **layout**: the page never scrolls — `html, body` are fixed to the
  window and every panel scrolls inside itself. `#app` takes what the header leaves
  (`flex: 1 1 auto; min-height: 0`) and the workspace grid fills it. It used to be
  `height: calc(100vh - 49px)`, a guess at the header's height, which overflows the
  moment the header is a pixel taller — a longer song name will do it — and then the
  whole page scrolls and the viewer is carried off-screen. Grid and flex items need
  the explicit `min-height: 0`, or they refuse to shrink below their content and
  `overflow: auto` never fires.
- `static/` **on a phone**: the three panes cannot share a 390px screen, so below the
  breakpoint one is shown at a time and a bar at the bottom of the workspace switches
  between them (Stages · the current stage · Score). The bar is in the DOM at every
  width and the stylesheet hides it above the breakpoint — there is no width-sniffing
  in `app.js` that could disagree with the media query, and every mobile rule is
  additive, so the desktop layout is untouched. The breakpoint is
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
- The Lyrics panel's **Type by system** mode divides by the *printed* systems from
  `.systems.json`, not by the score's line breaks: normal-mode cleaning strips those,
  so the editor used to offer one cell per part covering the whole piece and only
  ever worked for per-system scores. `editor_grid(root, systems=[(start, end), ...])`
  takes the ranges; unlabelled bounds are refused and it falls back to the score.
  Focusing a cell shows that system in the viewer's **One system** tab (only the
  first pane follows the cursor, so a split can keep another document in view); a
  small inline crop above each block is available too, off by default.
- The Lyrics panel has two client-side modes (remembered in `localStorage`):
  **Paste from AI** keeps the prompt/JSON round-trip, while **Type by system**
  fetches `/lyric-grid` (`lyric_txt.editor_grid(...).to_dict()`) and renders a
  textarea for every `(printed system, output part)` — parts, systems and the
  prefilled text all come from the lyric module, not from `server.py`. The browser
  POSTs the raw cells (`{"cells": {system: {part: text}}}`); the server turns them
  into name-addressed JSON blocks with `blocks_from_cells` (`parts: [part name]`,
  `measure_start: system start`), writes them to `lyrics.json`, and runs the same
  importer as the paste mode. Mismatches come back as **fields** (`kind`,
  `measure_start`, `measure_end`, `staff_ids`, `syllables`, `slots`, `message`) and
  are attached to the matching system/part cell by comparing those fields — the
  browser no longer parses warning prose (a song whose `.song.json` predates this holds
  the sentence as a string; the panel reads the fields back out of it). Lyric-panel
  scroll is preserved across the
  refresh. Blank cells are omitted, so this editor expresses a lyric line starting in
  a system, not an instruction to clear one isolated cell.
- The clean panel's per-system grid mirrors the backend's answer rules: a blank cell
  inherits the staff's previous answer (shown as a faint placeholder) and `-` marks the
  staff silent from there on, clearing the carry — both cleared and never-named slots are
  flagged `unset` and listed in the "will be DROPPED" confirm before cleaning.
- The **Record stage has two renderers**, and defaults to the scrolling one
  (`src/scrollvideo`, see its own section): it draws the video from the score, needs
  no GUI and runs unattended. The old screen recorder (`src/stemmanauha`, MuseScore +
  QuickRecorder, macOS) is still there, one radio button away. Both write
  `media/video/<slug> <part>.<ext>`, which is the whole integration: `_media_list`,
  `create_video.find_merged_outputs` and the YouTube titles all read the part out of
  that name, so **review, upload, retitling and delete do not know which renderer
  ran**. `pipeline.run_scroll_video` is the glue (it passes the slug as
  `build_videos(basename=...)` so the names come out right); `server._run_record`
  branches on `renderer` and records which one it used in `record.renderer`.
  `find_merged_outputs` matches `.mp4` as well as `.mov` for the same reason.
  This pull request proposes that the app's **vertical margins start at 0% top and
  5% bottom** rather than 0/0. The renderer's own default stays 0 — there the number
  means "leave the framing alone", and moving it would silently move `scroll_video.py`
  too — so this is a product default, held twice: `server.DEFAULT_TOP_MARGIN_PERCENT`
  / `DEFAULT_BOTTOM_MARGIN_PERCENT` (the record API's fallback and both
  `scroll-preview*` query defaults) and `DEFAULT_TOP_MARGIN` / `DEFAULT_BOTTOM_MARGIN`
  in `static/app.js`, which prefill the panel. The two have to agree or the preview
  frames the music differently from the video. The bottom edge is the one that needs
  the space: the lowest staff's lyrics otherwise sit against the frame. A default only
  fills a setting nobody answered — a song that recorded at 0 keeps 0, and typing 0
  still means 0. `_run_record` now always passes both margins to the renderer instead
  of only when one is non-zero, which was the same call anyway (the renderer's own
  defaults are 0). It also **remembers a chosen margin the way it remembers the BPM**
  — written into `record` when the request arrives, and falling back to what this song
  chose last before the app-wide default. They used to be written only after a render
  succeeded, so framing decided against a render that then failed was gone by the next
  page load. **A successful preview records it too** — nudging a margin and looking at
  the result is how the choice actually gets made, and requiring a render first lost it
  every time. Both paths go through `_remember_margins`, which writes only a real change
  and never while a job is running, since the state file is saved whole. A framing the
  renderer refuses is not recorded: coming back to a margin that cannot be drawn would
  be a trap. Nothing else about the preview writes to the song — no stage moves, no
  video appears.
- The Record panel already has a **silent scroll preview**, so the layout question can be
  answered before the encoding one. Whether the margins are right, whether the staff
  size reads, where the beat marker sits, whether a repeat lands on the right bar —
  all of that used to cost a full render to find out, and video encoding is the wrong
  feedback loop for it. `GET /scroll-preview` prepares the render's own data
  (`pipeline.scroll_preview` → `src.scrollvideo.preview`) off the worker thread and
  hands it to a player in the page (`static/scroll_preview.js`, `.pvviewport` in the
  stylesheet), with play/pause/restart/scrub. No ffmpeg, no audio, and — apart from
  the framing it was asked for, which this pull request proposes recording (see the
  margin note above) — nothing written into the song's state: it is a look at the
  score, not a stage of the work.
  The prepared payload is cached beside the song under a key naming the cleaned
  score's fingerprint **and** every setting that moves the picture (size, both
  margins, the tempo the app supplies), so a score edited in MuseScore or a margin
  nudged is prepared again rather than reused. Preparing costs seconds, which is why
  it is cached at all.
- The player also has **opt-in synchronized audio**. It stays silent and uses its
  wall clock until the user checks Audio, so opening or playing the picture does
  not generate a WAV. The
  visual preview keeps a private copy of `build.prepare`'s exact render source;
  `GET /scroll-preview-audio` validates the picture's revision and selected `ALL`
  or singing-part mix, then calls `audio.render_mix_cached` in the same
  `media/.scrollvideo-audio` cache the final renderer uses. Opening the picture
  still renders no audio. Once enabled, the browser prepares only the selected mix, uses the
  native audio element's `currentTime` as the picture clock, and falls back to wall
  time only for the renderer's short silent tail after the WAV ends. Switching a
  part keeps the current position, ignores stale requests, and swaps between
  pre-painted focus/background highlight tiles without engraving or rasterising
  again. An audio error leaves the picture and controls available.
- This pull request changes **what the preview is made of: the renderer's own
  pixels**, not its SVG. The preview drew the engraving in the browser, and a browser
  is not what draws the video. Verovio writes lyrics, measure numbers and part names
  as `font-family="Times, serif"`; cairosvg resolves that against the host's fonts
  and a browser against its own, so the words in the preview were never the words in
  the video, and nothing about the layout was quite what would be encoded. So the
  server now rasterises with the same call the renderer makes (`build.raster`, split
  out of `build_videos` the way `build.prepare` already was), only at
  `preview.PREVIEW_HEIGHT` rather than 2160, and sends **two strips as PNG tiles**:
  the engraving, and the same engraving with every playable glyph already repainted
  blue (`preview.lit_strip`, `video.lit_pixels` — the renderer's own arithmetic). A
  frame in the page is a canvas holding a window onto the first strip, a translucent
  band, and a copy of each sounding symbol's box out of the second — the three steps
  `video.render` takes, in that order. The browser therefore no longer decides how
  the music is drawn or what colour a lit note goes; it decides nothing. Tiles
  because Chrome refuses an image past 16384px on a side and a score is wider than
  that. The cache is now a folder (`<song>/.scroll-preview/`, payload plus tiles,
  served by name and emptied before a rebuild, since tile names are positional), and
  preparing costs a rasterisation on top of the engraving: ~15s for two minutes of
  music, ~900 KB of PNG. Still seconds against the render's minutes, and it fixes a
  real disagreement as well as a cosmetic one — a bottom margin revealed the spacer
  rest staff in the preview, which the video deliberately crops to white.
- This pull request proposes moving the preview **out of the Record panel and into
  the viewer**, as a `preview` tab beside the score documents, because on a phone the
  panel is a task screen and the preview is a full-screen visual — the same split the
  rest of the mobile Review/Record redesign makes. The Record panel keeps only the
  controls plus a **Preview** button that opens that tab, and the third mobile tab
  reads **Preview** while in Record rather than Score.
  That move is what makes the preview's **lifecycle** a question at all, and the
  answer is that hiding is not destroying. Switching Preview → Record to nudge a
  margin is the normal move, so `_pausePreview` stops the sound and the frames and
  leaves the prepared picture and the prepared WAV mounted; coming back is instant
  and costs no second rasterisation. Destroying is reserved for the case where the
  preview is *wrong*: the panel keeps a signature of every input that moves the
  picture (quality, both margins, the tempo the app supplies) **plus the cleaned
  score's fingerprint**, and when that signature changes it tears the player down and
  says so. Tearing down takes the sound with it — the audio request is aborted, the
  element cleared and the object URL revoked — because a stale mix heard against a
  new picture is the wrong tempo or the wrong crop, and sounds like neither. Late
  responses cannot undo any of that: a picture that arrives after its signature
  stopped matching is dropped, and a WAV that arrives after its player was destroyed
  is revoked rather than attached.
- **Hazards guarded:** re-cleaning warns it discards manual edits (the Clean
  button label changes once a cleaned file exists); lyric import uses `--replace`.
  No automatic LLM (users have no API key) — the lyrics stage supports either a
  copy-paste round-trip (copy prompt → user's own AI + PDF → paste JSON back) or
  direct per-system entry.

There are not yet pytest tests for `song_app`; it was smoke-tested end-to-end
(create → per-system grid → clean → health → lyric import overflow warnings)
against the `laulun_aika.mscx` and `simple_1` fixtures.

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
   renderer refused, 92% of the played notes having no highlight — so such a voice
   becomes a measure rest of the real bar. Lengths count `location`
   gaps, or a voice looks complete on its notes while the file has it occupying more
   of the bar. The fixture's m26 cost two wrong versions before that shape — see its
   STEPS.md.
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
   voice that kept the tie at the same tick span (requires **same pitch**, so it's
   safe). Slurs are **not** auto-mirrored: a slur connects different pitches, so it
   can't be pitch-checked, and mirroring one voice's slur onto another produces false
   positives (e.g. copying a bass melisma onto the tenors) — slurs are fixed by hand in
   the score. Then `detect_part_types` (clef + pitch-range heuristics name parts
   S/A/T/B and set clefs), apply names/clefs, strip brackets/barLineSpan.
7. `--add SSAA` appends new empty staves (rests) with the right clef per letter.

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
prompt offers the recorded answer as a `[default]` (Enter reuses it). Answers are
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
first). Copying the voice whole instead handed both notes to the upper part and left
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

## Lyric placement (`src/clean_score/lyric_txt.py`)

The most intricate module, and the single owner of lyric placement: format
normalization, target routing, chord eligibility, syllable distribution, XML
placement and the diagnostics that fall out of it. It round-trips lyrics between
`.mscx` and a plain text or JSON format, designed so an LLM can fix lyrics against
the original score (e.g. a PDF pasted into the chat) without breaking syllable
alignment. The prompt files `lyric_json_prompt.txt` / `lyrics_txt_prompt.txt` drive
that.

Its interface — all three callers (the CLI file adapters, the AI-JSON paste, the
song app's manual editor) go through these, and nothing else is public:

```python
export_lyrics(root) -> str                      # the TXT projection of the score
place_lyrics(root, source, fmt=, replace=, split=) -> LyricImport
editor_grid(root, systems=) -> EditorGrid       # parts x printed systems, prefilled
slot_counts(root) -> {staff: {measure: n}}      # notes that take a syllable
blocks_from_cells(grid, cells) -> [block]       # those cells as lyric JSON
export_file(...) / import_file(...) -> LyricImport      # the .txt/.json adapters
```

`source` is TXT, JSON text, or already-parsed JSON blocks (`fmt` overrides the
sniff). **Diagnostics are returned, never printed**: `LyricImport.mismatches` is a
list of `Mismatch(kind, message, measure_start, measure_end, staff_ids, syllables,
slots)` — kinds `too_many` / `too_few` / `no_systems` / `no_system_for_line` /
`block_count` — plus `filled_measure_starts` for nulls inferred from the printed
systems. `to_dict()` puts them on the wire for the browser; the CLI wrapper prints
`Warning: <message>` itself, so terminal output is unchanged.

- **Eligibility**: only voice 0, verse 1. A note gets a syllable token unless it
  is a slur/tie *continuation* (not the first note of the slur/tie) — those get
  no token. Rests get no token.
- **TXT format**: `# Measure N` headers, then `staffId [syllableCount]: tok1 tok2 ...`
  Tokens are space-separated; hyphens join syllables of a word (`il-man`);
  trailing hyphen = the word continues, and that holds between **any** two tokens,
  not only across a barline; a **leading** hyphen says the same thing looking
  backwards, so a line carried over from the previous system may be written
  `-hil-le`. `_` = eligible note with no lyric. Syllabic state
  (begin/middle/end/single) is reconstructed from hyphenation on import: a syllable
  is `middle` when a word runs both into and out of it, and reading that as `end`
  splits one word in two. It did — `lai-ne-hil-le` was stored as `lai-ne-hil` plus a
  stray `le`, because the continuation rule was applied only to the first token of a
  measure. The syllables still land on the right notes, so no health check, test or
  mismatch count noticed; what showed it was the by-system editor displaying
  `hil le` where the imported JSON said `hil-le`. See `test_lyric_hyphenation.py`.
  (Alignment is per-measure — the token counter resets at each
  barline — so a missing slur/tie only misaligns within its own measure, not the rest
  of the line.)
- **JSON format**: line-by-line; tokens are *distributed across measures* using
  actual chord counts from the score (`_get_chord_counts_per_measure`). The
  PDF-derived format has a `lyrics` array of `{text, staff_number, position,
  verse, parts}`. `staff_number` is the printed staff (top=1); `position` is
  `above`/`below`. These are mapped to **output staff ids** via the
  `lyricsStaffMap` metaTag that `clean_score` writes (`_read_lyrics_staff_map`):
  a printed staff that split into two voices gets the line on both voices when
  only one position appears *in that block* (unison), or split upper/lower when
  both positions appear (divisi is decided **per block**, not globally). An
  explicit `parts` on a lyric overrides the staff_number/position mapping (manual fix
  for the ~inevitable LLM errors). `parts` accepts output staff **ids** *and/or part
  **names*** (`["T1","T2"]`, also a scalar `part`); names resolve via the score's
  trackNames (`_read_part_name_map`). Names are the robust override — immune to
  printed-staff order (e.g. an ossia T3 printed on top), which staff_number cannot
  handle. The current `lyric_json_prompt.txt` has the LLM emit `"parts": []` (empty)
  in **every** lyric so manual overriding is just dropping ids/names into the existing
  array; empty → auto-map by staff_number/position. (An empty list is falsy, so the
  `if parts:` check falls through to the staff_number path — same as omitting it.) Legacy numeric/`_DEFAULT_PART_TO_STAFF` part keys still work. `--split`
  duplicates a part into two staves. When a lyric has no `parts`, import falls back to
  staff_number/position: for `--per-system` scores the printed numbering shifts per
  system, so it uses the per-system `lyricsSystemMap` (`_read_lyrics_system_map`) for the
  block's `measure_start`, else the single `lyricsStaffMap`. Resolution priority per
  lyric: explicit `parts` (ids/names) → staff_number+position via system/staff map.
  A null `measure_start` (the LLM emits null when no measure number is printed at the
  start of a line) is auto-filled by `_fill_missing_measure_starts` (reported as `filled_measure_starts`): blocks are one
  per printed system in order, so each null block takes the start measure of the
  system at its position (`per_system.system_ranges`); explicit values are left alone, and a
  block-count vs system-count mismatch is warned (so the user verifies alignment).
- Import is in-place on the tree, removes verse 2+, and clears lyrics from
  ineligible (spanner-continuation) chords. `--replace` / `clear_existing=True`
  wipes all verse-1 lyrics first (needed because MusicXML imports arrive with
  garbled OCR lyrics); without it, only measures/staves named in the input are
  touched (partial edit).

When editing this file, the export and import paths must stay symmetric — the
test `test_lyric_txt_spanner.py` asserts export→import round-trips back to the
original XML. The `il-man il-ki-rii-vi-` case (where a word's syllables span a
measure boundary) is covered by the two `measure_14` regression tests; the
syllable distribution in `json_lines_to_by_measure` must keep them green.

## Scrolling practice video (`src/scrollvideo/`)

An alternative to the screen-recording pipeline: the video is **rendered from the
score**, so nothing appears on screen, nothing depends on window focus or global
shortcuts, and it can run unattended. One video per voice: the score scrolls
horizontally as a single continuous system, every sounding note lights up, and the
voice the track is for is highlighted strongly while the others stay faint.

Interface — everything else is an implementation detail of these calls. This pull
request proposes replacing the `spacer_per_quarter` grid shown here with
`spacing_ratio`, the biggest step in width-per-beat allowed between neighbouring
bars:

```python
build_videos(mscx_path, out_dir, parts=None, height=2160, width=3840,
             fps=60, with_audio=True, keep_silent=False, emphasise=False,
             combined=True, spacing_ratio=1.3, smooth_seconds=2.0,
             basename=None, log=...) -> [video paths]
preview(mscx_path, out_dir, width=3840, height=2160, fps=60, ...) -> payload
```

**The picture is decided once, and there are two things that can be done with it.**
Everything before rasterisation lives in `build.prepare(mscx_path, tmp, ...) ->
Prepared`: the prepared score, the MusicXML and MIDI, the verovio engraving with its
timemap and drawn-id map, `TempoMap.from_midi`, the note and rest events,
`scroll_anchors` + `smooth_scroll`, the spacer-staff crop, the margin viewport, the
duration — and the refusals, which is the part worth naming. A D.C./D.S. jump,
margins that leave no picture and a timeline that misses more than 2% of the played
notes all fail in `prepare`, so **the preview refuses exactly what the render
refuses**, seconds in rather than minutes.

**And this pull request does the same for the pixels.** `build.raster(ready, height)
-> Raster` draws a `Prepared` — the strip, the glyph coverage, and each symbol's
pixel box, with the spacer crop and the margins already applied — at whatever height
is asked for. `build_videos` asks for 2160; `preview` asks for
`preview.PREVIEW_HEIGHT`. One call, so the preview cannot be a second drawing of the
same score, which is what it was while the browser was given SVG: verovio writes
words as `font-family="Times, serif"` and cairosvg and a browser do not pick the same
serif.

`preview.py` writes that `Raster` into `out_dir` as two sets of PNG tiles — the
engraving, and `lit_strip`: the same engraving with every playable glyph already
repainted blue through its coverage by `video.lit_pixels`, transparent everywhere
else — and returns the numbers to play them with: the frame size, where each tile
starts, the duration, the scroll curve as `(times, xs)` **in strip pixels**, and one
entry per symbol that lights up (on, off, staff, its box, and the beat marker's
band). The browser interpolates the curve, copies the window out of the first strip,
blends the marker band, and copies each sounding box out of the second. It works
nothing out about music, layout, time or colour, so it cannot disagree with the
render. Tiles because cairo caps surface dimensions and so do browsers — Chrome
refuses an image past 16384px on a side, which a score reaches. The curve also
carries `jump`, the backward step past which the player must land rather than
interpolate, so a repeat snaps back to the repeated bar instead of sliding across
the music in between.

`combined` also writes **"&lt;base&gt; ALL"** — the same picture with every voice at equal
volume (`render_mix(focus=None)`, muxed like any other track). It is a mix, not a part,
so it never goes through `part_names`; downstream reads the part out of the file name,
which is why it is called ALL, the name the screen recorder always used.

Three MuseScore CLI calls feed it, and each output answers one question:

| output | question | who answers |
| --- | --- | --- |
| MusicXML | what does the page look like, and which note is where? | verovio |
| MIDI | *when* does each note sound? | MuseScore's tempo map |
| WAV per voice | what do we hear? | MuseScore's synth |

**The clock is the whole design.** Verovio's timemap and MuseScore's audio are
*different musics*: verovio ignores playback properties, above all the
`timeStretch=3` that `clean_score` puts on fermatas. On the Hanget soi score
verovio says 48.0s where MuseScore renders 59.6s — a video on verovio's clock
drifts up to 14s. But MuseScore writes that stretch into its MIDI export as tempo
changes (120 -> 40 bpm for a fermata), so: **musical position (qstamp) from
verovio, seconds from the MIDI tempo map** (`timing.TempoMap`). Verified against
the MIDI's own note-ons — every highlight lands within 20ms of a note MuseScore
actually plays. This is what `tests/test_sync.py` pins; don't "simplify" it back
to `entry["tstamp"]`.

- `spacing.py` stops the scroll **lurching**, and this pull request changes what it
  does about it. Verovio spaces a measure by what is in it, so a bar of 32nds comes
  out five times wider *per beat* than an equally long bar of quarters — and since
  the scroll follows the notes, the video surges through the sparse bar and crawls
  through the busy one. The fix used to be a staff of evenly spaced rests at one
  subdivision over the whole song (the trick `add_rest_track.qml` already used in
  MuseScore), which works but charges every bar in the song for the worst bar in it.
  Now the rest count is chosen **per bar**, and a song that already scrolls evenly
  gets no rest staff at all.
  What is capped is **width per quarter note**, not raw width, so a 3/4 bar next to
  a 4/4 one is not mistaken for a lurch. The narrowest widths-per-beat that keep
  every neighbouring step inside `DEFAULT_MAX_RATIO` (1.3, `--spacing-ratio`) have a
  closed form — `x_i = max_j natural_j / cap**|i - j|` — so a dense bar widens the
  bars around it and dies away geometrically, rather than lifting the whole song.
  Reaching a target is **measured, not predicted**: verovio spaces each separate
  moment in a bar, so rests laid where the music already sounds change nothing, and
  past that what one more is worth falls away as the bar fills (about a fifth as
  much in a bar of 32nds as in a bar of quarters). `even_engraving` engraves, reads
  the bars back off the SVG's staff lines, works out from that engraving what a rest
  was worth in each bar, and solves again — three or four engravings for a score
  that needs widening, one for a score that does not.
  Two things were got wrong on the way here. The rests must be written as **real
  note values** (`slot_durations` splits a bar into as few as it takes, then halves
  the longest until there are enough): verovio reads what a rest is *written* as and
  not its `<duration>`, so a rest of "one fifth of a bar" is taken for a whole rest
  and quietly drags the part out of time with the audio, with nothing wrong in the
  picture to show it. And the targets are settled **before** any rest is written and
  never moved again — re-solving the cap against the widths a plan produced looks
  like the way to tidy away the last few percent of rounding, and instead it walks
  outwards bar by bar and inflates the whole score by a third and rising. That
  leftover stays, so an engraved step can sit a few percent past the cap.
  A bar's length is read by following the MusicXML cursor (`note`/`forward` advance
  it, `backup` winds it back), not by adding up every note: a two-voice bar is
  written as one voice after the other and summing reports it as twice as long, so
  every target computed for it would be half what it should be. And everything about
  the staff that can be told not to print is (`print-object="no"` on its name, clef
  and time signature) — it is cropped off the bottom of the strip, but those are
  drawn in the left margin where the crop cannot reach them.
  The staff is injected into the **MusicXML** (after MuseScore has produced it, so
  it never reaches the MIDI or the audio) and cropped back off the bottom of the
  strip by `visible_height` — `build_videos` rasterises proportionally taller so the
  singing staves still fill the frame. The crop margin is deliberately tiny: the
  last staff's lyrics sit in the gap above the spacer, and a generous margin clips
  them. Verovio's own spacing options cannot do this job — `spacingNonLinear: 1.0`
  gets the spread to 1.04x but makes the page 7x wider, leaving less than one bar on
  screen.
- `score.py` is the only edit made to the score before engraving: parts with nothing
  to sing (percussion, or a staff of only rests — the click track
  `add_rest_track.qml` adds) are dropped, along with the staves they own. They would
  otherwise cost a staff of height in every frame and each get their own pointless
  practice video. The original file is never touched; with nothing to drop it is used
  as-is. `build_videos(..., keep_silent=True)` / `--keep-silent` turns it off.
- `engrave.py` renders with `breaks: "none"` so the whole score is one system
  (one page — a second page is an error, not something to stitch). Notes are
  `<g id=... class="note">` and the timemap's `on`/`off` lists name those same ids;
  that pairing is what makes highlighting possible at all.
  This pull request adds one more thing it owns: **a music symbol written inside a
  piece of text is drawn here, not left to a font.** The quarter note in "♩ = 80" is
  the case that shows up — verovio writes it as a character of its own music font
  and offers that font only as a base64 `@font-face` in the SVG's stylesheet, which
  cairosvg ignores, so the note reached the video as the empty box a font draws for
  a character it has not got (#58). It affected the tempo the app itself adds to a
  score with none (#28) as well as printed ones. `draw_symbol_text` replaces the
  character with the outline verovio ships beside the font — the same outlines every
  symbol it *does* draw is made of — and moves the writing after it along by the
  advance width in `Leipzig.xml`. Only a symbol that **opens** its text is drawn:
  after writing, placing it would mean measuring that writing in whatever font the
  renderer picked, and a symbol in the wrong place is worse than a box. A page with
  no such symbol is returned untouched, so renders that never had the problem stay
  byte-identical.
- `geometry.py` owns two verovio-SVG facts. Coordinates live in the **nested**
  `<svg class="definition-scale" viewBox=...>`, not the root (whose px size is 1/25
  of it), and a note's position is its notehead `<use transform="translate(x, y)">`
  **plus every ancestor `<g transform>`** (verovio emits a page margin; dropping it
  puts every highlight a staff too high). `rasterise` renders the strip in tiles
  because cairo caps surface dimensions at 32767px and a 3-minute score is wider;
  tile edges are cut on the **output pixel grid**, not by converting a fixed unit
  width, so seams don't accumulate rounding drift.
  This pull request proposes **giving each tile only the measures it shows**. A tile
  used to be the entire engraving with the viewBox moved, and cairo then clips — but
  cairosvg has already walked and drawn every node in the document in Python, so the
  clip saves nothing. Measured on Kaksi laulua krapulasta 2 (86 bars, 4 minutes,
  a 1.8 MB SVG of 27 000 nodes): a 1765px-wide tile cost the same 13s as an 8000px
  one. Cost was nodes x tiles, not nodes x picture, and the 4K render pays it nine
  times over. `_Croppable` parses the engraving once and hands each tile the
  measures inside its window; `measure_spans` works out how far a measure's ink
  actually reaches from the shapes themselves — glyph widths taken from `<defs>`,
  curve control points, and the widest a piece of writing could be at its font size
  — so a slur drawn in one bar and carrying on over the next still comes with the
  tile it reaches. **Anything whose reach cannot be worked out counts as drawing
  across the whole page** and is never left out: a tile that took too long is a far
  smaller bug than a notehead rubbed off the score. The picture is byte-identical,
  which is the property under test rather than a hope — `test_geometry` asserts the
  cropped strip and coverage equal the uncropped ones pixel for pixel. Measured on
  that song: `build.raster` for the preview 58s -> 11s (whole preview preparation
  113s -> 27s), and at the video's 2160 rows, 107s -> 28s.
- `timing.smooth_scroll` finishes the job the spacer starts: it averages the scroll
  **speed** over a couple of seconds and integrates it back into positions. Averaging
  positions directly would flatten the curve at both ends — a perfectly even scroll
  would ramp up at the start and down at the finish. Repeats stay sharp: a jump back
  is real motion, and jumps are found in the anchors *before* resampling smears them
  across frames, then each stretch is smoothed on its own. Scrolling at a dead
  constant speed is not an option: measured, it puts the sung note up to 0.45 screens
  from where it belongs, where smoothing keeps it inside 0.02.
  Measured end to end (spacer + 2s smoothing): Käyttäytymisohjeita's speed
  coefficient of variation goes 0.31 -> 0.11 and Venematka's 0.43 -> 0.16, costing
  about half a bar of on-screen music.
- **The sounding note is repainted, not covered.** `geometry.note_coverage` renders
  a second pass with the **noteheads** in a marker colour nothing else uses (pure red
  on a black-and-white engraving) and reads coverage back as red-minus-green: full
  on the head, zero on staff lines and on the lyric that shares the note's group, and
  correctly *partial* on antialiased edges. `video.render` draws those pixels as
  blue-on-white weighted by coverage, so the head itself goes MuseScore blue with a
  smooth outline instead of sitting under a coloured box.
  **Heads only** — stems, flags and beams stay black. That is a deliberate choice
  (colouring stems drags the eye up and down as they flip direction, and a beam is
  shared between notes so it would flicker), and it keeps `NoteGeom.box()` down to
  the head, which is less to composite per frame.
  One gotcha is load-bearing: verovio ships a stylesheet
  (`#id ellipse, #id path, ... {stroke:currentColor}`) that outranks presentation
  attributes on the shapes it names, so marking uses an **inline style**.
  This pull request proposes taking that gotcha one level further down. A notehead
  is a `<use>` of a glyph kept in `<defs>`, so the shapes actually painted are paths
  the rule names by id — and a rule on the path beats a style inherited from the
  `<use>` above it. Marking therefore left the glyph outlined in **black** around its
  red fill, and red-minus-green read that outline as almost no coverage: the whole
  antialiased edge came back at well under half its true value (74 measured as 32 on
  one edge pixel, 157 as 111 on another), the edge was repainted nearly white instead
  of part-blue, and a lit notehead had a staircase for a border — thin open heads,
  half and whole notes, worst of all (#63). Adding `color` to the marker style
  resolves that `currentColor` to the marker too, and coverage then equals the
  engraving's own ink exactly. `test_geometry` pins that: wherever nothing else on
  the page is drawn, coverage must equal the strip's ink, partial pixels included.
  What is left at the border after that is the **codec**, not the picture. h264 4:2:0
  stores colour at half resolution each way, and a notehead is only ~50px across in a
  4K frame, so a thin blue ring picks up blocky colour fringes while the black stem
  beside it stays crisp — luma is full resolution. Measured on one frame against the
  composite it was made from, mean error over the notehead: 4:2:0 1.48, 4:4:4 1.74 at
  x264's own bitrate, i.e. subsampling and not quantisation is the limit
  (`chroma-qp-offset=-6` bought 12%, `-12` no more). 4:4:4 is the only real cure and
  it is not shippable — these are watched on a phone and sent to YouTube, and neither
  decodes High 4:4:4 Predictive.
- `video.py` composites: the engraving is rasterised **once** and a frame is a crop
  of that strip plus the recolouring above. Nothing is
  re-engraved per frame — a four-voice minute of music costs about a minute of CPU
  for all four videos. Scroll position is interpolated from the notes' own x
  positions (`timing.scroll_anchors`), so a fermata's held note simply sits still.
- `audio.py` replaces `export.qml` + AppleScript: a per-voice mix is MIDI controller
  7 on each Part's `<Channel>` in the .mscx, so it is a copy-the-score-and-set-
  volumes edit, then one headless CLI render (~6s per voice). Every CLI call is
  bounded by `CLI_TIMEOUT` — a wedged MuseScore process otherwise hangs the render
  (and the test suite) forever.

Because video and audio come off the same clock, there is **no `audio_delay_ms`**
here — that offset exists in `stemmanauha` only because a screen recording and an
mp3 are captured independently.

**It is deterministic**, and that is deliberate: `engrave.XML_ID_SEED` pins verovio's
element ids (otherwise random per run, which changes nothing visible but makes
renders impossible to diff). Verified by rendering twice: SVG, rasterised strip,
MuseScore's MusicXML/MIDI/WAV and the final mp4 all come out byte-identical. The one
caveat is MuseScore's MusicXML export stamping `<encoding-date>` with today's date,
so a re-render on a later day differs in that file (not in the video).

**The picture is identical for every voice, so it is encoded once.** At 1080p30,
compositing the frames was 3.2s of a 21.5s render and x264 the other 18.3s, so
rendering per voice paid for the encode four times over to change nothing but which
highlights were brighter. Instead: one encode, then each practice track is a
`-c:v copy` mux of that video with its own mix, and the audio mixes run concurrently
(four MuseScore processes: 11.8s together instead of 43s).
`emphasise=True` / `--emphasise` restores per-voice highlighting (that voice bright,
the others faint) and with it a full encode per voice — it is the slow path, and
`test_video.test_mux_puts_audio_on_a_video_without_touching_the_picture` pins that
the default one does not re-encode.

**Output is 3840x2160 @ 60fps** (`--width/--height/--fps`). 60fps because the whole
frame pans horizontally, which is exactly what judders at 30. Measured on
Käyttäytymisohjeita (78 bars, 1451 notes, 2:32, 4 voices): **228s** for all four,
peak RSS 2.9GB, 25MB per video.

     3s  setup      MusicXML + MIDI + engrave
    14s  rasterise  59518x2160 strip = 386 MB
    12s  audio      4 mixes, concurrent
   170s  video      ONCE
    ~5s  mux        4x, stream copy

At 4K the encoder is no longer the bottleneck — moving 25MB frames is. x264 presets
barely change the time (medium 16.7s vs veryfast 13.8s for a 15s probe) but change
the size a lot (24MB vs 47MB), so `DEFAULT_PRESET` stays `medium`. For the same
reason `video.render` fills **one reused frame buffer** rather than copying a blank
per frame, and `geometry.rasterise` fills a single strip buffer instead of
`hstack`ing tiles — at 4K each of those would otherwise double hundreds of MB of
traffic or footprint. Strip memory is ~2.5MB per second of music at 2160p, so a
6-minute score needs roughly 900MB; that is the number to watch if a very long score
ever fails.

Three behaviours worth knowing:

- **Tie continuations get their own highlight.** Verovio's timemap emits an `on` for
  the second note of a tie; MuseScore's MIDI (correctly) does not retrigger. The
  highlight moves to the tied notehead while the sound continues — which is what a
  singer follows, and matches MuseScore's own playback cursor.
- **Section repeats and voltas work, and the scroll jumps back.** Verovio *expands*
  repeats in its timemap exactly as MuseScore does, so the played timeline is already
  unrolled and the qstamp axis still matches MIDI ticks. The section is engraved once,
  so the repeat pass sounds under suffixed ids (`xyz-rend2`) that are not drawn;
  `engrave._drawn_ids` maps them back with verovio's `getNotatedIdForElement`. A
  repeated note therefore gets one highlight event per pass, and the scroll walks
  back to where that section is drawn. **D.C./D.S. jumps are still refused** —
  verovio does not follow them (on Jouluriemua it plays 181 quarters where MuseScore
  plays 257.5), so `build.unsupported_repeats` looks for `Jump` only (a `Marker` — segno, coda,
  fine — is just a label and changes nothing on its own).
- **Every render is verified against the audio before it ships.** `build.alignment`
  checks what fraction of highlights land within 200ms of a note MuseScore actually
  strikes, and refuses below 98%. That is the real property, so it catches timeline
  disagreements we did not think to look for — don't replace it with a structural
  check on the markup.

Tests (`src/scrollvideo/tests/`) — `test_sync.py` needs the MuseScore CLI and skips
without it, like the browser tests:

- `test_sync.py` — the clock, end to end on a fixture with a 3x fermata: every
  highlight lands within 20ms of a MIDI note-on, the last note ends with the audio,
  and a guard test spelling out what verovio's own clock *would* have shipped.
- `test_symbol_text.py` — a tempo mark's note is drawn rather than left to a font, it
  is ink on the page above the music (measured against the same page with the drawing
  taken out again, so the "= 80" beside it cannot satisfy the reading), the writing
  after it moves along by the font's advance, and a symbol that follows writing is
  deliberately left alone.
- `test_preview.py` — the preview against the render it is a preview of: the same
  scroll curve number for number, a fermata timed on MuseScore's clock rather than
  verovio's, the symbols `place` would light, a repeat that stays one backward jump,
  the same refusals, and that nothing is encoded and no voice is mixed. This pull
  request adds the tests that only became possible once the preview carried pixels:
  the strip is byte-identical to `build.raster`'s, a lit glyph is
  `video.lit_pixels`'s own blue, a bottom margin shows white rather than the spacer
  staff — and, the one that pins the lot, a frame composed out of the payload the way
  `scroll_preview.js` composes it, against real frames from `video.render` written as
  raw pixels so the comparison is not arguing with a codec.
- `test_geometry.py` — the ancestor-translate offset, the definition-scale viewBox,
  and that tiled rasterisation matches single-shot (alignment pinned; antialiasing
  along a seam is allowed to differ by a pixel). This pull request adds the
  cropping's own rules: cropped tiles equal uncropped ones pixel for pixel, a tile
  really is handed less of the score, a slur running past its own barline still
  comes with the tile it reaches, and a shape we cannot measure is never left out.
- `test_timing.py` — TempoMap arithmetic (a 3x fermata window), notes still sounding
  at the end being closed rather than dropped.
- `test_video.py` — highlights land on the right staff, and are present while a note
  sounds and gone after it stops (decoded back out of the rendered mp4).
- `test_geometry.py` also pins the highlight: the note box is the head and stops short
  of the stem, marking paints the head via an inline style but leaves the stem and the
  lyric alone, and coverage marks strictly less than all the ink. `test_video.py` pins that colour lands only where
  the glyph is — a leak outside it means someone reintroduced the box.
- `src/song_app/tests/test_record_renderers.py` — the Record stage routes to the
  scrolling renderer by default, passes the size choice through, still reaches the
  screen recorder on request, refuses to render without a cleaned score, names the
  files so review and upload find them, and cannot be killed by progress reporting.
- `src/song_app/tests/test_record_panel_ui.py` — the same choice in a real browser:
  both renderers offered, scrolling preselected, controls swap, and the run button
  actually posts `renderer` (browser-marked, skips without Playwright).
- `src/song_app/tests/test_scroll_preview.py` / `test_scroll_preview_ui.py` — the
  endpoint's caching and its invalidation (score edited, or any setting that moves
  the picture), that previewing changes nothing about the song, and a refusal
  arriving as a message; then the player itself in a browser — play, pause, seek,
  restart, the repeat landing rather than sliding, the sounding glyph turning blue,
  the beat marker following it, a phone-sized layout, and a rebuilt preview after the
  score changes. This pull request adds the folder cache's own rules (a rebuild
  empties the old tiles; a tile name off the wire cannot reach out of the folder),
  and re-grounds the browser tests: **the stub strip encodes each column's own x
  position in its pixels** (`(x >> 8, x & 255, 0)`), so with no viewBox left to read,
  one pixel off the canvas says exactly where the player has scrolled to and every
  assertion is made by looking at what is on screen. The drawing is stubbed in both,
  so neither needs MuseScore.
- `test_spacing.py` — this pull request rewrites it around the adaptive rule: a
  score of ordinary bars is engraved at its natural width with no rest staff, the
  reported 4-note/32-note pair comes back inside the cap, one dense bar among
  sixteen sparse ones widens its neighbours and leaves the far end untouched, and a
  2/4 bar among 4/4 bars is not widened for being short. Plus the mechanism's own
  rules: more rests widen a bar and never narrow it, a bar does not budge until it
  is asked for more moments than its music already has, the rests keep the music in
  time (the written-value bug, caught by comparing the timemap against the unspaced
  score), a two-voice bar is not read as twice as long, a bar of a length no rest
  spells gets a whole-measure rest rather than an approximation, a count that
  divides the bar unevenly is still written, and where the crop falls.
- `test_timing.py` also pins the smoothing: an already-even scroll is left exactly
  even (the edge-ramp bug), uneven spacing is evened out, and a repeat stays one
  clean jump rather than three smeared ones.
- `test_score.py` — which parts count as silent, that dropping one takes its staff
  with it, and that the original file is never modified.
- `test_audio.py` / `test_build.py` — the volume edit (replaced, not duplicated;
  pan/program untouched), the D.C.-jump refusal, that section repeats/voltas are
  *not* refused, and the alignment measure itself (full when highlights match the
  MIDI, falling when they drift).
- `tests/test_files/fermata.mscx` — `simple_1_output` with a `timeStretch=3` fermata
  added to measure 1 (4.00s -> 5.00s of MIDI). `fermata.musicxml` is the same score
  pre-converted so engraving tests need no MuseScore.

## Reading a scanned score (playbook)

Hard-won in the session that built `pdf_systems`, where reading whole rendered pages
produced a confident, tidy and substantially wrong conclusion. If you are an agent
about to read a score off a PDF, start here.

**Crop before concluding anything.** A whole A4 rendered small enough to look at
cannot show a slur or a notehead. Use `pdf_systems.crop_systems` (400 dpi, one band,
~0.9s) and read a system at a time. `page_images(grid=True)` overlays a labelled
percentage scale for reading system boundaries off — never estimate them by eye, that
is how a crop ends up clipping the lyric line under the bottom staff.

**Let the arithmetic check the reading.** `lyric_txt.slot_counts(root)` gives
`[staff][measure] = notes that take a syllable`. Every correct correction predicts
its slot count *before* being encoded and lands on it exactly; a reading that needs
the numbers bent to fit is wrong. `place_lyrics` returns the same numbers as
`Mismatch` records.

**The direction of a mismatch says what kind of problem it is:**

| | means |
|---|---|
| `too_many` | the **reading** is wrong — too many syllables for the notes |
| `too_few`, by a lot | usually a voice **sharing** another staff's words (below) |
| `too_few`, by exactly one or two | usually a **dropped slur** — a melisma the OCR lost |
| all `too_few`, never `too_many` | do **not** conclude "missing slurs" from this alone; that inference was made once and was mostly wrong |

**Voices sing words that are not printed under them.** Older choral engraving prints
a text once and expects more than one voice to use it, so notes with no text beneath
are the norm, not an anomaly:

- Text set *between* the staves usually serves both.
- A voice's own line can start part-way through a system; before that it sings the
  other staff's words (bass sharing the tenor line for two measures, then breaking
  away at its own entry).
- A voice whose per-measure note counts **match another voice's exactly** is very
  likely singing that voice's words — in the fixture the upper bass doubles the tenor
  rhythm throughout.
- A measure where *every* voice has the same note count and one text is printed is a
  unison convergence: all of them sing it.

**Check continuity across system breaks.** Each voice's text must join into a
sentence from one system to the next. When it is ambiguous which staff a line between
two staves belongs to, this decides it — only one assignment leaves every voice with
a sentence.

Worked through twice, with the wrong turn left in, in
`fixtures/virta-venhetta-vie/STEPS.md`.

## MuseScore plugins (`plugins/`)

QML for MuseScore 3.x. **Install by copying/symlinking into
`~/Documents/MuseScore3/Plugins`** (MuseScore loads them from there, not from
this repo). After changing a plugin, reload it in MuseScore (Plugins → Plugin
Manager, or restart). They cannot be unit-tested from Python.

- `export.qml` — export per-voice mp3s, each choir voice mixed louder than the
  rest; supports SSAA/SATB/TTBB/SAM naming. Triggered by the recording script.
- `voice2.qml` — split a selection into two voices (lowest note → voice 2).
- `copylyrics.qml` — copy topmost-staff lyrics down to lower staves by tick.
- `replacelyrics.qml` — search/replace across hyphenated lyric syllables.
- `add_rest_track.qml` — add a spacer staff of 16th rests (even measure spacing).
- `mute.qml` — toggle mute / set volume on all instruments.
- `lyric_export_import.qml` — in-app lyric TSV transfer with highlighting.

## Recording pipeline (`src/stemmanauha/`, macOS only)

`record_stemmanauha.py MySong` → `create_video.run()`:
mp3 export (AppleScript drives MuseScore's `export.qml`) → record play-along
video via QuickRecorder (AppleScript + OBS websocket) → `ffmpeg` merges each
voice mp3 onto the video (audio sync offset `audio_delay_ms`, default 1300ms) →
optional YouTube upload.

`run()` takes granular controls (used by the web app, see above): `audio_delay_ms`
(the merge sync offset), `redo_mp3` / `redo_video` (selectively clear and redo a
stage — each step otherwise skips if its output already exists), and `merge_only`
(re-merge existing media with a new offset, no recording — the fast fix when the
sync is just off). `merge_mp3_to_video(..., force=True)` overwrites existing
merged outputs, and it identifies the **raw** recording as the `.mov` whose name
is not one of the `"<song> <part>.mov"` merge outputs (so re-merging never feeds
its own output back in). Before the per-voice merges it downscales the recording
**once** in place to `MAX_VIDEO_HEIGHT` (env, default 1080; 0 disables) via
`_cap_video_height` — Retina screen recordings are 1440p+, and YouTube would serve
that; capping keeps the merges `-c:v copy` (one re-encode, not one per voice).
Already-merged songs need a **re-merge** (force) to regenerate at 1080p.

This is heavily environment-dependent: it relies on specific macOS apps, global
keyboard shortcuts wired in QuickRecorder/MuseScore, `MUSESCORE_EXPORT_PATH`,
`VIDEO_EXPORT_PATH`, and `ffmpeg`/`ffprobe`. It is not portable or testable in
CI. The `.scpt` AppleScript files and the keyboard shortcuts described in
`README.md`/`record_stemmanauha.py --help` must match. The CLI still skips a
stage when its output exists; the web app exposes the redo flags instead.

## This host: the live app, the deploy, the board

The app is not something someone starts when they want it. It runs as a systemd **user**
service from this very checkout, `song-app.service` on 127.0.0.1:8123. The units are
committed under `systemd/` — the live copies live in `~/.config/systemd/user/`, so an
edit here is not live until it is copied over and `systemctl --user daemon-reload` has
run.

The phone reaches it over Tailscale at **https://choir.taile8d16e.ts.net/**, a Tailscale
*service* (`svc:choir`) whose 443 handler proxies to `http://127.0.0.1:8123`. The older
`https://bazzite.taile8d16e.ts.net:8123` still works and points at the same app. That
config lives in tailscaled, not in this repo:

```bash
tailscale serve --service=svc:choir --https=443 --set-path=/ http://127.0.0.1:8123
```

Port 8000 is **not** available on this host — a podman container (`sos`, the Outdoor dev
server) has it, and `song.py` silently falls back to the next free port rather than
failing, so an app "started on 8000" ends up somewhere like 8002 with nothing pointing at
it. The two addresses are different **origins**, so an installed PWA does not follow a
move from one to the other; it has to be installed again (see the PWA identity notes).

A merge deploys itself. `song-app-deploy.timer` runs `scripts/deploy-song-app.sh
--unattended` every two minutes: it fast-forwards `main` to `origin/main`, installs
requirements, restarts the app and waits for `/healthz` to say `ok`. **It refuses more
often than it acts** — a dirty checkout, another branch, or local commits that are not
on `origin/main` all mean someone is working here, and deploying would either destroy
that work or ship a commit GitHub has never seen. Nothing to deploy is a no-op with no
restart, deliberately: a restart is the evidence a release waits for, so a spurious one
would tell the board a merge had reached the host when it had not.

"Dirty" has to mean *a person edited the checkout*, and one thing that is not that
used to trip it: the poller checks each issue out into `.worktrees/issue-N` **inside
this repo**, so `git status --porcelain` listed an untracked `.worktrees/` and the
deploy refused for as long as any agent was working. It refused silently as far as the
board was concerned — the timer failed every two minutes, the app kept serving old
code, and the card that had just merged was told its production deploy had failed. This
change gitignores `.worktrees/`. A worktree is a separate working directory, so a
fast-forward of `main` cannot disturb one; leaving it visible bought nothing and cost
every deploy made while a card was open.

`/healthz` exists for that watcher. It returns `{"status": "ok"}` and touches nothing —
it has to answer while a clean or a video render is occupying the worker threads.

**Song chats need three keys in `.env`, and for a month they were not there.** The
AgentDeck mapping added in #48 reads `AGENTDECK_URL`, `AGENTDECK_API_URL` and
`AGENTDECK_ACCOUNT_KEY`, and they were only ever written to `.env.default` — blank.
`.env` exists on this host, so `.env.default` is never loaded, and every song answered
`unconfigured` (#52). They are now set: browser URL
`https://bazzite.taile8d16e.ts.net` (the tailnet origin AgentDeck itself is served
from, so the phone can open it), API URL `http://127.0.0.1:8756`, account
`claude_code:main`. `.env` is gitignored, so this is host state, not something a
checkout carries — a second host has to be told again. One trap when testing by hand:
an AgentDeck-owned agent chat already exports `AGENTDECK_URL` as the loopback base for
its own API calls, and `load_dotenv` does not override a variable that is already set,
so `./song.py` run from such a chat persists the loopback origin into the song's
`.song.json`. The service has no such variable; clear them (`env -u AGENTDECK_URL`)
before reading anything into a conclusion.

Issues are worked from a GitHub Project board by the AgentDeck poller (instance
`musescore`, unit `agentdeck-poller@musescore.timer`). Its manifest is **not** in this
repo: it lives in `~/agentdeck/poller/manifests/musescore/`, with every project's
alongside it, and the host half (paths, account, token) is the uncommitted overlay in
`~/.local/share/agentdeck/poller/instances/musescore/`. Comment `/claude <what you want>`
on an issue to start or steer work; `/merge` from **In review** releases it. Nothing
merges without that comment.

## Conventions & gotchas

- Everything operates on **uncompressed `.mscx`** XML. `.mscz` is just a zip;
  `clean_score.py` unzips it, and MusicXML is converted via the MuseScore CLI
  (`MUSESCORE_CLI_PATH`).
- Durations: `lyric_txt.py` reads `<Division>` from the score for real ticks;
  `utils/utils.py` uses a fixed `RESOLUTION=128`. Keep them straight — they are
  different tick bases.
- Use `lxml.etree` everywhere (not `xml.etree`); code relies on `getparent()`,
  XPath like `.//Spanner[@type='Slur']`, and `pretty_print`.
- `songs/`, `backup/`, `playlists.txt`, `token.pickle`, `client_secrets.json`,
  and `.env` are gitignored — don't commit generated output or credentials.
- Part naming heuristics in `part_types.py` use clef + MIDI pitch thresholds
  (e.g. lowest < 50 = Bass) — adjust thresholds there, not in `main.py`.
