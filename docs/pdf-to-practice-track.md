# From a PDF to a practice track (agents)

What to do when someone hands you sheet music and says "build me a practice track from this
PDF". The result is one sung MP3 per part, plus the score and its MIDI, in a folder. Putting it
on a stemmanauhat site is a last, optional step.

Ask the person only two things: a bar you cannot read off the page with confidence (show them
the bar and the crop), and where to publish (and whether to at all). Decide everything else
yourself.

## 0. The machine

- `.venv` with `pip-requirements.txt`, MuseScore 3 (`MUSESCORE_CLI_PATH`), poppler, ffmpeg:
  `scripts/setup.sh`, then `./song.py --check`.
- homr, which reads the notes off the page: `scripts/install-homr.sh`.
- The singer: `scripts/install-singer.sh` (uses an NVIDIA card when there is one, else the
  processor), and the UFR DiffSinger voices downloaded by hand from
  <https://utaufrance.com/telechargement-du-pack-diffsinger-ufr/>, with `UFR_DIR` in `.env`
  naming the folder that holds `core/`, `dsdur/` and `tiger/`.
- The app running: `./song.py --no-browser` prints its address. Every step below is a call to
  it; `$APP` is that address (`http://127.0.0.1:8000`, or the next free port).

A step that runs a job (scan, clean) answers `{"started": true}` at once. It is done when
`GET $APP/api/songs/<slug>` shows `jobs.<kind>.status` as `succeeded` (or `failed`, with the
error in its log). Nothing tells you when it ends, so look once after about as long as it
takes: a clean is seconds, a scan about 20 seconds a system on a graphics card.

## 1. Make the song and read the page

```bash
curl -F name="Song name" -F per_system=true -F voicing=mixed -F pdf=@song.pdf $APP/api/songs
```

`voicing` is `men`, `women` or `mixed`; the reply names the song's `slug`. Then:

1. `POST $APP/api/songs/<slug>/find-systems` with `{}`: the printed systems, as bands on the
   pages, each `{"index", "page", "top", "bottom"}` with `top` and `bottom` as fractions of the
   page's height (0 is its top). Look at each page (`GET .../page/<n>`) and correct a band that
   cuts a staff or the words under it.
2. `PUT $APP/api/songs/<slug>/bounds` with `{"systems": [...]}` from that reply.
3. `POST $APP/api/songs/<slug>/scan`: homr reads every system. A system that failed is listed
   in the scan log; scan again to read only the ones missing.
4. Count each system's bars on the page against the scan's, **staff by staff**: the scan can
   add an empty bar to one staff only and push that staff's later bars one bar late inside the
   system. Section 3 puts it back with `bar` and `delbar` fixes.

## 2. Name the parts and clean

`GET $APP/api/songs/<slug>/systems` lists, per printed system, the staves and how many lines
each carries: `{"grid": [{"system", "measure_start", "measure_end", "staves": [{"staff_id",
"lines", ...}]}]}`. Name each staff's lines, top first, keyed by that `system` (the first is
`0`) and `staff_id` (the first is `1`):

```bash
curl -X PUT -H 'content-type: application/json' $APP/api/songs/<slug>/systems \
  -d '{"0": {"1": "S1,S2", "2": "A1,A2", "3": "T1,T2", "4": "B1,B2"}, "1": {...}}'
```

The page pictures (`GET .../system/<n>`) and the clean's log count systems from 1 instead.

A blank staff takes the previous system's answer; `-` names nothing there. Name parts S/A/T/B
plus a number (`S1`, `A2`): the singer's voice is chosen by that first letter. Then `POST
.../clean`.

- A line a page divides further for a few bars is its part's name plus a letter after the
  number (`A1b`, not `Ab`): in bars where the staff prints one line it sings the part's notes,
  and `--fold-divisi` (step 6) folds it into its part's track. A staff that prints two-note
  chords most of the way is named that way (`A1,A1b`) from its first system, or the lettered
  line copies whole chords and every note is sung twice.
- Two parts printed in unison on one staff (an intro on an `S A` and a `T B` staff) cannot be
  named: name the upper part, then copy its bars to the other with `bar` fixes (step 3).

## 3. Check every bar against the page

The scan gets most notes right and some wrong; a practice track with a wrong note teaches it.

- `GET .../bar?staff=<n>&measure=<m>` reads a bar of the cleaned score, where `staff` counts
  the cleaned score's staves, one per part, top first (`GET .../bar` lists them); `GET
  .../system/<n>` is the page's picture of that system. Compare them bar by bar, part by part.
- `GET .../problems` lists the bars homr itself doubted (`⚠`), with its other readings.
- Every correction is an entry in the song's `fixes.json`, then `POST .../clean` again. A hand
  edit to the cleaned score is lost at the next clean. CLAUDE.md, "The prototyping fixture",
  describes each kind of entry (`bar`, `slur`, `tie`, `delbar`, ...) and the mistakes agents
  made with them.
- An `unmark` entry, saying what you checked, takes off a `⚠` homr put on a bar that is right.
- A bar MuseScore refused and reset to a rest is matched against its reading *before* the
  reset: its `from` is the `scanned_from` of that bar's `text` entry in `fixes.json`, not what
  `/bar` shows. A `bar` fix cuts ties coming into the bar, so add the previous bar's tie again
  after it. A `tie`, `slur` or `unslur` after a `bar` fix in the same bar takes as `from` what
  that fix wrote, and a `delbar` takes a `from` per staff (`[["whole:R"], ["measure:R"], ...]`)
  when the staves read differently. A bar the scan gave the wrong length (`len` on the
  Measure) takes three steps: `bar` every staff to the same rest, `barlen`, then `bar` each
  staff again with `from: ["measure:R"]`. Check lengths, time signatures and voltas in the
  cleaned `.mscx` too.
- Triplets and slurs are what the scan gets wrong most. A slur decides which notes share a
  syllable, so a missing one shows up as a lyric that does not fit (next step).

## 4. Words

Read the words off the page (text a scan or a PDF's text layer gives is often garbled). Write
one block per printed system, and in it one line per part, naming the part (`lyric_json_prompt.txt`
describes every field):

```json
[{"measure_start": 1, "lyrics": [
   {"staff_number": 1, "text": "Kun mi-nä o-len", "parts": ["S1"]},
   {"staff_number": 1, "text": "Kun mi-nä o-len", "parts": ["S2"]}]},
 {"measure_start": 5, "lyrics": [...]}]
```

and send it as a string: `POST $APP/api/songs/<slug>/lyrics` with `{"json": "<that JSON>"}`.

Hyphens join a word's syllables; `_` is a note with no syllable. The reply's `mismatches` say
where a line has too many or too few syllables for the notes: too many means the reading is
wrong, one or two too few usually a slur the scan dropped (fix the score, step 3). A sentence
spoken on one held chord is one syllable: join its words with `‿`. A page that prints a syllable
under each note of a slurred pair ("wa - ah") uses the slur for phrasing only: take it out with
`unslur`, which shows up as one too many syllables.

A second verse under a repeated passage is a second entry with `"verse": 2`; the singer sings it
on the second pass.

## 5. Tempo, names, credits

```bash
curl -X POST -H 'content-type: application/json' $APP/api/songs/<slug>/publish-settings \
  -d '{"bpm": 92, "tempo_changes": [{"measure": 17, "bpm": 72}],
       "credits": {"composer": "...", "lyricist": "...", "arranger": "..."}}'
```

A cleaned score has no tempo of its own, so give the printed one. A page that prints only
words ("Reggae", "Tempo 2, slower") leaves the number to you: pick one that suits the style, and
say which you picked when you hand the song over. A printed swing marking is a `swing` entry in
`fixes.json` per marked bar.

## 6. Sing it

```bash
.venv/bin/python sing_song.py <slug>
```

It builds the score, MIDI and timing, checks every part's notes against the MIDI, sings each
part and writes `/var/tmp/practice-<slug>/` (`--out` moves it): `parts/<n>-<Name>.mp3` (a `/`
in a name becomes `_`), `score.musicxml`, `score.mid`, and its working files in `work/`. A few
minutes on a graphics card, much longer on the processor. MuseScore needs a screen even to
export: run it in the desktop's session, or with its `DISPLAY` and `XAUTHORITY` set.

- Not in Finnish: `--lang en|de|sv|la|et`. Two languages: `--lang-from fi:Word,...` names the
  first word of each stretch (it stops and lists the words it thinks you missed).
- A part name that does not start with S, A, M, T or B: `--voice LYSE` (high) or `--voice REMY`
  (low) for every part.
- A stopped run: the same command with `--resume` sings only the lines not finished.
- Lettered divisi lines (`A1b`, step 2): `--fold-divisi` sings each into its part's track
  instead of a track of its own.
- `--cpu` sings on the processor even when there is a graphics card.
- It stops before singing when a note's syllable would be sung as no sound at all (only stops,
  no vowel or held sound) and lists them by part and bar: change the words, or `--allow-silent`.
  A bare "kk" or "t" is sung as the consonant and a short "uh".

Check the result before publishing. The run stops by itself when a part's notes do not match the
MIDI. It does not check the words: each line's syllables, in the order they are sung, are in
`work/p<n>-*.json` (`syl`), and a second verse should come on the repeat. Every MP3 should last
the length the `bundle:` line gives. Listening to a part is the best check of all.

### Where heavy work runs

Reading a scan and singing run on this machine: on its graphics card when the installers found
an NVIDIA one, else on the processor. Nothing needs setting for that.

Another machine can take some of it. Set `REMOTE_RUNNER` in `.env` to a command; the app runs
`<runner> read-band <image> <out dir> <homr venv> <homr flags...>` for each scanned system, and
`sing_song.py` runs `<runner> sing-line <line.json> <out.wav> <voice>` for lines while this
machine sings others. Exit 0 with the file written means done; anything else and it is done
here. The one runner shipped is `src/kube_runner/runner.py`, for a node with an NVIDIA card in
a Kubernetes cluster: set `REMOTE_KUBE_CONTEXT` to its kubectl context. Its first job builds the
venv there (minutes), and the first line copies the voices from `UFR_DIR`.

## 7. Publish (optional)

Ask the person which list it goes on. With the site's Cloudflare keys in `.env`
(`CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `STEMMANAUHAT_D1_DATABASE_ID`):

```bash
.venv/bin/python sing_song.py <slug> --publish <choir>
```

Give it the same options as the run you checked, plus `--resume`, and it publishes those
tracks without singing them again.

A part name the site's voice tree does not know stops it; ask the person where it goes and pass
`--place "Name=<id>"`, `--place "Name=-"` (no place) or `--place "Name=^<parent>"` (a new place
under one). Publishing again makes a new version and takes the old one away. Never publish
piano-only (`--no-sing`) over a sung song.

## Re-singing after the singer changed (#453)

`SINGER_VERSION` in `src/sing_song/sing_ufr.py` is bumped whenever the singing rules change, and
each sung publish records the version it was sung with. The song app's song list then says, per
song, the choirs it still needs a re-sing in (the site's sung listings not published with the
current version), and `GET /api/resing` gives the same list. Re-sing a song with one run that
publishes to every choir it needs:

```bash
.venv/bin/python sing_song.py <slug> --waiting --publish jm --publish public   # when queueing it
.venv/bin/python sing_song.py <slug> --publish jm --publish public            # the job itself
```

The run writes how it is going to `songs/<slug>/.sing-run.json` (waiting, building, singing part
i of n, publishing, done, failed with the reason), which the song list shows and re-reads every
10 s while a run is waiting or going (every minute otherwise). `--resume` sings again any line an older singer version sang.
`--remote-only` sings every line on the remote runner (eero-pc's GPU here) and none on this
machine; a line the runner cannot sing stops the run instead of being sung here.
