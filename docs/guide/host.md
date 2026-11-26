# This host

Part of this repository's agent guide; the index is [CLAUDE.md](../../CLAUDE.md). Moved out of it word for word (#513).

## This host: the live app, the deploy, the board

The app is not something someone starts when they want it. It runs as a systemd **user**
service from this very checkout, `song-app.service` on 127.0.0.1:8123. The units are
committed under `systemd/` — the live copies live in `~/.config/systemd/user/`, so an
edit here is not live until it is copied over and `systemctl --user daemon-reload` has
run.

The phone reaches it over Tailscale at **https://choir.taile8d16e.ts.net/**, a Tailscale
*service* (`svc:choir`) whose 443 handler proxies to `http://127.0.0.1:8123`. The older
`https://bazzite.taile8d16e.ts.net:8123` still works and points at the same app. That
config lives in tailscaled, not in this repo:

```bash
tailscale serve --service=svc:choir --https=443 --set-path=/ http://127.0.0.1:8123
```

Port 8000 is **not** available on this host — a podman container (`sos`, the Outdoor dev
server) has it, and `song.py` silently falls back to the next free port rather than
failing, so an app "started on 8000" ends up somewhere like 8002 with nothing pointing at
it. The two addresses are different **origins**, so an installed PWA does not follow a
move from one to the other; it has to be installed again (see the PWA identity notes).

A merge deploys itself. `song-app-deploy.timer` runs `scripts/deploy-song-app.sh
--unattended` every two minutes: it fast-forwards `main` to `origin/main`, installs
requirements, restarts the app and waits for `/healthz` to say `ok`. **It refuses more
often than it acts** — a dirty checkout, another branch, or local commits that are not
on `origin/main` all mean someone is working here, and deploying would either destroy
that work or ship a commit GitHub has never seen. Nothing to deploy is a no-op with no
restart, deliberately: a restart is the evidence a release waits for, so a spurious one
would tell the board a merge had reached the host when it had not.

"Dirty" has to mean *a person edited the checkout*, and one thing that is not that
used to trip it: the poller checks each issue out into `.worktrees/issue-N` **inside
this repo**, so `git status --porcelain` listed an untracked `.worktrees/` and the
deploy refused for as long as any agent was working. It refused silently as far as the
board was concerned — the timer failed every two minutes, the app kept serving old
code, and the card that had just merged was told its production deploy had failed. This
change gitignores `.worktrees/`. A worktree is a separate working directory, so a
fast-forward of `main` cannot disturb one; leaving it visible bought nothing and cost
every deploy made while a card was open.

**`songs/` is backed up nightly** (#406): `songs-backup.timer` runs
`scripts/backup-songs.sh` at 03:30, which commits everything in `songs/`, merges the
songs repo's `main` in and pushes it. A failure is a failed unit
(`systemctl --user --failed`); see SETUP.md.

`/healthz` exists for that watcher. It returns `{"status": "ok"}` and touches nothing —
it has to answer while a scan, clean or publish is occupying the worker threads.

**Song chats need three keys in `.env`, and for a month they were not there.** The
AgentDeck mapping added in #48 reads `AGENTDECK_URL`, `AGENTDECK_API_URL` and
`AGENTDECK_ACCOUNT_KEY`, and they were only ever written to `.env.default` — blank.
`.env` exists on this host, so `.env.default` is never loaded, and every song answered
`unconfigured` (#52). They are now set: browser URL
`https://bazzite.taile8d16e.ts.net` (the tailnet origin AgentDeck itself is served
from, so the phone can open it), API URL `http://127.0.0.1:8756`, account
`claude_code:main`. `.env` is gitignored, so this is host state, not something a
checkout carries — a second host has to be told again. One trap when testing by hand:
an AgentDeck-owned agent chat already exports `AGENTDECK_URL` as the loopback base for
its own API calls, and `load_dotenv` does not override a variable that is already set,
so `./song.py` run from such a chat persists the loopback origin into the song's
`.song.json`. The service has no such variable; clear them (`env -u AGENTDECK_URL`)
before reading anything into a conclusion.

**homr lives at `~/.local/share/musescore-choir-plugins/homr-venv` on this host**, put
there by `scripts/install-homr.sh` and pointed at by nothing in `.env` because that is
the script's own default. It is host state, like `.venv` — but unlike the `omr`
distrobox container it replaces, a second host reproduces it by running one committed
script. The deploy does not touch it, on purpose: it is not in `pip-requirements.txt`,
so a merge cannot churn 150 MB of model weights, and `/healthz` does not know it
exists.

Issues are worked by AgentDeck (instance `musescore`, unit
`agentdeck-poller@musescore.timer`): the `claude` label starts a Work, and the deck runs
it. Its manifest is **not** in this repo: it lives in
`~/agentdeck/poller/manifests/musescore/`, with every project's alongside it, and the
host half (paths, account, token) is the uncommitted overlay in
`~/.local/share/agentdeck/poller/instances/musescore/`. Comment `/claude <what you want>`
on an issue to steer work. The deck merges a pull request by itself once its review
passed and its checks are green on the same head, so nobody presses Merge
(agentdeck#3269); the `paused` label holds a Work, and a draft is never merged.
