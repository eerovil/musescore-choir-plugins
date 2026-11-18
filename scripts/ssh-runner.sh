#!/usr/bin/env bash
# REMOTE_RUNNER over plain SSH: sing lines on another machine you can ssh into (#483).
#
#   REMOTE_RUNNER=scripts/ssh-runner.sh       in .env, with:
#   SSH_RUNNER_HOST       the machine, as `ssh` knows it (user@host or a ~/.ssh/config name)
#   SSH_RUNNER_SING_VENV  the singer's venv there (default: install-singer.sh's default)
#   SSH_RUNNER_UFR_DIR    the voices there (default: install-singer.sh's default)
#   SSH_RUNNER_DIR        where a line's files wait there (default /tmp/choir-runner)
#   SSH_RUNNER_ENV        settings for the singer there only, e.g. "GPU=1" or "SING_PROVIDER=coreml"
#
# sing_song.py runs `<runner> sing-line <line.json> <out.wav> <voice>`. This copies the line
# and this checkout's sing_ufr.py over, sings it there, and copies the WAV back. Any failure
# exits non-zero, and sing_song.py then sings the line here and sends this runner no more.
# Stopped with SIGTERM, it ends the singer over there before it exits.
# Reading a scan band (`read-band`) is not supported: the scan reads it here.
set -uo pipefail

here=$(cd "$(dirname "$0")/.." && pwd)
singer="$here/src/sing_song/sing_ufr.py"

fail() { echo "ssh-runner: $1" >&2; exit "${2:-1}"; }

case "${1:-}" in
  sing-line) ;;
  read-band) fail "read-band is not supported by ssh-runner; the band is read here" 2 ;;
  *) fail "usage: $0 sing-line <line.json> <out.wav> <voice>" 2 ;;
esac
[ $# -eq 4 ] || fail "usage: $0 sing-line <line.json> <out.wav> <voice>" 2
line=$2 out=$3 voice=$4
[ -f "$line" ] || fail "no line file $line"
host=${SSH_RUNNER_HOST:-}
[ -n "$host" ] || fail "SSH_RUNNER_HOST is not set"
dir=${SSH_RUNNER_DIR:-/tmp/choir-runner}
venv=${SSH_RUNNER_SING_VENV:-"~/.local/share/musescore-choir-plugins/sing-venv"}
ufr=${SSH_RUNNER_UFR_DIR:-"~/.local/share/musescore-choir-plugins/ufr"}

# A path for the remote shell: quoted, with a leading ~/ left for that shell's $HOME.
rq() {
  case "$1" in
    "~") printf '"$HOME"' ;;
    "~/"*) printf '"$HOME"/%q' "${1#\~/}" ;;
    *) printf '%q' "$1" ;;
  esac
}

ssh_opts=(-o BatchMode=yes -o ConnectTimeout=20)
name="job-$$-$RANDOM$RANDOM"
job="$dir/$name"
rjob=$(rq "$job")

# The singer's settings this run was given (the song's language, any tuning), as
# sing_ufr.py reads them; not the voices' folder, the card or the provider, which are
# the other machine's (SSH_RUNNER_UFR_DIR, SSH_RUNNER_ENV).
sets=""
for k in $(grep -oE 'environ(\.get\(|\[)"[A-Z_]+"|"[A-Z_]+" (not )?in os\.environ' "$singer" \
           | grep -oE '"[A-Z_]+"' | tr -d '"' | sort -u); do
  case "$k" in UFR_DIR|DUMP_TOKENS|PRINT_DURS|GPU|SING_PROVIDER) continue ;; esac
  if [ -n "${!k+x}" ]; then sets+=" $k=$(printf '%q' "${!k}")"; fi
done

cleanup() { ssh "${ssh_opts[@]}" "$host" "rm -rf $rjob" >/dev/null 2>&1 || true; }
stop() {
  # SIGTERM from sing_song.py: the singer over there runs on without its ssh, so end it.
  [ -n "${pid:-}" ] && kill "$pid" 2>/dev/null
  ssh "${ssh_opts[@]}" "$host" "pkill -f $(printf '%q' "$name")" >/dev/null 2>&1 || true
  cleanup
  exit 143
}
trap stop TERM INT

ssh "${ssh_opts[@]}" "$host" "mkdir -p $rjob" || fail "cannot reach $host over ssh"
scp -q "${ssh_opts[@]}" "$line" "$host:$job/line.json" \
  && scp -q "${ssh_opts[@]}" "$singer" "$host:$job/sing_ufr.py" \
  || { cleanup; fail "cannot copy the line to $host"; }

echo "ssh-runner: singing on $host"
ssh "${ssh_opts[@]}" "$host" \
  "cd $rjob && env$sets UFR_DIR=$(rq "$ufr") ${SSH_RUNNER_ENV:-} nice -n 10 $(rq "$venv")/bin/python -I $rjob/sing_ufr.py $rjob/line.json $rjob/out.wav 100 $(printf '%q' "$voice")" &
pid=$!
wait "$pid"
code=$?
pid=""
if [ "$code" -ne 0 ]; then
  cleanup
  fail "the singer on $host stopped with exit $code"
fi
rm -f "$out.part"
if ! scp -q "${ssh_opts[@]}" "$host:$job/out.wav" "$out.part"; then
  rm -f "$out.part"
  cleanup
  fail "the singer on $host wrote no WAV"
fi
mv "$out.part" "$out"
cleanup
