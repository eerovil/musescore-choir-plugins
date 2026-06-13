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
.venv/bin/python -m pytest src/clean_score/tests/ -q     # 62 tests, all passing
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
- `test_missing_tuplets.py` / `test_missing_slurs.py` — the dropped-tuplet and
  dropped-slur cross-voice auto-fixes (mirror within/across staves; well-formed and
  donor-less voices left untouched).
- `test_revoice.py` / `test_interactive.py` / `test_json_staff_mapping.py` — the
  re-voicing plan, non-interactive anomaly reduction, and JSON lyric staff mapping.

## How the voice-splitting pipeline works (`src/clean_score/main.py`)

`main(input_path, output_path, add_staffs=None)` parses the
`.mscx` XML and transforms it in passes:

0. `fix_missing_tuplets` (`utils/missing_tuplets.py`) repairs OCR measures where a
   tuplet bracket was dropped from one voice but a parallel voice (any staff, same
   measure index) kept it. It only touches a voice whose ticks don't add up *and*
   where a donor tuplet matches by tick position + base duration + note count, then
   copies the tuplet onto the run and pads the leftover with a rest. Never guesses a
   tuplet without a donor. Runs first (before any split/rebuild), all modes.
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
6. `add_missing_ties` then `add_missing_slurs` (`utils/missing_slurs.py`) recover
   OCR-dropped ties/slurs by mirroring them from a parallel voice that kept the
   spanner at the same tick span (ties: same pitch; slurs: same note count). This
   also fixes lyric alignment, since slur/tie-continuation notes get no syllable.
   Then `detect_part_types` (clef + pitch-range heuristics name parts S/A/T/B and set
   clefs), apply names/clefs, strip brackets/barLineSpan.
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
  explicit `parts: [ids]` on a lyric overrides the mapping (manual fix for the
  ~inevitable LLM errors). Legacy numeric/`DEFAULT_PART_TO_STAFF` part keys still
  work. `--split` duplicates a part into two staves. For `--per-system` scores the
  printed numbering shifts per system (omitted parts), so import prefers the
  per-system `lyricsSystemMap` (`read_lyrics_system_map`) and resolves each block via
  the map for the system covering its `measure_start`; it falls back to the single
  `lyricsStaffMap` when no system map is present.
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
