# A new song from PDF to the site, and singing

Part of this repository's agent guide; the index is [CLAUDE.md](../../CLAUDE.md). Moved out of it word for word (#513).

## A new song from PDF to the site (agents)

The flow is `docs/pdf-to-practice-track.md`. What is particular to this host:

- The running app is at `http://localhost:8123` (`song-app.service`); drive it through its API.
- Run `sing_song.py` as a background command in your own chat (a few minutes on the card),
  not as an `agentdeck job`: the job queue's one heavy slot is shared with other projects' tests,
  and singing needs the card, not the processor (eerovil/eeron-omat-taskit#123). The card lock
  (`src/gpu_card.py`, #510) makes a singer wait for the card and rest 60 s after the last one (a
  continuous run crashed the old GTX 970 and needed a reboot), so runs can be started back to
  back; split a long batch into runs under 2 hours, the background command's limit. A person
  starts one from the app instead: Publish panel, *Sing and publish*.
  Give it the desktop's `DISPLAY` and `XAUTHORITY` (`systemctl --user show-environment`):
  MuseScore needs a screen even to export.
- Every live Finnish song has AI-sung parts (eeron-omat-taskit#36): a new song is done when it
  is published **and** sung. Publish only when the errand asks, to the choir it names: `jm` is
  the men's choir, `naiskuoro` the women's, `joy`, `joy-alumnit` and `yl` mixed, `laurille`
  three treble parts (`--voice LYSE`), `testi` the test list; only public-domain songs go in
  `public`. A part name the site's voice tree does not know: ask Eero where it goes, never guess.
- Check the live row afterwards: `cd ~/stemmanauhat-cf && ./scripts/run.sh --cloudflare corepack
  pnpm exec wrangler d1 execute stemmanauhat --remote --command "SELECT choir, prefix, parts,
  duration FROM songs WHERE slug='<slug>'"`.
- Leave the Review approval to Eero; `sing_song.py --publish` does not need it.
- A change to how a sound is sung is Eero's call by ear: explain it, and check it with
  `DUMP_TOKENS=<file>` (the planned sounds and their lengths, without singing) before rendering.
  The by-ear benchmark that tunes `sing_ufr.py` lives in eeron-omat-taskit (`finnish-sing-bench`).

## Singing (`src/sing_song/`)

`sing_song.py <slug>` builds the bundle with `publish.build_bundle` (the Publish settings'
tempo, part names, credits), then for each part: `unroll.py` writes each line (top and bottom
of the staff) on the bundle's clock, `check_onsets.py` stops the run unless every note starts
within 50 ms of a note in `score.mid`, `spoken.py` cuts a sentence on one held chord into a
syllable a note, `g2p.py` gives the sounds of a song not in Finnish, `sing_ufr.py` sings it
(UFR DiffSinger: every part of a men's song REMY and of a women's LYSE, by the song's voicing; in a mixed song or one with no voicing, LYSE for S/A/M parts and REMY for T/B, and a part whose name says no voice, like "Solo", by its clef: treble LYSE, bass or treble-8
REMY, said in the log; `--voice NAME=VOICE` overrides one part, named case-insensitively when exactly one part has that name, else by its number; two different bare `--voice VOICE` are refused), two different lines of a part are mixed
at half volume each, and `gate.py` fades the rests where the whole part rests (the voice
leaves a hiss). `--fold-divisi` folds lettered divisi parts (`S1b`) into their part as further
voices (Katedralen, eeron-omat-taskit#104). `--publish <choir>` hands the MP3s to
`publish.publish` as `Bundle.sung`. A publish that sends piano only (`--no-sing --publish`, or the app's
Publish) is refused, exit 3, when the site already has a sung version for that choir, checked
against the site's own row right before writing it; `--piano-over-sung` (the panel's "Publish
piano only" after it asks) replaces it (#483); the panel then points at *Sing and publish*, which
keeps it sung (#510). Each publish writes under its own version prefix (time plus six hex
characters), so a refused one deletes only its own files. `--try PART:A-B` sings bars A to B of one part
(the first pass through them, so a repeat is not sung twice; `check_onsets.py` then checks only
the notes starting in that phrase) and stops, to listen to a phrase before a whole run: it writes `try/<n>-<Name>-bars-A-B.mp3`
under `--out`, keeps a word that starts in range whole, prints how long the music and the
singing took, and publishes nothing. It came from eeron-omat-taskit's `sing-song/` (#439),
where its history is. The bundle's `words.json` (`sung_words()`) lists each part's notes with
the syllable sung on each; `--publish` sends it along (`Bundle.words`), and the site sings a
note several parts start together once only where they sing the same syllable
(eeron-omat-taskit#110: the tenor's "kis" fell on the bass's "dum"). `python -m
src.sing_song.add_words` added it once to the songs sung before it, only for parts whose every
note (pitch, place, words) still matches the live `score.musicxml`. With `REMOTE_RUNNER` set, lines are shared with the runner as each side
is free (`share()`, `<runner> sing-line <line.json> <out.wav> <voice>`); a line it fails is
sung here and it gets no more.

`src/kube_runner/runner.py` is that runner for a GPU node in a Kubernetes cluster (eero-pc
here, `REMOTE_KUBE_CONTEXT=eero-pc`), moved from eeron-omat-taskit's `homr-eero-pc/` (#439).
Stdlib only, so the app's service can run it as a file. One pod per job kind (`homr/reader`,
`sing/singer`), its venv on the node's disk, one job on the card at a time (a lock under
`HOMR_EERO_PC_STATE`, default `~/.cache/homr-the GPU machine`), and a kind that failed is not asked again for five
minutes. Every step is a short `kubectl exec`, retried, because eero-pc's long streams were
cut (#103). The reader builds its homr venv from this host's pins and checks it file by file.
`src/kube_runner/tests/` runs it against a fake cluster.

**Singing from the app** (#510, `song_app/sing_run.py`, diagram `docs/diagrams/sing-from-the-app.md`).
The Publish panel's *Sing and publish* (`POST /sing {choir}`, `GET /sing`, `POST /sing/stop`) runs
`sing_song.py <slug> --publish <choir>` with the panel's saved settings, and the Pronunciation
list's *Try* (`POST /sing/try {part, first, last}`, `GET /sing/try`, `GET /sing/try/<mp3>`) runs
`--try PART:A-B` and plays the MP3 in the page. Neither sings in the app's process: each is a
transient systemd user unit (`sing-<slug>`, `sing-<slug>-try`, `RuntimeMaxSec=3h`) running off a
snapshot of HEAD in `/var/tmp/sing-code/<sha>/` (`SING_CODE_DIR`), so a deploy restart or a merge
mid-run changes nothing; the desktop's `DISPLAY`/`XAUTHORITY` come from `systemctl --user
show-environment`, and a `REMOTE_RUNNER` inside the checkout is pointed into the snapshot. The app
writes nothing in `songs/`: `app.json`, `run.log` and `try/<id>/` (the last 3 tries) live under
`progress.state_dir(slug)`, `~/.local/state/musescore-choir-plugins/sing/<slug>/`
(`SING_STATE_DIR` in tests). A run's `.sing-run.json` is matched to the app's run by `run_id`
(`SING_RUN_ID`). One full run of a song at a time is `sing_song.py`'s song lock
(`progress.run_lock`); a start is refused (409) while it is held, the unit is active, or a job that
writes the song runs. **The card lock** (`src/gpu_card.py`): a singer holds a `flock` on
`~/.local/state/musescore-choir-plugins/card.lock` while it sings and waits for it otherwise
(the panel says "Waiting for the graphics card"), resting 60 s after the last singer; a homr read
takes the card only when it is free and otherwise reads on the processor, never waiting inside its
heavy slot. Tests: `test_card_lock.py`, `test_sing_api.py` (a fake `systemd-run` on `PATH`),
`test_sing_ui.py`.

**Re-sings** (#453): `SINGER_VERSION` in `sing_ufr.py` is bumped when the singing rules change; a
sung publish records it as `singer_version` on that choir's publish record, and a run writes its
progress to `songs/<slug>/.sing-run.json` (`progress.py`). The song list (`GET /api/resing`,
`song_app/resing.py`) calls a song pending in a choir where the site lists it sung (its D1 `songs`
rows, kept in `songs/.site-songs.json`) and this app did not publish that upload with the current
version; the test list never counts.
`SINGER_CHANGES` beside it says what each version changed: `"*"` (every song) or a language code
(#484), so a version that changes English re-sings only songs sung in English. A song's language
is `songs/<slug>/language.json` (`language_check.py`; not `.song.json`, so a job's stale save
cannot undo it), set from the New song form, the Start panel (`POST /language`) or the first
`--lang`; `--change-lang` replaces it. With none saved, a song is sung as Finnish only when its
words look Finnish (`looks_foreign`), and the re-sing list reads that check once per score
(`songs/.resing-language.json`).

**Languages and saved readings** (#484): every language a song can be sung in is one entry in
`languages.py` (name, whether g2p sings it, its exact vowel set, the sound prefixes a reading may
use, its rules); a variant (`la-de`, Latin the German way) names a `base` and lays its own rules
over it, and `languages.get` follows the chain. `language_check.LANGUAGES` and `main.G2P` are
read from it. `g2p.py` loads it by path, since the singer runs it with `python -I`. A saved
reading in `pronounce.json` carries the `lang` it is sung in (places `{bar, beat, word, sounds,
lang}`, `everywhere: {lang: {word: sounds}}`; an entry with none is English) and is voice sounds,
or ARPAbet for English only, checked against the pack's `core/phonemes.json` on save. A run
copies `pronounce.json` into its work folder once, so a save mid-run waits for the next run; each
g2p run writes `<notes>.<lang>.used.json`, and the run logs the saved readings none used.
`src/sing_song/tests/test_g2p_unchanged.py` (host only) pins English and Latin against g2p
before #484 over every foreign-looking song in `songs/`.

`SING_PROVIDER` (#483, `sing_ufr.providers`) picks the singer's onnxruntime provider: `cpu`,
`cuda` (or `GPU=1` when unset), or `coreml`, which falls back to the processor when the build has
no CoreML; each model's provider is logged. The kube runner does not pass it on.
The singer runs in its own venv (`scripts/install-singer.sh`, default
`~/.local/share/musescore-choir-plugins/sing-venv`; CUDA 11 onnxruntime when there is an NVIDIA
card, because eero-pc's GTX 970 is below newer builds' floor; it runs on this host's GTX
1660 Super as it is, eeron-omat-taskit#138). The voices are not in the repo: their
terms forbid re-hosting them, so `UFR_DIR` in `.env` points at a downloaded pack
(`~/.local/share/musescore-choir-plugins/ufr` here). The pack ships for OpenUtau (V1.0: `0_CORE/`
and a folder per voice); `scripts/install-singer.sh <the zip>` runs `src/sing_song/ufr_pack.py`,
which copies the files `sing_ufr.py` reads into `core/`, `dsdur/` and `tiger/` and sets `UFR_DIR`.
The singer is not deterministic: two runs are two takes.
