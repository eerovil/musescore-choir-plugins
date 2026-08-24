#!/usr/bin/env bash
# Bring the live song app on this host up to current origin/main.
#
# Two callers, one implementation:
#   * a person shipping a merge      — deploy-song-app.sh
#   * the deploy timer, every minute — deploy-song-app.sh --unattended
#
# The unattended path is what makes a merged card reach the running app without
# anyone typing anything. The poller's deploy-watch extension only *observes*:
# it checks that song-app restarted after the release started waiting and that
# /healthz answers, and parks the card in Blocked if neither happens. Performing
# the deploy is host wiring, so it lives here.
#
# It refuses rather than guesses:
#   - a dirty checkout is never touched (someone is working in it)
#   - a checkout on another branch is left alone
#   - local commits not on origin/main mean deploying would silently ship them
#   - already at origin/main is a no-op: no restart, so no false deploy evidence
#     for a release that is still waiting on its own merge
set -euo pipefail

REPO="${REPO:-/var/home/eero/musescore-choir-plugins}"
SERVICE="${SERVICE:-song-app.service}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8123/healthz}"
UNATTENDED=0
[ "${1:-}" = "--unattended" ] && UNATTENDED=1

say() { if [ "$UNATTENDED" = 1 ]; then echo "$*"; else echo "$*" >&2; fi; }
die() { say "deploy: $*"; exit 1; }

cd "$REPO"

branch=$(git rev-parse --abbrev-ref HEAD)
[ "$branch" = "main" ] || die "checkout is on $branch, not main — leaving it alone"
[ -z "$(git status --porcelain)" ] || die "checkout is dirty — leaving it alone"

git fetch --quiet origin main
local_head=$(git rev-parse HEAD)
remote_head=$(git rev-parse origin/main)

if [ "$local_head" = "$remote_head" ]; then
  say "already at $(git rev-parse --short HEAD) — nothing to deploy"
  exit 0
fi

# Anything here is a commit the deploy would ship that GitHub has never seen.
ahead=$(git rev-list --count origin/main..HEAD)
[ "$ahead" = "0" ] || die "$ahead local commit(s) are not on origin/main — push or drop them first"

say "deploying $(git rev-parse --short HEAD) -> $(git rev-parse --short origin/main)"
git merge --ff-only --quiet origin/main

# Dependencies may have moved with the merge. Quiet, and not fatal: a failed
# install must not leave the app stopped on the old code.
if ! .venv/bin/pip install -q -r pip-requirements.txt; then
  say "warning: pip install failed — restarting on the code as merged anyway"
fi

systemctl --user restart "$SERVICE"

# The restart is only evidence of a deploy once the app answers. Give it the same
# few seconds a person would.
for _ in $(seq 1 20); do
  if curl -fsS --max-time 2 "$HEALTH_URL" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
    say "deployed $(git rev-parse --short HEAD), $SERVICE healthy"
    exit 0
  fi
  sleep 1
done

die "$SERVICE did not answer $HEALTH_URL after the restart"
