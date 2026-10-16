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
#
# The lock is a directory, made in one step, so two runs cannot both get it; it
# holds the run's process number and goes when the run ends. It works the same on
# Linux and macOS (which has no flock). It lives in this machine's temporary
# folder, not in songs/.git, because songs/ may sit in a synced folder and a lock
# there would travel to other machines. A lock whose process has ended is stale
# (that run was killed) and is taken over.
LOCK="${TMPDIR:-/tmp}"
LOCK="${LOCK%/}/songs-backup-$(pwd -P | cksum | cut -d' ' -f1).lock"
if [ "${1:-}" = "--lock-path" ]; then
    echo "$LOCK"
    exit 0
fi

take_lock() {
    mkdir "$LOCK" 2>/dev/null || return 1
    echo "$$" >"$LOCK/pid"
}
if ! take_lock; then
    holder=$(cat "$LOCK/pid" 2>/dev/null || true)
    if [ -n "$holder" ] && ! ps -p "$holder" >/dev/null 2>&1; then
        echo "songs backup: stale lock from process $holder, which has ended; taking it over"
        rm -rf "$LOCK"
        take_lock || { echo "songs backup: another run is in progress, skipping"; exit 0; }
    else
        echo "songs backup: another run is in progress, skipping"
        exit 0
    fi
fi
trap 'rm -rf "$LOCK"' EXIT

git add -A
if git diff --cached --quiet; then
    echo "songs backup: nothing changed"
else
    files=$(git diff --cached --name-only | wc -l | tr -d ' ')
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
