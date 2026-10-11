# Setting up your own copy

This gets the song app running on your own computer, from nothing. It is written
for Linux (tested on Fedora 42 and Ubuntu 24.04) with notes for macOS. To put the
songs on a practice-track site as well, you also set up your own copy of the
stemmanauhat site on Cloudflare (step 5).

What you need:

| what | why | without it |
| --- | --- | --- |
| Python 3.11 or newer | the app | nothing runs |
| **MuseScore 3** (3.6.2) | reading and writing scores, previews, the MIDI | nothing works. MuseScore 4 will not do: its command line is different |
| poppler (`pdftoppm`, `pdfinfo`) | cutting the PDF into one picture per printed line | no scanning and no page pictures |
| homr | reading a scanned PDF into notes | you bring each score as MusicXML yourself |
| a Cloudflare account and the stemmanauhat site | publishing | the Publish stage says publishing is off |

## 1. Get the code and run the setup script

```bash
git clone https://github.com/eerovil/musescore-choir-plugins.git
cd musescore-choir-plugins
scripts/setup.sh
```

`scripts/setup.sh` does everything it can without `sudo`, and is safe to run
again:

- makes `.venv` and installs `pip-requirements.txt` into it;
- finds MuseScore 3, or on Linux offers to download the official 3.6.2 AppImage
  into `~/.local/bin` (unpacking it into `~/.local/opt/MuseScore3` where an
  AppImage cannot start, as in a container);
- checks for poppler and prints the command to install it;
- copies `.env.default` to `.env` and writes the MuseScore path into it, leaving
  any value you already set alone;
- offers to install homr (about 10 minutes and 660 MB; see below);
- ends with `./song.py --check`.

`--yes` answers yes to every question, `--no-homr` skips homr.

**The system packages it cannot install for you.** MuseScore's AppImage needs a
few libraries a desktop has and a bare server does not, and on a machine with no
screen (a server, a container) it has to draw on a virtual one, `xvfb-run`:

```bash
# Fedora
sudo dnf install python3 git curl poppler-utils alsa-lib mesa-libGL mesa-libEGL \
    libX11 libxcb fontconfig freetype expat libgpg-error nss libdb fuse-libs \
    xorg-x11-server-Xvfb xorg-x11-xauth
# Ubuntu / Debian
sudo apt install python3 python3-venv git curl poppler-utils libasound2t64 libgl1 \
    libegl1 libx11-6 libx11-xcb1 libxcb1 libfontconfig1 libfreetype6 libexpat1 \
    libgpg-error0 libnss3 libglib2.0-0t64 libfuse2t64 libdb5.3t64 xvfb xauth
```

Where there is no screen, the script writes `~/.local/bin/musescore3-headless`, a
two-line wrapper that runs MuseScore inside `xvfb-run`, and puts that in `.env`.
These two lists are what a clean Fedora 42 and Ubuntu 24.04 container needed to
take the fixture song (step 3) through cleaning and lyrics to Review.

**macOS:** `brew install python@3.13 poppler`, and install MuseScore 3.6.2 from
<https://github.com/musescore/MuseScore/releases/tag/v3.6.2>. The script finds it
at `/Applications/MuseScore 3.app/Contents/MacOS/mscore`.

## 2. Check it

```bash
.venv/bin/python song.py --check
```

One line for each thing the app uses:

```
  ok      Python packages  all of pip-requirements.txt
  ok      MuseScore 3      /home/you/.local/bin/MuseScore3.AppImage (3.6.2)
  ok      poppler          pdftoppm and pdfinfo
  ok      songs/           /home/you/musescore-choir-plugins/songs (made on first start)
  off     homr             not installed: scanned PDFs cannot be read — ...
  off     Publish          off: set CLOUDFLARE_ACCOUNT_ID, ... in .env (see SETUP.md)

Ready: ./song.py starts the app.
```

`MISSING` is something the app cannot work without (the Python packages or
MuseScore 3) and makes the check fail; `off` is something optional.

## 3. Start the app

```bash
.venv/bin/python song.py               # opens the browser at http://127.0.0.1:8000/
.venv/bin/python song.py --no-browser --port 8123
```

To try it on a real song before you have your own, drop the public-domain song
kept in this repo into `songs/`:

```bash
fixtures/virta-venhetta-vie/reset.sh
```

**From a phone.** The app listens on `127.0.0.1` only. To reach it from a phone,
either start it with `--host 0.0.0.0` and open `http://<computer's address>:8000/`
on the same network (anyone on that network can then use it: there is no login),
or keep it on `127.0.0.1` and put it behind something that adds a login, such as
[Tailscale Serve](https://tailscale.com/kb/1312/serve). The app installs to a
phone's home screen; see [docs/PWA.md](docs/PWA.md).

## 4. homr, for reading scanned scores (optional)

homr turns a picture of a page into notes. It gets a venv of its own, outside the
checkout, because it is large (~660 MB of libraries plus ~150 MB of model weights):

```bash
scripts/install-homr.sh          # ~10 minutes
scripts/install-homr.sh --status # installed version against the newest; installs nothing
```

Or press **Install homr** in the *homr* box on a song's **Scan** stage, which runs
the same script and shows its log. The box also says when a newer homr is out. If
`uv` is missing, the script installs it into `~/.local/bin` first.

It goes into `~/.local/share/musescore-choir-plugins/homr-venv`. Set `HOMR_VENV`
to put it elsewhere, and then `HOMR_BIN` in `.env` so the app can find it.
`HOMR_SOURCE` is where it is installed from: it follows `main` of
[eerovil/homr](https://github.com/eerovil/homr), so a later install may read
scores differently. Set it to a commit to get a particular version back.

A branch of homr needs no install: clone the fork to `~/homr` (or set
`HOMR_CHECKOUT`), and the Scan panel's **Read with** picker offers it and each of
its git worktrees, borrowing the installed venv's libraries and model weights.

## 5. Publishing to your own stemmanauhat site (optional)

Publish sends a song to a copy of the stemmanauhat site
([eerovil/stemmanauhat-cf](https://github.com/eerovil/stemmanauhat-cf)), which
draws the score and plays it part by part, behind a per-choir login. Each copy
runs on its own Cloudflare account. Set one up by following that repository's
README, *Run your own copy*, which ends with three values for this app's `.env`:

```dotenv
CLOUDFLARE_ACCOUNT_ID="..."
CLOUDFLARE_API_TOKEN="..."        # R2 Storage: Edit and D1: Edit on that account
STEMMANAUHAT_D1_DATABASE_ID="..."
STEMMANAUHAT_R2_BUCKET=""         # blank for the bucket called "stemmanauhat"
```

Then `./song.py --check` lists the site's choirs on its **Publish** line. The
Publish stage offers those same choirs: they are read from the site's database,
so a choir added there (`scripts/add-choir.sh` in the site's repo) shows up here
without changing anything. The last list read is kept in `songs/.choirs.json`, so
the panel still shows the choirs while Cloudflare cannot be reached; publishing
itself waits until it can. The formats the site reads are in
[docs/stemmanauhat-site.md](docs/stemmanauhat-site.md).

## 6. Keeping your songs (optional)

Each song is a folder under `songs/`, with its state in `.song.json`. `songs/` is
not part of this repo (it is in `.gitignore`), because the songs are usually
sheet music under copyright. To keep them safe or move them between computers,
make `songs/` a git repository of its own and push it to a **private** repository:

```bash
cd songs && git init && git remote add origin git@github.com:you/your-songs.git
```

Run `git` inside `songs/` for the songs and at the top for the code; neither sees
the other's changes. Rendered previews (`.pages/`, `*.render.pdf`) rebuild
themselves and need not be kept. There is no locking: editing one song on two
computers at once gives you a merge conflict in a `.mscx` file, so push before you
switch.

`scripts/backup-songs.sh` commits and pushes `songs/` unattended, and
`systemd/songs-backup.timer` runs it nightly. It is written for the original host
(it pushes with AgentDeck's git credential helper), so read it before using it.

## 7. Keeping it running (optional)

To run the app all the time on Linux, as a systemd user service:

```bash
cp systemd/song-app.service ~/.config/systemd/user/
# The unit expects the checkout at ~/musescore-choir-plugins: edit
# WorkingDirectory and ExecStart if yours is elsewhere.
systemctl --user daemon-reload
systemctl --user enable --now song-app.service
loginctl enable-linger "$USER"     # keep it running while you are logged out
```

It serves on `127.0.0.1:8123`, and `/healthz` answers `{"status": "ok"}`.

To have it follow `main` by itself, also install `song-app-deploy.service` and
`song-app-deploy.timer`: every two minutes, `scripts/deploy-song-app.sh
--unattended` fast-forwards the checkout to `origin/main`, installs the
requirements and restarts the app. It refuses to touch a checkout with local
changes, on another branch, or with commits that are not on `origin/main`. Leave
it out if you want to choose when you update.

## 8. Other things in this repo

- The MuseScore plugins in `plugins/` are hand tools for MuseScore itself;
  nothing in the app runs them. Copy or link them into
  `~/Documents/MuseScore3/Plugins`. See [TOOLS.md](TOOLS.md).
- AgentDeck song chats: the app can open one AgentDeck chat per song. Leave
  `AGENTDECK_URL`, `AGENTDECK_API_URL` and `AGENTDECK_ACCOUNT_KEY` blank unless
  you run [AgentDeck](https://github.com/eerovil/agentdeck); the header button then
  says it is not set up.
- The tests: `.venv/bin/python -m pytest src/clean_score/tests/ src/song_app/tests/
  src/score_export/tests/ -q`. Tests that need something missing skip rather than
  fail: the browser tests without Playwright, the PDF tests without poppler, the
  MuseScore tests without MuseScore.
