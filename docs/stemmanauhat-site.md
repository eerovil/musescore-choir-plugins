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
  score.mid                          the same score's MIDI, which the site plays
  timing.json                        playback time -> score position (below)
  manifest.json                      the same facts as the D1 row
```

`choir` is one of the ids in the site's D1 `choirs` table (`SELECT id, name,
public, staging FROM choirs`), which song-app reads to offer them (#408); `public` is the
one every site has. A choir with `staging = 1` is the site's test list
(stemmanauhat-cf#73, `testi` on the original copy); a site without the column has none. On the original copy they are `jm`, `naiskuoro` and `public`. `slug` is song-app's folder name and
never changes.

**No audio is published.** The site plays `score.mid` with a piano soundfont and
mixes the parts itself (stemmanauhat-cf), so the per-part MP3s song-app once
rendered are no longer made (stemmanauhat-cf#36). The MIDI is exported off the
same prepared copy as the MusicXML and the timing, so its tracks, tempo changes
and fermata holds are the ones the timing was measured on. `parts` lists the part
names in score order; the site matches them to the MIDI tracks. Songs
published before carry a `file` per part as well, and their MP3s stay in R2
unread.

`manifest.json`:

```json
{"slug": "hanget-soi", "title": "Hanget soi", "choir": "jm",
 "version": "20261009T120000Z", "score": "score.musicxml", "timing": "timing.json",
 "midi": "score.mid", "parts": [{"name": "T1"}, {"name": "T2"}, ...],
 "duration": 96.375, "composer": "…", "lyricist": "…"}
```

**Parts that share a staff** (#420). A song with more than five singing parts
is drawn with its parts back on the staves the page prints them on, so
`score.musicxml` can have fewer parts than `parts`. Then every entry says where
it is drawn: `staff` is the 0-based `<part>` of `score.musicxml` and `voices` the
`<voice>` numbers of its notes there:

```json
"parts": [{"name": "S1", "staff": 0, "voices": [1]},
          {"name": "S1b", "staff": 0, "voices": [2]}, ...]
```

An entry without them is one part on its own staff in voice order, as every song
published before had. The MIDI still has one track per part.

`composer`, `lyricist` and `arranger` are the credits set on song-app's Publish
panel (prefilled from the score's own metaTags until someone saves them). Each is
present only when it is set; a manifest from before them has none, and none had
`midi`.

## D1: the list

The site owns the `songs` table: its migrations create it and add to it, and
song-app creates nothing. A database without them refuses the first publish. The
columns song-app writes:

```sql
  choir TEXT NOT NULL,          -- jm | naiskuoro | public
  slug TEXT NOT NULL,
  title TEXT NOT NULL,          -- the song's display name
  prefix TEXT NOT NULL,         -- R2 prefix of the current version, ends in "/"
  parts TEXT NOT NULL,          -- JSON, the manifest's "parts"
  duration REAL NOT NULL,       -- seconds
  published_at TEXT NOT NULL,   -- ISO 8601 UTC
  composer TEXT,                -- the credits (site migration 0004); NULL when not set
  lyricist TEXT,
  arranger TEXT,
  PRIMARY KEY (choir, slug)
```

Publishing upserts one row. A song can be in any number of lists at once:
publishing to `public` or to a choir deletes no other choir's row (#450; until
then a publish to a choir deleted the song's row in any *other* private choir,
the rule of the site's hand-publish script, eerovil/stemmanauhat-cf#20, which
took songs shared by `joy` and `joy-alumnit` off one of them). Publishing to the
test list deletes nothing either (#422), so a fix tried out there leaves the song
on its choirs' lists; publishing to a choir still takes it off the test list. song-app keeps
one publish record per choir, so replacing a version deletes only that choir's
old files.

### The voice tree

The site carries a singer's part from song to song with a tree of voices
(stemmanauhat-cf#107, its migration 0009): `voices (id, parent, name_fi, name_en,
names)`, at most four levels, and `unplaced_parts (name)` for names in no place on
purpose. Names are compared ignoring letter case and spaces, and a name ending in
"b" counts when the name without it is above the fourth level. song-app reads
both tables and, for each part name in neither, the Publish panel asks where it
goes (#430); where a part belongs is the publisher's call, never a guess. Before
anything goes up, the publish writes:

- another name for a place: `UPDATE voices SET names = json_insert(names, '$[#]', ?)`
- a new place under one: an `INSERT INTO voices` row whose id and both names are
  the part name (made unique), until someone renames it on the site
- no place: `INSERT OR IGNORE INTO unplaced_parts`

A part name with no answer refuses the publish. A database without the tables
is published to as before.

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
its MIDI tempo map, the same MIDI that is published. song-app refuses
to publish when the two disagree by more than 10 ms anywhere.
