# Setting up on a new machine

Everything here is macOS, because the recording half of the toolkit is. The
score-cleaning, lyric and scrolling-video halves are not, but nothing else has
been tried.

## 1. The two repos

The songs themselves are **not in this repo** — they are copyrighted sheet music,
so they live in a separate **private** repo that is cloned into `songs/` (which
this repo gitignores):

```bash
git clone git@github.com:eerovil/musescore-choir-plugins.git
cd musescore-choir-plugins
git clone git@github.com:eerovil/musescore-choir-songs.git songs
```

That second clone is what makes a new machine useful rather than empty. Skip it
and the toolkit still runs — `songs/` is just created empty on first use.

`songs/` is its own git repo, so `git` inside it acts on the songs, and `git` at
the repo root acts on the code. Neither sees the other's changes.

## 2. Python

```bash
python3 -m venv .venv                        # 3.13 here; 3.11+ should be fine
.venv/bin/pip install -r pip-requirements.txt
```

There is no `pip install -e .` step and nothing is on `$PATH`: the CLI wrappers
do `from src...import`, so **run them from the repo root** — `./song.py`,
`./clean_score.py`, not from inside `src/`. Use `.venv/bin/python` directly
rather than activating.

## 3. Things that are not pip packages

```bash
brew install ffmpeg poppler
```

| what | needed by | without it |
| --- | --- | --- |
| **MuseScore 3** | everything (`.mscz`/MusicXML conversion, audio, MIDI) | nothing works; MuseScore 4 is a different CLI and is not supported |
| `ffmpeg` / `ffprobe` | both video renderers | no video |
| poppler (`pdftoppm`, `pdfinfo`) | PDF system crops | Systems tab and the lyric editor's score crops are unavailable; their tests skip |
| QuickRecorder | the screen recorder only | use the scrolling renderer, which is the default anyway |

Install the QML plugins separately by copying or symlinking `plugins/` into
`~/Documents/MuseScore3/Plugins` — MuseScore loads them from there, not from this
repo. Only `export.qml` is needed for the screen-recording path; the rest are
hand tools.

## 4. `.env`

`.env` is gitignored (it points at local paths), so copy the defaults and check
them:

```bash
cp .env.default .env
```

The one that must be right is `MUSESCORE_CLI_PATH` — on a stock macOS install
`/Applications/MuseScore 3.app/Contents/MacOS/mscore`. Verify:

```bash
"$(grep MUSESCORE_CLI_PATH .env | cut -d'"' -f2)" --version
```

`YOUTUBE_CLIENT_SECRETS_PATH` matters only for uploading (below);
`MUSESCORE_EXPORT_PATH` and `VIDEO_EXPORT_PATH` only for the screen recorder.

### AgentDeck song chats (optional)

The song workspace can keep one normal AgentDeck chat mapped to each song. The
mapping is stored in that song's `.song.json`, so reopening the song reuses the
same chat instead of creating another one.

Set these in `.env` when AgentDeck is available:

```dotenv
AGENTDECK_URL="https://agentdeck.example"       # URL the browser should open
AGENTDECK_API_URL="http://127.0.0.1:PORT"       # optional; blank = AGENTDECK_URL
AGENTDECK_ACCOUNT_KEY="provider:account"         # exact AgentDeck account key
```

`AGENTDECK_API_URL` is useful when AgentDeck's browser URL sits behind a reverse
proxy or access layer but Choir and AgentDeck run on the same host. It is used
only for Choir → AgentDeck requests; the persisted link always uses
`AGENTDECK_URL`. The account key is the value AgentDeck shows/uses for the account,
not merely its display label.

Creating a song chat uses AgentDeck's ordinary **New chat** path, with the song
folder as its working directory. It is deliberately not an AgentDeck delegation:
the mapped chat is a long-lived user workspace and should keep normal boss-chat
semantics. If AgentDeck later reports that the mapped session is gone, the Choir
header changes the action to **Recreate AgentDeck**. A temporary AgentDeck outage
does not erase the mapping.

## 5. Check it works

```bash
.venv/bin/python -m pytest src/clean_score/tests/ src/song_app/tests/ src/scrollvideo/tests/ -q
```

180 pass with everything installed; fewer is expected — browser tests skip
without Playwright, the PDF tests without poppler, the sync tests without a
MuseScore CLI. Then the real check:

```bash
./song.py
```

Open a song that has already reached **review**, and look at the Cleaned MSCX
preview. If it renders, MuseScore, the Python side and the songs clone are all
talking to each other.

The prototyping fixture is a public-domain song kept in this repo, so it works
before the songs clone exists:

```bash
fixtures/virta-venhetta-vie/reset.sh
```

## 6. YouTube upload (only if you upload from this machine)

Not synced, and deliberately: `client_secrets.json` and `token.pickle` are both
gitignored. Download the OAuth client secret from the Google Cloud project into
the repo root as `client_secrets.json`; the first upload opens a browser to
authorise and writes `token.pickle` beside it. Uploads from a second machine
mean a second `token.pickle` — that is fine, they are independent.

## Working across machines

Commit and push `songs/` when you stop working on a song — the state file
`.song.json` is the thing that matters, and it is tiny. Two rules follow from
what the songs repo does *not* carry:

- **Practice audio and video are per-machine.** `media/` is gitignored (tens of
  gigabytes, past GitHub's limits), so a song that shows as *recorded* on the
  laptop has no video on the desktop. Re-render it there —
  `./scroll_video.py` is unattended and deterministic — or, if it was uploaded,
  it is on YouTube and the ids are in `.song.json`.
- **Rendered previews rebuild themselves.** `.pages/`, `*.render.pdf` and the
  `.persystem_cache.json` answer cache are all derived; the first page load on a
  new machine is slow while MuseScore re-renders, then it is cached again.

There is no locking. Editing the same song on two machines at once gives you a
merge conflict in a `.mscx`, which is not something you want to resolve by hand —
push before you switch.
