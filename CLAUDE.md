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

MuseScore `.mscx` files are XML; almost all Python here is `lxml` tree
manipulation. There is no database, server, or web frontend.

## Layout

```
plugins/                 QML plugins for MuseScore 3 (install separately, see below)
song.py                  Launcher for the song web app (FastAPI; src/song_app/)
clean_score.py           CLI wrapper → src/clean_score/main.py (split voices into staves)
lyric_txt.py             CLI wrapper → src/clean_score/lyric_txt.py (lyrics <-> txt/json)
rename_parts.py          Standalone CLI: rename Part/Instrument names + add click staff
record_stemmanauha.py    CLI wrapper → src/stemmanauha (record practice video)
src/song_app/            Local web app tying the workflow together (see DESIGN.md)
  state.py               Song state machine (.song.json), slug, stages
  health.py              Health check (malformed-tick / extra-voice scan; no mutation)
  pipeline.py            Glue: convert + clean (clean_score) + lyric import (lyric_txt)
  server.py              FastAPI routes, WebSocket progress, file-watch re-check
  static/                Vanilla-JS SPA (library + 3-pane workspace, PDF iframe)
src/clean_score/         Score-cleaning package
  main.py                Voice-splitting pipeline (single-staff/2-voice -> 2-staff)
  lyric_txt.py           Lyric export/import (txt + json formats), slur/tie aware
  utils/                 part_types, reversed_voices, missing_ties,
                         corrupted_measures, utils, globals
  tests/                 pytest
src/stemmanauha/         Audio/video recording automation (macOS, AppleScript + OBS/ffmpeg)
  create_video.py        Orchestrates mp3 export -> video record -> merge -> upload
  upload_to_youtube.py   YouTube Data API upload
  *.scpt                 AppleScript files driving MuseScore + QuickRecorder
songs/                   Per-song working dirs (gitignored, output lives here)
backup/                  Gitignored .mscz backups (created by backup.sh)
*.txt prompts            lyric_json_prompt.txt, lyrics_txt_prompt.txt (LLM prompts for lyric fixing)
```

## Environment & running

- Python 3.13, virtualenv at `.venv/`. Use `.venv/bin/python` directly.
- Install deps: `.venv/bin/pip install -r pip-requirements.txt`
  (lxml, pytest, dotenv, google-api-python-client, google-auth-oauthlib, pillow,
  pyautogui, fastapi, uvicorn, python-multipart; recording also needs
  `obsws-python`, `ffmpeg`/`ffprobe` on PATH, and macOS with MuseScore 3 +
  QuickRecorder).
- Config is via `.env` (falls back to `.env.default`). Keys:
  `MUSESCORE_CLI_PATH`, `MUSESCORE_EXPORT_PATH`, `VIDEO_EXPORT_PATH`,
  `YOUTUBE_CLIENT_SECRETS_PATH`. Never commit real secrets;
  `.env`, `client_secrets.json`, and `token.pickle` are gitignored.
- The CLI wrappers import the package via `from src.clean_score... import ...`,
  so **run them from the repo root** (e.g. `./clean_score.py ...`).

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

# Backup all .mscz files to backup/
./backup.sh
```

### Tests

```bash
.venv/bin/python -m pytest src/clean_score/tests/ -q     # 60 tests, all passing
```

`pyproject.toml` only sets `log_cli_level=DEBUG`. Key test modules:

- `test_lyric_txt_spanner.py` — asserts lyric export→import round-trips back to
  the original XML (the real behavioral coverage).
- `test_simple1_split.py` — a **golden-file snapshot** test: it runs the split
  pipeline on `test_files/<name>_input.mscx` and compares the element-tag
  sequence against `<name>_output.mscx`. If you intentionally change pipeline
  output, regenerate the goldens by copying the freshly produced
  `<name>_test_output.mscx` over `<name>_output.mscx`. The comparison is
  shallow (tags only, not text/attributes).
- `test_per_system.py` — drives the `--per-system` rebuild against the real
  `laulun_aika.mscx` fixture (systems, part order, per-system pull, tuplet survival,
  line breaks, prompt reuse, and the answer cache).
- `test_missing_tuplets.py` — the dropped-tuplet cross-voice auto-fix (mirror
  within/across staves; well-formed and donor-less voices left untouched).
- `test_revoice.py` / `test_interactive.py` / `test_json_staff_mapping.py` — the
  re-voicing plan, non-interactive anomaly reduction, and JSON lyric staff mapping.

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
  MuseScore CLI), `run_clean` (calls `main(..., interactive=False)`; per-system
  reads `.persystem_cache.json`, which the **grid form** populates via
  `save_system_answers`), and `run_lyric_import` (calls `import_file`, capturing
  the stderr `Warning:` lines as the syllable-mismatch list). `system_grid` builds
  the per-system grid from `per_system.find_systems` + the staff voice summaries.
  `render_score_pdf` exports a `.mscx` to PDF via the MuseScore CLI (cached by
  mtime) so scores can be shown in-browser next to the original PDF — it renders
  from a temp copy with the staff size (`<Spatium>`) shrunk by `SPATIUM_SCALE`
  (env `RENDER_SPATIUM_SCALE`, default 0.65) so the score's own system breaks fit
  the page instead of MuseScore adding extra ones;
  `strip_lyrics_copy` writes a lyrics-removed copy (cached) so the "Cleaned MSCX"
  (no-lyrics) view always reflects the live structure rather than a stale snapshot.
- `health.py` is **validation only** (never mutates): per voice it sums note/rest
  durations as exact whole-note `Fraction`s (so tuplets don't round-off) and flags
  `malformed-measure` (voice doesn't fill the bar) and `extra-voices` (a staff
  measure with >1 note-bearing voice). Missing notes that still fill the bar (a
  half-rest standing in for lost notes, e.g. the m18 case) are **not**
  tick-detectable — they surface as lyric syllable overflow at import. Missing
  slurs are undetectable and stay manual. `merge_issues` carries over `dismissed`
  status and marks vanished open issues `fixed` across re-scans (ids are stable:
  `malformed-m18-s2-v1`).
- `server.py`: REST routes under `/api/songs/...`, a per-slug WebSocket (`/ws/{slug}`)
  for streamed progress logs + `state` pings, long tasks (clean/record) run in a
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
- `static/` is a dependency-free vanilla-JS SPA: a library view and a 3-pane
  workspace (stage rail · per-stage panel · viewer `<iframe>`, controls clustered
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
- **Hazards guarded:** re-cleaning warns it discards manual edits (the Clean
  button label changes once a cleaned file exists); lyric import uses `--replace`.
  No automatic LLM (users have no API key) — the lyrics stage is a permanent
  copy-paste round-trip (copy prompt → user's own AI + PDF → paste JSON back).

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
1. `preprocess_corrupted_measures` fixes measures with bad tick totals.
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
the normal split entirely: `find_systems` cuts at line breaks, `prompt_system_decls`
asks the user to name each staff's voices per system, and `build_parts` rebuilds the
score as one staff per named part (sorted S<A<T<B, then by number), pulling each
part's notes from the declared `(staff, voice)` per system and filling measure-rests
where absent. Old Parts/Staves are removed (that's how part deletion happens). Same
name on two staves in a system → first wins; blank → skip. Each staff prompt offers
the previous system's answer as a `[default]` (Enter reuses, `-` clears), and the
original line breaks are re-added on the top staff so the system layout survives.
Answers are cached per input file (basename, no extension) in `.persystem_cache.json`
at the repo root (gitignored): each interactive run loads the prior answers as the
`[default]` per staff and saves the chosen answers back, so re-running needs almost no
typing — and a complete cache lets `--per-system` run **non-interactively** (no TTY),
which is how tests drive it (`revoice_by_system(..., can_prompt=False)`).
main() handles this in an early branch. Because the PDF's printed staff numbering
**shifts per system** as parts are omitted (e.g. with T3 absent, the bass becomes
printed staff 3), it writes a per-system `lyricsSystemMap` metaTag (JSON: per
measure-range, `printed_no -> [output staff ids]`) in addition to an identity
`lyricsStaffMap` fallback. `build_system_lyric_map` builds it by grouping parts that
share a source staff into one printed staff (divisi: voice 0 → 'above', voice 1 →
'below') and ordering printed staves by **musical rank** (S<A<T<B, then number) — not
by the OCR's source-staff order, which can be shuffled. `lyric_txt.py` import reads it
(`read_lyrics_system_map`) and resolves each JSON block via the map for the system
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

## Lyric txt/json format (`src/clean_score/lyric_txt.py`)

The most intricate module. Round-trips lyrics between `.mscx` and a plain text
or JSON format, designed so an LLM can fix lyrics against the original score
(e.g. a PDF pasted into the chat) without breaking syllable alignment. The
prompt files `lyric_json_prompt.txt` / `lyrics_txt_prompt.txt` drive that.

- **Eligibility**: only voice 0, verse 1. A note gets a syllable token unless it
  is a slur/tie *continuation* (not the first note of the slur/tie) — those get
  no token. Rests get no token.
- **TXT format**: `# Measure N` headers, then `staffId [syllableCount]: tok1 tok2 ...`
  Tokens are space-separated; hyphens join syllables of a word (`il-man`);
  trailing hyphen = word continues into next measure; `_` = eligible note with
  no lyric. Syllabic state (begin/middle/end/single) is reconstructed from
  hyphenation on import. (Alignment is per-measure — the token counter resets at each
  barline — so a missing slur/tie only misaligns within its own measure, not the rest
  of the line.)
- **JSON format**: line-by-line; tokens are *distributed across measures* using
  actual chord counts from the score (`_get_chord_counts_per_measure`). The
  PDF-derived format has a `lyrics` array of `{text, staff_number, position,
  verse, parts}`. `staff_number` is the printed staff (top=1); `position` is
  `above`/`below`. These are mapped to **output staff ids** via the
  `lyricsStaffMap` metaTag that `clean_score` writes (`read_lyrics_staff_map`):
  a printed staff that split into two voices gets the line on both voices when
  only one position appears *in that block* (unison), or split upper/lower when
  both positions appear (divisi is decided **per block**, not globally). An
  explicit `parts` on a lyric overrides the staff_number/position mapping (manual fix
  for the ~inevitable LLM errors). `parts` accepts output staff **ids** *and/or part
  **names*** (`["T1","T2"]`, also a scalar `part`); names resolve via the score's
  trackNames (`read_part_name_map`). Names are the robust override — immune to
  printed-staff order (e.g. an ossia T3 printed on top), which staff_number cannot
  handle. The current `lyric_json_prompt.txt` has the LLM emit `"parts": []` (empty)
  in **every** lyric so manual overriding is just dropping ids/names into the existing
  array; empty → auto-map by staff_number/position. (An empty list is falsy, so the
  `if parts:` check falls through to the staff_number path — same as omitting it.) Legacy numeric/`DEFAULT_PART_TO_STAFF` part keys still work. `--split`
  duplicates a part into two staves. When a lyric has no `parts`, import falls back to
  staff_number/position: for `--per-system` scores the printed numbering shifts per
  system, so it uses the per-system `lyricsSystemMap` (`read_lyrics_system_map`) for the
  block's `measure_start`, else the single `lyricsStaffMap`. Resolution priority per
  lyric: explicit `parts` (ids/names) → staff_number+position via system/staff map.
  A null `measure_start` (the LLM emits null when no measure number is printed at the
  start of a line) is auto-filled by `_fill_missing_measure_starts`: blocks are one
  per printed system in order, so each null block takes the start measure of the
  system at its position (`find_systems`); explicit values are left alone, and a
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
