#!/usr/bin/env bash
# Commit everything in songs/ and push it to the private songs repo
# (eerovil/musescore-choir-songs). Run nightly by systemd/songs-backup.timer.
#
# songs/ is its own git repo, and until #406 nothing committed it: it had one
# commit, made by hand, and seven weeks of work existed only on this disk.
#
# Order: commit what is here first, then merge origin's branch in, then push.
# Committing first means a merge never meets a dirty tree, and a merge that
# conflicts is aborted with the local commit kept, so nothing is lost and the
# next run tries again. Never rebases, never force-pushes.
#
# Exits non-zero on any failure, so the unit shows as failed
# (`systemctl --user --failed`, `journalctl --user -u songs-backup`).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SONGS_DIR="${SONGS_DIR:-$REPO_ROOT/songs}"
BRANCH="${SONGS_BRANCH:-main}"

# Unattended: a missing credential must fail the run, not open a password prompt.
export GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/bin/false SSH_ASKPASS=/bin/false

cd "$SONGS_DIR"
git rev-parse --git-dir >/dev/null

# One run at a time; a second one has nothing to add.
exec 9>"$(git rev-parse --git-dir)/songs-backup.lock"
if ! flock -n 9; then
    echo "songs backup: another run is in progress, skipping"
    exit 0
fi

git add -A
if git diff --cached --quiet; then
    echo "songs backup: nothing changed"
else
    files=$(git diff --cached --name-only | wc -l)
    git commit -q -m "Backup $(date +%F)"
    echo "songs backup: committed $files file(s)"
fi

git fetch -q origin "$BRANCH"
if ! git merge -q --no-edit "origin/$BRANCH"; then
    git merge --abort 2>/dev/null || true
    echo "songs backup: merging origin/$BRANCH failed; local commit kept, nothing pushed" >&2
    exit 1
fi

git push -q origin "HEAD:$BRANCH"
echo "songs backup: pushed $(git rev-parse --short HEAD) to origin/$BRANCH"
