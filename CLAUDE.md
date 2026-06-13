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
clean_score.py           CLI wrapper → src/clean_score/main.py (split voices into staves)
lyric_txt.py             CLI wrapper → src/clean_score/lyric_txt.py (lyrics <-> txt/json)
rename_parts.py          Standalone CLI: rename Part/Instrument names + add click staff
record_stemmanauha.py    CLI wrapper → src/stemmanauha (record practice video)
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
  pyautogui; recording also needs `obsws-python`, `ffmpeg`/`ffprobe` on PATH, and
  macOS with MuseScore 3 + QuickRecorder).
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
.venv/bin/python -m pytest src/clean_score/tests/ -q     # 17 tests, all passing
```

`pyproject.toml` only sets `log_cli_level=DEBUG`. There are two test modules:

- `test_lyric_txt_spanner.py` — asserts lyric export→import round-trips back to
  the original XML (the real behavioral coverage).
- `test_simple1_split.py` — a **golden-file snapshot** test: it runs the split
  pipeline on `test_files/<name>_input.mscx` and compares the element-tag
  sequence against `<name>_output.mscx`. If you intentionally change pipeline
  output, regenerate the goldens by copying the freshly produced
  `<name>_test_output.mscx` over `<name>_output.mscx`. The comparison is
  shallow (tags only, not text/attributes).

## How the voice-splitting pipeline works (`src/clean_score/main.py`)

`main(input_path, output_path, add_staffs=None)` parses the
`.mscx` XML and transforms it in passes:

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
6. `add_missing_ties`, `detect_part_types` (clef + pitch-range heuristics name
   parts S/A/T/B and set clefs), apply names/clefs, strip brackets/barLineSpan.
7. `--add SSAA` appends new empty staves (rests) with the right clef per letter.

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
  hyphenation on import.
- **JSON format**: line-by-line per part; tokens are *distributed across measures*
  using actual chord counts from the score (`_get_chord_counts_per_measure`).
  Supports numeric part keys (= staff id) or names via `DEFAULT_PART_TO_STAFF`
  (`S1->1, S2->2, A1->3, A2->4`). `--split` duplicates a part into two staves.
- Import is in-place on the tree, removes verse 2+, and clears lyrics from
  ineligible (spanner-continuation) chords.

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
voice mp3 onto the video (with a 1300ms audio delay) → optional YouTube upload.

This is heavily environment-dependent: it relies on specific macOS apps, global
keyboard shortcuts wired in QuickRecorder/MuseScore, `MUSESCORE_EXPORT_PATH`,
`VIDEO_EXPORT_PATH`, and `ffmpeg`/`ffprobe`. It is not portable or testable in
CI. The `.scpt` AppleScript files and the keyboard shortcuts described in
`README.md`/`record_stemmanauha.py --help` must match. Re-recording requires
deleting existing files in `songs/<name>/media/`.

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
