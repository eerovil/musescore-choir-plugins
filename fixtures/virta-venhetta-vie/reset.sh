#!/usr/bin/env bash
# Drop the fixture song into songs/ at a chosen stage, replacing whatever is there.
#
#   fixtures/virta-venhetta-vie/reset.sh            # furthest stage (20-lyrics)
#   fixtures/virta-venhetta-vie/reset.sh 00         # just registered, ready to clean
#   fixtures/virta-venhetta-vie/reset.sh 10         # cleaned, one health issue open
#
# Stages are overlays: 10 and 20 hold only the files they add or change, so the
# PDF and the OCR XML are stored once. This script layers them in order.
set -euo pipefail

FIXTURE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$FIXTURE/../.." && pwd)"
SLUG="$(basename "$FIXTURE")"
DEST="$REPO/songs/$SLUG"
UPTO="${1:-20}"

mkdir -p "$REPO/songs"
rm -rf "$DEST"
mkdir -p "$DEST"

for stage in 00-registered 10-cleaned 20-lyrics; do
    [[ "${stage:0:2}" > "$UPTO" ]] && break
    [[ -d "$FIXTURE/$stage" ]] || continue
    cp -p "$FIXTURE/$stage/." "$DEST/" -r 2>/dev/null || cp -Rp "$FIXTURE/$stage/". "$DEST/"
    echo "applied $stage"
done

echo "songs/$SLUG ready (stage <= $UPTO)"
