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
error in its log).

## 1. Make the song and read the page

```bash
curl -F name="Song name" -F per_system=true -F voicing=mixed -F pdf=@song.pdf $APP/api/songs
```

`voicing` is `men`, `women` or `mixed`; the reply names the song's `slug`. Then:

1. `POST $APP/api/songs/<slug>/find-systems` with `{}`: the printed systems, as bands on the
   pages. Look at each page (`GET .../page/<n>`) and correct a band that cuts a staff or the
   words under it.
2. `PUT $APP/api/songs/<slug>/bounds` with `{"systems": [...]}` from that reply.
3. `POST $APP/api/songs/<slug>/scan`: homr reads every system. A system that failed is listed
   in the scan log; scan again to read only the ones missing.

## 2. Name the parts and clean

`GET $APP/api/songs/<slug>/systems` lists, per printed system, the staves and how many lines
each carries. Name each staff's lines, top first, for every system:

```bash
curl -X PUT -H 'content-type: application/json' $APP/api/songs/<slug>/systems \
  -d '{"1": {"1": "S1,S2", "2": "A1,A2", "3": "T1,T2", "4": "B1,B2"}, "2": {...}}'
```

A blank staff takes the previous system's answer; `-` names nothing there. Name parts S/A/T/B
plus a number (`S1`, `A2`): the singer's voice is chosen by that first letter. Then `POST
.../clean`.

## 3. Check every bar against the page

The scan gets most notes right and some wrong; a practice track with a wrong note teaches it.

- `GET .../bar?staff=<n>&measure=<m>` reads a bar of the cleaned score; `GET .../system/<n>`
  is the page's picture of that system. Compare them bar by bar, part by part.
- `GET .../problems` lists the bars homr itself doubted (`⚠`), with its other readings.
- Every correction is an entry in the song's `fixes.json`, then `POST .../clean` again. A hand
  edit to the cleaned score is lost at the next clean. CLAUDE.md, "The prototyping fixture",
  describes each kind of entry (`bar`, `slur`, `tie`, `delbar`, ...) and the mistakes agents
  made with them.
- An `unmark` entry, saying what you checked, takes off a `⚠` homr put on a bar that is right.
- A bar MuseScore refused and reset to a rest is matched against its reading *before* the
  reset: its `from` is the `scanned_from` of that bar's `text` entry in `fixes.json`, not what
  `/bar` shows. A `bar` fix cuts ties coming into the bar, so add the previous bar's tie again
  after it. A bar the scan gave the wrong length (`len` on the Measure) takes three steps:
  `bar` every staff to the same rest, `barlen`, then `bar` each staff again with `from:
  ["measure:R"]`. Check lengths, time signatures and voltas in the cleaned `.mscx` too.
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
spoken on one held chord is one syllable: join its words with `‿`.

## 5. Tempo, names, credits

```bash
curl -X POST -H 'content-type: application/json' $APP/api/songs/<slug>/publish-settings \
  -d '{"bpm": 92, "tempo_changes": [{"measure": 17, "bpm": 72}],
       "credits": {"composer": "...", "lyricist": "...", "arranger": "..."}}'
```

A cleaned score has no tempo of its own, so give the printed one. A printed swing marking is a
`swing` entry in `fixes.json` per marked bar.

## 6. Sing it

```bash
.venv/bin/python sing_song.py <slug>
```

It builds the score, MIDI and timing, checks every part's notes against the MIDI, sings each
part and writes `/var/tmp/practice-<slug>/` (`--out` moves it): `parts/<n>-<Name>.mp3` (a `/`
in a name becomes `_`), `score.musicxml`, `score.mid`, and its working files in `work/`. A few
minutes on a graphics card, much longer on the processor.

- Not in Finnish: `--lang en|de|sv|la|et`. Two languages: `--lang-from fi:Word,...` names the
  first word of each stretch (it stops and lists the words it thinks you missed).
- A part name that does not start with S, A, M, T or B: `--voice LYSE` (high) or `--voice REMY`
  (low) for every part.
- A stopped run: the same command with `--resume` sings only the lines not finished.

## 7. Publish (optional)

Ask the person which list it goes on. With the site's Cloudflare keys in `.env`
(`CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `STEMMANAUHAT_D1_DATABASE_ID`):

```bash
.venv/bin/python sing_song.py <slug> --publish <choir>
```

A part name the site's voice tree does not know stops it; ask the person where it goes and pass
`--place "Name=<id>"`, `--place "Name=-"` (no place) or `--place "Name=^<parent>"` (a new place
under one). Publishing again makes a new version and takes the old one away. Never publish
piano-only (`--no-sing`) over a sung song.
