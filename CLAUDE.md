# CLAUDE.md

Guidance for working in this repository. This file holds what every task needs; the rest
of the guide is in `docs/guide/`, one file per topic. **Before you change or debug
something, read its file from "Where the rest is" below.**

**Asked to build a practice track from a PDF?** Follow `docs/pdf-to-practice-track.md`
(AGENTS.md says the same for other agents).

Live-check song-app with a desktop viewport (about 1280 wide): below 840 px the stage bar
folds into a menu (`style.css`).

## What this project is

A personal toolkit for producing **choir practice tracks** from MuseScore
sheet music. The app works with MuseScore 3 or 4 (#414); MuseScore 3 is still the
default and the only one that runs the corruption check below, and the QML plugins
are MuseScore 3 only. It has two halves:

1. **MuseScore QML plugins** (`plugins/`) that run *inside* MuseScore 3 to edit
   scores and export audio.
2. **Python scripts/packages** (`src/`, plus thin CLI wrappers in the repo root)
   that process MuseScore files (`.mscx`/`.mscz`/MusicXML) as XML, fix lyrics,
   and publish the result.

The end goal: take a SATB-style score where multiple voices share a staff, split
it so each voice has its own staff, put the words under it, and publish it to the
Cloudflare stemmanauhat site (eerovil/stemmanauhat-cf), which draws the score with
OpenSheetMusicDisplay and plays its MIDI part by part. The app used to render
practice videos and upload them to YouTube; that is gone (#400), and a song now
ends at **Publish**.

MuseScore `.mscx` files are XML; almost all musical processing is `lxml` tree
manipulation. There is no database: persistent app state is JSON/files under
`songs/`. The local FastAPI server and vanilla-JS frontend in `src/song_app/`
orchestrate the same file-based tools.

## Where the rest is

Each file is a section moved out of this one word for word (#513). A new note goes in the
file for its topic, not here; add a file and a line below for a new topic.

- [layout.md](docs/guide/layout.md): which file or package does what. Read when you do not know where something lives.
- [song-app.md](docs/guide/song-app.md): `src/song_app/`: song state and stages, `pipeline.py`, `pdf_systems.py`, `health.py`, `server.py`, Publish, MuseScore's check after a clean, Check again, Undo, the `WRITERS` job list. Read before changing the server or a stage.
- [song-app-panels.md](docs/guide/song-app-panels.md): the browser side (`static/`): layout, phone, New song, the Lyrics panel and paste, the clean grid, heavy slots, the Fix panel's problem list, score download and upload. Read before changing a panel or its routes.
- [omr.md](docs/guide/omr.md): `omr.py`: where homr is installed, engines, GPU builds, the remote runner, slur and whole-rest repairs. Read before touching how homr is run or installed.
- [omr-systems.md](docs/guide/omr-systems.md): `omr_systems.py`: reading one printed system, flattening, voices, assembling, the meter. Read before changing what a scan assembles.
- [scan.md](docs/guide/scan.md): `scan.py`, `system_finder.py`, the Scan panel: holes, stamps and what a re-read discards, finding systems. Read before changing the scan stage.
- [omr-fork-rule.md](docs/guide/omr-fork-rule.md): whether an OMR fix goes in `eerovil/homr` or here, and how the fork follows upstream. Read before fixing a misread.
- [fixes-json.md](docs/guide/fixes-json.md): every kind of recorded fix in a song's `fixes.json`, `GET /bar`, and the Virta venhettä vie fixture. Read before writing or changing a fix.
- [clean-pipeline.md](docs/guide/clean-pipeline.md): `src/clean_score/main.py` pass by pass, per-system mode, voicing and part names. Read before changing cleaning.
- [lyric-placement.md](docs/guide/lyric-placement.md): `lyric_txt.py`: its interface, the TXT and JSON formats, routing, mismatches. Read before changing lyric import or export.
- [score-export.md](docs/guide/score-export.md): `src/score_export/`: the publish copy, tempo, fermatas, shared staves, MuseScore's clock. Read before changing what is published.
- [singing.md](docs/guide/singing.md): a new song from PDF to the site on this host, `sing_song.py`, the kube runner, singing from the app, languages. Read before singing, publishing or changing the singer.
- [reading-a-scanned-score.md](docs/guide/reading-a-scanned-score.md): how to read music and words off a PDF without fooling yourself. Read before judging a scan or fixing a song from its page.
- [tests.md](docs/guide/tests.md): the test rules in full and what each test module pins. Read before adding tests or when a test's purpose is unclear.
- [omr-benchmark.md](docs/guide/omr-benchmark.md): `fixtures/omr-benchmark/`, the scanned pages with note-level truth. Read before testing against real scans.
- [host.md](docs/guide/host.md): the live app (`song-app.service`, port 8123), the deploy timer, the nightly backup, `.env` keys for song chats, the poller. Read before touching the deploy or checking a change live.
- [plugins.md](docs/guide/plugins.md): the MuseScore 3 QML plugins. Read before changing one.

Also: `DESIGN.md` (the app's rationale and state model), `docs/stemmanauhat-site.md` (the
contract with the site), `docs/diagrams/`, `SETUP.md`, `TOOLS.md`, `CHANGELOG.md` (add a
line for each user-facing pull request).

## Environment & running

- Python 3.13, virtualenv at `.venv/`. Use `.venv/bin/python` directly.
- A new machine: `scripts/setup.sh` (venv, MuseScore 3, `.env`, optional homr), then
  `./song.py --check` (#408). SETUP.md is the walk-through for someone else's copy.
- Install deps: `.venv/bin/pip install -r pip-requirements.txt`
  (lxml, pytest, dotenv, pillow, fastapi, uvicorn, python-multipart, httpx,
  verovio, mido, numpy; MuseScore 3 and poppler are not pip packages).
- Config is via `.env` (falls back to `.env.default`). Keys:
  `MUSESCORE_CLI_PATH`, and for Publish `CLOUDFLARE_ACCOUNT_ID`,
  `CLOUDFLARE_API_TOKEN`, `STEMMANAUHAT_D1_DATABASE_ID` (optionally
  `STEMMANAUHAT_R2_BUCKET`). The video-era keys (`MUSESCORE_EXPORT_PATH`,
  `VIDEO_EXPORT_PATH`, `YOUTUBE_CLIENT_SECRETS_PATH`, `MEDIA_ROOT`,
  `STEMMANAUHAT_DISPATCH_TOKEN`, `STEMMANAUHAT_REPO`) are read by nothing now.
  The media already on disk (on this host `/var/mnt/ssd/choir-media`) and the
  videos on YouTube are left where they are. Never commit real secrets; `.env`,
  `client_secrets.json`, and `token.pickle` are gitignored.
- The CLI wrappers import the package via `from src.clean_score... import ...`,
  so **run them from the repo root** (e.g. `./clean_score.py ...`). Their shebang is the
  system `python3`, which lacks the dependencies, so unless `.venv` is activated run them
  as `.venv/bin/python clean_score.py ...`.
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

# Backup all .mscz files to backup/
./backup.sh
```

### Working in a worktree (agents, read this first)

An issue worker gets a fresh worktree under `.worktrees/issue-N`, and a fresh worktree
has **no `.venv`, no `.env` and no `songs/`** — all three are gitignored and live only
in the main checkout at `~/musescore-choir-plugins`. Interactive sessions use their own
worktree too (`git worktree add .worktrees/<topic> -b <branch> origin/main`), never that
shared checkout: several sessions share it, and a dirty one blocks the deploy timer. Without them nothing runs: there is
no interpreter with lxml in it, `MUSESCORE_CLI_PATH` is unset, and the app has no songs.
Link them in before doing anything else, and check they resolve before the first build or
test:

```bash
for f in .venv .env songs; do ln -sfn ~/musescore-choir-plugins/"$f" "$f"; done
```

`songs/` is the **real** song folders, shared with the running app — a re-clean or a
rename inside a worktree changes the songs you actually sing. Read them freely; write
to one only when the issue is about that song, and say so in the PR. Tests never need
it: they build their own songs in a tmp dir and read `fixtures/` from the worktree.

### Tests

Run only the tests of what you changed, push, and read CI; cite the CI run in the pull
request rather than a local count. Full rules and the module list: `docs/guide/tests.md`.

```bash
.venv/bin/python -m pytest src/song_app/tests/test_publish.py -q   # one module
.venv/bin/python -m pytest src/score_export/tests/ -q              # one package
```

- Nothing is gated: `main` has no branch protection and a merge is live within two
  minutes, so a red or unfinished CI run is the only thing that holds a merge.
- A poller verification job runs the full suite with `-m "not browser and not omr"`; CI
  runs the browser tests.
- Run song_app tests that publish with `env -u AGENTDECK_URL -u AGENTDECK_API_URL`, or
  they wait for a real heavy slot and hang.
- MuseScore tests run under a timeout: another heavy job on the host makes them fail.

## Rules that are easy to break

- A song's defect is fixed from the printed page into that song's `fixes.json`, with the
  reason. Do not add an automatic repair pass for it, and do not hand-edit the cleaned
  score: a re-clean rebuilds it (`fixes-json.md`).
- `scanned.musicxml` is derived; regenerate it, never edit it. homr is not a pip
  requirement and must not become one (`scan.md`, `omr.md`).
- If homr got the page wrong, the fix belongs in the fork (`omr-fork-rule.md`).
- `clean_score`'s `GLOBALS` is one per process: cleans take turns, never run two at once.
- A new job that writes a song goes in `server.WRITERS` and starts with `state.start_job`;
  take another copy's data with `song.adopt(fresh)`, never `song.data = ...` (`song-app.md`).
- Key lyric boxes and part lookups by staff id, never by part name. `lyric_txt.py`'s
  export and import must stay symmetric (`lyric-placement.md`).
- A part name the site's voice tree does not know: ask Eero where it goes, never guess.
  Publish only when asked, to the choir named (`singing.md`).
- The live app is on port 8123; port 8000 belongs to another project (`host.md`).

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
