# What song-app publishes to the Cloudflare stemmanauhat site

The contract between song-app (`src/song_app/publish.py`, #382) and the new
score-player site (eerovil/stemmanauhat#8). song-app writes it; the site reads
it. Proposed by #382, so the site card may still change it — change both sides
together.

## Where it goes

One Cloudflare account, reached with one API token (R2 Storage: Edit, D1: Edit)
kept in song-app's `.env`, never in git:

```
CLOUDFLARE_ACCOUNT_ID=...
CLOUDFLARE_API_TOKEN=...
STEMMANAUHAT_D1_DATABASE_ID=...
STEMMANAUHAT_R2_BUCKET=stemmanauhat   # the default
```

## R2: the files

Each publish writes a fresh **version prefix**, and the D1 row is pointed at it
only after every file is there, so the site sees the old version or the whole
new one, never half. That choir's previous version's files are deleted afterwards.

```
songs/<choir>/<slug>/<version>/      version = UTC time, 20261009T120000Z
  score.musicxml                     the score, exported by MuseScore 3
  timing.json                        playback time -> score position (below)
  manifest.json                      the same facts as the D1 row
```

`choir` is `jm`, `naiskuoro` or `public`. `slug` is song-app's folder name and
never changes.

**No audio is published.** The site plays the score's MIDI with a piano
soundfont and mixes the parts itself (stemmanauhat-cf), so the per-part MP3s
song-app once rendered are no longer made (stemmanauhat-cf#36). `parts` lists the
part names in score order; the site matches them to the MIDI tracks. Songs
published before carry a `file` per part as well, and their MP3s stay in R2
unread.

`manifest.json`:

```json
{"slug": "hanget-soi", "title": "Hanget soi", "choir": "jm",
 "version": "20261009T120000Z", "score": "score.musicxml", "timing": "timing.json",
 "parts": [{"name": "T1"}, {"name": "T2"}, ...],
 "duration": 96.375}
```

## D1: the list

```sql
CREATE TABLE IF NOT EXISTS songs (
  choir TEXT NOT NULL,          -- jm | naiskuoro | public
  slug TEXT NOT NULL,
  title TEXT NOT NULL,          -- the song's display name
  prefix TEXT NOT NULL,         -- R2 prefix of the current version, ends in "/"
  parts TEXT NOT NULL,          -- JSON, the manifest's "parts"
  duration REAL NOT NULL,       -- seconds
  published_at TEXT NOT NULL,   -- ISO 8601 UTC
  PRIMARY KEY (choir, slug)
);
```

song-app runs that `CREATE TABLE IF NOT EXISTS` before every publish, so it
works before the site's own migration exists; the site's migration should create
the same table. Publishing upserts one row. A song can be in the `public` list and one
choir's list at once (#384): publishing to `public` deletes nothing, and
publishing to a choir deletes the song's row in any *other* private choir (a
move between `jm` and `naiskuoro`) but never its `public` row — the same rule as
the site's own hand-publish script (eerovil/stemmanauhat-cf#20). song-app keeps
one publish record per choir, so replacing a version deletes only that choir's
old files.

## timing.json

```json
{"version": 1, "measures": 33, "duration": 96.375,
 "points": [[0.0, 0, 0.0], [0.5, 0, 1.0], ..., [96.375, 32, 3.0]]}
```

Each point is `[seconds, measure, beat]`:

- `seconds` — playback time from the start of the song.
- `measure` — 0-based index of the bar in `score.musicxml` (the n-th `<measure>`
  of a part; a pickup bar is index 0).
- `beat` — quarter notes from that bar's start (6/8 counts in quarters too: an
  eighth is 0.5).

There is a point on every beat of every bar played, wherever the tempo changes
(fermatas), and at each bar's end. Between two points that name the same bar the
position moves in a straight line; that is exact, because the tempo is constant
between points. When the next point names another bar, the cursor jumps there.
Repeats and D.C./D.S. jumps are already unrolled: a repeated bar appears again
later with later times.

The clock is MuseScore's own — bar order from its `.mpos` export, seconds from
its MIDI tempo map — the same clock the audio was rendered on. song-app refuses
to publish when the two disagree by more than 10 ms anywhere.
