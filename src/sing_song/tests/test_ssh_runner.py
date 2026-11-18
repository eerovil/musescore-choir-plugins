"""scripts/ssh-runner.sh: REMOTE_RUNNER over plain SSH (#483).

`ssh` and `scp` are stubs on PATH that run the "remote" side on this machine, so the
script's own steps are what is under test: the line and the singer go over, the WAV comes
back, the settings are the run's and the voices the other machine's, every failure exits
non-zero, and a stopped run ends the singer over there.
"""

import os
import signal
import subprocess
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
RUNNER = os.path.join(ROOT, "scripts", "ssh-runner.sh")

SSH = """#!/usr/bin/env bash
# skip options, then the host; the rest is the remote command
while [ "${1:-}" = "-o" ]; do shift 2; done
host=$1; shift
echo "$*" >> "$STUB_LOG"
[ -n "${SSH_DOWN:-}" ] && { echo "ssh: connect to host $host: No route to host" >&2; exit 255; }
# a real ssh carries none of this side's environment over
exec env -i PATH="$PATH" HOME="$HOME" STUB_LOG="$STUB_LOG" SING_SLEEP="${SING_SLEEP:-}" \
  SING_FAIL="${SING_FAIL:-}" SING_NO_WAV="${SING_NO_WAV:-}" bash -c "$*"
"""

SCP = """#!/usr/bin/env bash
while [ "${1#-}" != "$1" ]; do [ "$1" = "-o" ] && shift; shift; done
exec cp "${1#*:}" "${2#*:}"
"""


def _exe(path, text):
    path.write_text(text)
    path.chmod(0o755)


@pytest.fixture
def remote(tmp_path, monkeypatch):
    """A stub ssh/scp, a "remote" venv whose python records how it was called, and an env."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    _exe(bin_ / "ssh", SSH)
    _exe(bin_ / "scp", SCP)
    venv = tmp_path / "remote venv"
    (venv / "bin").mkdir(parents=True)
    _exe(venv / "bin" / "python", """#!/usr/bin/env bash
# -I sing_ufr.py line.json out.wav bpm voice
echo "args $*" >> "$STUB_LOG"
echo "env SONG_LANG=${SONG_LANG:-} UFR_DIR=$UFR_DIR GPU=${GPU:-} SING_PROVIDER=${SING_PROVIDER:-}" >> "$STUB_LOG"
[ -f "$2" ] || { echo "no singer copied" >&2; exit 1; }
[ -n "${SING_SLEEP:-}" ] && sleep "$SING_SLEEP"
[ -n "${SING_FAIL:-}" ] && { echo "acoustic model failed"; exit 1; }
[ -n "${SING_NO_WAV:-}" ] && exit 0
cp "$3" "$4"
""")
    log = tmp_path / "log"
    env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", STUB_LOG=str(log),
               SSH_RUNNER_HOST="singer.example", SSH_RUNNER_SING_VENV=str(venv),
               SSH_RUNNER_UFR_DIR="/srv/ufr", SSH_RUNNER_DIR=str(tmp_path / "remote dir"),
               SONG_LANG="en", UFR_DIR="/here/ufr", GPU="1", SING_PROVIDER="cuda")
    for k in ("SING_SLEEP", "SING_FAIL", "SING_NO_WAV", "SSH_DOWN", "SSH_RUNNER_ENV"):
        env.pop(k, None)
    line = tmp_path / "line.json"
    line.write_text('[{"pitch": 60}]')
    return {"env": env, "log": log, "line": str(line), "wav": str(tmp_path / "out.wav"),
            "dir": tmp_path / "remote dir"}


def _run(r, *args, **extra):
    args = args or ("sing-line", r["line"], r["wav"], "REMY")
    return subprocess.run(["bash", RUNNER, *args], env=dict(r["env"], **extra),
                          capture_output=True, text=True, timeout=60)


def test_a_line_is_sung_over_there_and_comes_back(remote):
    done = _run(remote)
    assert done.returncode == 0, done.stderr
    assert open(remote["wav"]).read() == '[{"pitch": 60}]'
    log = remote["log"].read_text()
    args = next(l for l in log.splitlines() if l.startswith("args ")).split()
    assert "/sing_ufr.py" in " ".join(args) and args[-2:] == ["100", "REMY"]
    # the run's language goes over; the voices, the card and the provider are the other machine's
    assert "env SONG_LANG=en UFR_DIR=/srv/ufr GPU= SING_PROVIDER=" in log
    assert not any(remote["dir"].iterdir())


def test_settings_for_the_other_machine_go_only_there(remote):
    assert _run(remote, SSH_RUNNER_ENV="SING_PROVIDER=coreml").returncode == 0
    assert "SING_PROVIDER=coreml" in remote["log"].read_text()


@pytest.mark.parametrize("extra, said", [
    ({"SING_FAIL": "1"}, "stopped with exit 1"),
    ({"SING_NO_WAV": "1"}, "wrote no WAV"),
    ({"SSH_DOWN": "1"}, "cannot reach singer.example"),
    ({"SSH_RUNNER_HOST": ""}, "SSH_RUNNER_HOST is not set"),
])
def test_every_failure_exits_non_zero_and_says_why(remote, extra, said):
    done = _run(remote, **extra)
    assert done.returncode != 0
    assert said in done.stderr
    assert not os.path.exists(remote["wav"])


def test_reading_a_band_is_refused_by_name(remote):
    done = _run(remote, "read-band", "page.png", "out", "venv")
    assert done.returncode == 2
    assert "not supported by ssh-runner" in done.stderr


def test_a_stopped_run_ends_the_singer_over_there(remote):
    p = subprocess.Popen(["bash", RUNNER, "sing-line", remote["line"], remote["wav"], "REMY"],
                         env=dict(remote["env"], SING_SLEEP="30"), text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.time() + 20
    while "args " not in (remote["log"].read_text() if remote["log"].exists() else ""):
        assert time.time() < deadline, "the singer never started"
        time.sleep(0.1)
    started = time.time()
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=20)
    assert p.returncode == 143
    assert time.time() - started < 15
    assert "pkill -f job-" in remote["log"].read_text()
    singers = subprocess.run(["pgrep", "-f", str(remote["dir"])], capture_output=True, text=True)
    assert singers.stdout.strip() == ""
    assert not os.path.exists(remote["wav"])
