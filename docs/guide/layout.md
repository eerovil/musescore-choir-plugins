# Layout: where each module lives

Part of this repository's agent guide; the index is [CLAUDE.md](../../CLAUDE.md). Moved out of it word for word (#513).

## Layout

```
plugins/                 QML plugins for MuseScore 3 (install separately, see plugins.md)
song.py                  Launcher for the song web app (FastAPI; src/song_app/)
clean_score.py           CLI wrapper → src/clean_score/main.py (split voices into staves)
lyric_txt.py             CLI wrapper → src/clean_score/lyric_txt.py (lyrics <-> txt/json)
rename_parts.py          Standalone CLI: rename Part/Instrument names + add click staff
sing_song.py             CLI wrapper → src/sing_song/main.py (sing the parts; optionally publish)
src/song_app/            Local web app tying the workflow together (see DESIGN.md)
  state.py               Song state machine (.song.json), slug, stages
  health.py              Health check (malformed-tick / extra-voice scan; no mutation)
  pdf_systems.py         Crop the source PDF into one image per printed system
  omr.py                 Run homr on a page image -> MusicXML (its own venv; see omr.md)
  omr_systems.py         Scan one printed system at a time; flatten + assemble
  scan.py                The scan stage: crop + read every band, assemble, invalidate
  system_finder.py       Propose the printed-system bands off a page (homr's staves)
  pipeline.py            Glue: convert + clean (clean_score) + lyric import (lyric_txt)
  publish.py             Publish: MusicXML + MIDI + timing + credits to the Cloudflare site
  check.py               `./song.py --check`: what this machine has and lacks, one line each
  server.py              FastAPI routes, WebSocket progress, file-watch re-check
  static/                Vanilla-JS SPA (library + 3-pane workspace, PDF viewer)
src/clean_score/         Score-cleaning package
  main.py                Voice-splitting pipeline (single-staff/2-voice -> 2-staff)
  lyric_txt.py           Lyric export/import (txt + json formats), slur/tie aware
  utils/                 part_types, reversed_voices, missing_ties,
                         corrupted_measures, utils, globals
  tests/                 pytest
src/sing_song/           Sing a song's parts with the UFR DiffSinger voices (see singing.md)
  main.py                bundle + unroll + onset check + sing + MP3s; --publish through publish.py
  sing_ufr.py            the singer (runs in scripts/install-singer.sh's venv)
  ufr_pack.py            the voices' folder from the UFR pack as it downloads (install-singer.sh <zip>)
  unroll.py, check_onsets.py, g2p.py, gate.py, spoken.py, fold.py   its steps
  places.py, readings.py the song page's Pronunciation list: where a word is sung, its readings
  tests/                 pytest
src/kube_runner/         REMOTE_RUNNER for a GPU in a Kubernetes cluster: read a band, sing a line
src/score_export/        The cleaned score made ready to publish (see score-export.md)
  score.py               the publish copy: silent parts out, marks off, tempos, fermata hold
  musescore.py           the MuseScore 3 or 4 CLI (run_musescore, part_names, CLI_TIMEOUT)
  clock.py               MuseScore's clock: TempoMap from the MIDI, bar order from .mpos
  engraving.py           verovio settings shared by every toolkit (RESOURCE_PATH, XML_ID_SEED)
  tests/                 pytest
fixtures/                In-repo prototyping song + the OMR benchmark's PD slice
                         (see fixtures/*/README.md, STEPS.md)
songs/                   Per-song working dirs (gitignored, output lives here)
backup/                  Gitignored .mscz backups (created by backup.sh)
README.md                The web app, for people using it (screenshots in docs/images/)
TOOLS.md                 The CLI tools and MuseScore plugins, run by hand
CHANGELOG.md             What changed, by merge date — add a line for each user-facing PR
*.txt prompts            lyric_json_prompt.txt, lyrics_txt_prompt.txt (LLM prompts for lyric fixing)
```
