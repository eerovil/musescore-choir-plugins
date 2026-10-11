# Setting up on a new machine

This was written on macOS. Nothing in the toolkit needs it any more (the live
app runs on Linux), so read `brew install` as your platform's package manager.

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
brew install poppler
```

| what | needed by | without it |
| --- | --- | --- |
| **MuseScore 3** | everything (`.mscz`/MusicXML conversion, previews, MIDI) | nothing works; MuseScore 4 is a different CLI and is not supported |
| poppler (`pdftoppm`, `pdfinfo`) | PDF system crops | Systems tab and the lyric editor's score crops are unavailable; their tests skip |
| homr (below) | reading a scanned page into MusicXML | scanned scores have to be brought in as MusicXML by hand; its tests skip |

### homr, for reading scanned scores (optional)

homr turns a page image into MusicXML. It is **not** a pip package of this
project and does not go into `.venv`: it is ~660 MB of onnxruntime and opencv
wheels plus ~150 MB of model weights it keeps inside its own site-packages, and
the unattended deploy reinstalls this project's requirements on every merge.
So it gets a venv of its own, outside the checkout, built by a script:

```bash
scripts/install-homr.sh          # ~10 minutes, ~660 MB on disk
scripts/install-homr.sh --status # installed commit vs the fork's main; installs nothing
```

Or press **Install homr** in the *homr* box at the bottom of the app's Library
page — it runs this same script and shows its log, so a phone can do it. The box
also says when the fork's `main` has moved past the installed commit and offers
**Update homr**. It is refused while a song is scanning, cleaning or publishing, and scans wait while it runs. If `uv` is missing, the script installs
it into `~/.local/bin` with astral's own installer first.

That creates `~/.local/share/musescore-choir-plugins/homr-venv` on python 3.12
and downloads the model weights, so the first scan is not the slow one.
Re-running it is safe. Set `HOMR_VENV` to put it elsewhere, and then `HOMR_BIN`
in `.env` so the app can find it. `HOMR_SOURCE` is where homr is installed
from. It follows `main` in our fork, which tracks upstream — so an install takes
whatever homr is on the day it runs, and a re-run can change how scores parse.
Set `HOMR_SOURCE` to a commit to get a particular parse back.
Without homr, `src/song_app/omr.py` raises a message saying so and its tests
skip; nothing else in the app is affected.

A branch of the fork needs **no install at all**. Clone the fork somewhere (or
add a git worktree to an existing clone) and the app will run it from source,
borrowing the installed venv's dependencies:

```bash
git clone https://github.com/eerovil/homr.git ~/homr
git -C ~/homr worktree add ~/homr-trees/system-4 prototype/system-4
```

`HOMR_CHECKOUT` says where the working copy is (default `~/homr`); its git
worktrees are found from it. The Scan panel then offers a **Read with** picker
listing the installed venv plus each working copy, labelled with the branch it
has out *at that moment* — switch a branch there and the next scan runs it, with
nothing to reinstall. The choice applies to that scan run only; nothing is
recorded, so comparing two engines means reading a system with one, then the
other, and looking at both against the page. The picker is hidden when the
installed venv is all there is.

The first time a working copy is picked, the venv's model weights are symlinked
into it — homr keeps its weights beside its own source, so otherwise every
worktree would download its own 150 MB.

Install the QML plugins separately by copying or symlinking `plugins/` into
`~/Documents/MuseScore3/Plugins` — MuseScore loads them from there, not from this
repo. They are all hand tools; nothing in the app runs them.

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

`CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` and
`STEMMANAUHAT_D1_DATABASE_ID` turn on the Publish button (below); blank, the
Publish stage says publishing is off on this host. The other keys in
`.env.default` (`MUSESCORE_EXPORT_PATH`, `VIDEO_EXPORT_PATH`,
`YOUTUBE_CLIENT_SECRETS_PATH`, `MEDIA_ROOT`, `STEMMANAUHAT_DISPATCH_TOKEN`) belonged
to the video and YouTube stages, which are gone; nothing reads them.

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

Leave the keys blank and the button is still there, but there is nothing for it to
open. This pull request proposes labelling that state **AgentDeck ⚠** and, on a tap,
writing the reason into a strip under the header. Before it, the button was disabled
and carried its reason in a `title` tooltip, which a phone never shows — so the whole
state read as a button that does nothing (#52).

## 5. Check it works

```bash
.venv/bin/python -m pytest src/clean_score/tests/ src/song_app/tests/ src/score_export/tests/ -q
```

180 pass with everything installed; fewer is expected — browser tests skip
without Playwright, the PDF tests without poppler, the MuseScore tests without a
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

## 6. Publishing (only if you publish from this machine)

Publish sends a song to the Cloudflare stemmanauhat site. It needs one API token
with *R2 Storage: Edit* and *D1: Edit* on that account, plus the account id and the
D1 database id, all in `.env`. The site's own migrations create its tables, so a
fresh database has to have them applied before the first publish. The formats are
in [docs/stemmanauhat-site.md](docs/stemmanauhat-site.md).

## Working across machines

Commit and push `songs/` when you stop working on a song — the state file
`.song.json` is the thing that matters, and it is tiny. One rule follows from
what the songs repo does *not* carry:

- **Rendered previews rebuild themselves.** `.pages/`, `*.render.pdf` and the
  `.persystem_cache.json` answer cache are all derived; the first page load on a
  new machine is slow while MuseScore re-renders, then it is cached again.

There is no locking. Editing the same song on two machines at once gives you a
merge conflict in a `.mscx`, which is not something you want to resolve by hand —
push before you switch.
