"""read_band.py against a fake eero-pc.

``kubectl exec`` runs its script in a local shell, with the pod's directories moved
under tmp_path, and ``get pod`` answers from a little state machine. What is under
test is this side: the order of the calls, what survives a cut stream, when a band is
given back to bazzite, and that the pod frees the card by itself.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time

import pytest

from src.kube_runner import runner as read_band  # noqa: E402

FLAGS = ["--gpu", "force", "--no-title"]


class FakeEeroPc:
    """``kubectl --context eero-pc -n homr ...``, as each test wants it to answer."""

    def __init__(self):
        self.pod = "missing"          # missing | ready | unschedulable | exited
        self.schedulable = True
        self.calls = []
        self.deleted = 0
        self.delete_fails = 0           # how many deletes fail before one works
        self.down = False
        self.fail_exec = lambda script: False   # True: "connection reset by peer"
        self.hang = lambda script: False        # True: this exec never answers
        self.before_get = lambda: None

    def __call__(self, args, *, input=None, timeout=None, namespace=None):
        if namespace is not None:    # only _free_card names one: the other kind's pod
            self.calls.append(["other"] + args)
            return subprocess.CompletedProcess(args, 0, b"", b"")
        self.calls.append(args)
        if self.down:
            return subprocess.CompletedProcess(args, 1, b"", b"Unable to connect to the server")
        verb = args[0]
        if verb == "exec" and self.hang(args[-1]):
            threading.Event().wait(timeout)        # what kubectl's own timeout ends
            return subprocess.CompletedProcess(args, 124, b"", b"kubectl timed out")
        if verb == "get":
            self.before_get()
            if self.pod == "missing":
                return subprocess.CompletedProcess(args, 1, b"", b'Error (NotFound): pods "reader"')
            if self.pod == "exited":
                body = {"metadata": {}, "status": {"phase": "Succeeded", "conditions": [
                    {"type": "Ready", "status": "False"}]}}
                return subprocess.CompletedProcess(args, 0, json.dumps(body).encode(), b"")
            cond = ([{"type": "Ready", "status": "True"}] if self.pod == "ready" else
                    [{"type": "PodScheduled", "status": "False",
                      "message": "0/1 nodes are available: 1 Insufficient nvidia.com/gpu."}])
            body = {"metadata": {}, "status": {"phase": "Running" if self.pod == "ready"
                                               else "Pending", "conditions": cond}}
            return subprocess.CompletedProcess(args, 0, json.dumps(body).encode(), b"")
        if verb == "apply":
            self.pod = "ready" if self.schedulable else "unschedulable"
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if verb == "delete":
            if self.delete_fails:
                self.delete_fails -= 1
                return subprocess.CompletedProcess(args, 1, b"", b"Unable to connect")
            self.deleted += 1
            self.pod = "missing"
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if verb == "exec":
            script = args[-1]
            if self.pod == "missing":
                return subprocess.CompletedProcess(
                    args, 1, b"", b'Error from server (NotFound): pods "reader" not found')
            if self.pod != "ready" or self.fail_exec(script):
                return subprocess.CompletedProcess(
                    args, 1, b"", b"error: connection reset by peer")
            return subprocess.run(["sh", "-c", script], input=input, capture_output=True,
                                  stdin=None if input is not None else subprocess.DEVNULL)
        raise AssertionError(f"unexpected kubectl call {args}")

    def execs(self):
        return [a[-1] for a in self.calls if a[0] == "exec"]


def _executable(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def pod_shell(tmp_path, monkeypatch):
    """The pod is Linux, and its scripts run here in this machine's shell. A Mac differs
    in two commands they use: put a stand-in for each on PATH, behaving as the pod's does."""
    shims = tmp_path / "shims"
    if shutil.which("setsid") is None:
        # a Mac has none: start the command in a session of its own, as setsid does,
        # so ending a job by its process group still reaches it
        _executable(shims / "setsid", f'#!/bin/sh\nexec "{sys.executable}" -c '
                    '"import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])" "$@"\n')
    real_base64 = shutil.which("base64")
    probe = tmp_path / "base64-probe"
    probe.write_bytes(b"x")
    if subprocess.run([real_base64, str(probe)], capture_output=True).stdout.strip() != b"eA==":
        # a Mac's base64 does not read a file named as its argument: hand it in on stdin
        _executable(shims / "base64", f'#!/bin/sh\n[ $# -eq 1 ] && [ -f "$1" ] && '
                    f'exec "{real_base64}" < "$1"\nexec "{real_base64}" "$@"\n')
    if shims.exists():
        monkeypatch.setenv("PATH", f"{shims}{os.pathsep}{os.environ['PATH']}")


@pytest.fixture
def eero_pc(tmp_path, monkeypatch):
    """eero-pc with its homr venv built and checked."""
    fake = FakeEeroPc()
    pod_shell(tmp_path, monkeypatch)
    keep = tmp_path / "pod-keep"
    # what use_kind() sets is put back after the test, and points under tmp_path
    for name in ("KIND", "NAMESPACE", "POD", "HOST_KEEP", "SYSLIB"):
        monkeypatch.setattr(read_band, name, getattr(read_band, name))
    monkeypatch.setitem(read_band.KINDS, "read-band", ("homr", "reader", str(keep), "/unused"))
    monkeypatch.setitem(read_band.KINDS, "sing-line", ("sing", "singer", str(tmp_path / "sing-keep"), "/unused"))
    monkeypatch.setattr(read_band, "KEEP", str(keep))
    monkeypatch.setattr(read_band, "JOBS", str(tmp_path / "pod-jobs"))
    monkeypatch.setattr(read_band, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(read_band, "_kubectl", fake)
    monkeypatch.setattr(read_band, "POLL_S", 0.01)
    monkeypatch.setattr(read_band.time, "sleep", lambda s: None)
    pins = read_band.Pins(venv=str(tmp_path / "local-venv"), python="3.12.13", uv="0.11.26",
                          freeze="", homr_commit="b89a164", ort_wheel="", ort_sha="",
                          ld_preload=b"", fingerprint="f00d")
    monkeypatch.setattr(read_band, "pins", lambda venv: pins)
    venv = keep / "venvs" / "f00d"
    # homr in the pod: python -c RUN <flags> <image>; writes <image>.musicxml beside it.
    _executable(venv / "bin" / "python",
                '#!/usr/bin/env bash\necho "reading on the pod"\n'
                'img="${!#}"; echo "<score-partwise/>" > "${img%.*}.musicxml"\n')
    (venv / ".verified").write_text("")
    return fake


def _page(tmp_path):
    image = tmp_path / "system-01.png"
    image.write_bytes(b"png bytes")
    return str(image)


def _read(tmp_path, flags=FLAGS):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    lines = []
    read_band.read(_page(tmp_path), flags, str(out), "venv", log=lines.append)
    return (out / "system-01.musicxml").read_text(), lines


def test_a_band_is_read_on_eero_pc(tmp_path, eero_pc):
    text, lines = _read(tmp_path)
    assert text == "<score-partwise/>\n"
    assert "reading on the pod" in lines
    assert not os.listdir(tmp_path / "pod-jobs")   # the job is cleared away


def test_main_writes_the_file_and_exits_0(tmp_path, eero_pc, capsys):
    out = tmp_path / "out"
    out.mkdir()
    assert read_band.main(["read-band", _page(tmp_path), str(out), "venv", *FLAGS]) == 0
    assert (out / "system-01.musicxml").exists()
    assert "reading on the pod" in capsys.readouterr().out


def test_main_says_why_and_exits_non_zero_when_eero_pc_cannot(tmp_path, eero_pc, capsys):
    eero_pc.down = True
    out = tmp_path / "out"
    out.mkdir()
    assert read_band.main(["read-band", _page(tmp_path), str(out), "venv", *FLAGS]) == 3
    assert "cannot reach the cluster" in capsys.readouterr().out.splitlines()[-1]
    assert not os.listdir(out)


def test_a_cpu_read_is_not_taken_to_the_card(tmp_path, eero_pc):
    with pytest.raises(read_band.Unavailable, match="GPU"):
        _read(tmp_path, ["--gpu", "no", "--no-title"])
    assert eero_pc.calls == []


def test_a_cut_stream_costs_a_call_not_the_band(tmp_path, eero_pc):
    n = {"exec": 0}

    def every_other(script):
        n["exec"] += 1
        return n["exec"] % 2 == 0       # "connection reset by peer" on every second call
    eero_pc.fail_exec = every_other
    text, _ = _read(tmp_path)
    assert text == "<score-partwise/>\n"


def test_starting_a_job_twice_starts_it_once(tmp_path, eero_pc):
    seen = []

    def start_twice(script):
        if "setsid sh run.sh" in script:
            seen.append(script)
            subprocess.run(["sh", "-c", script])    # ran, but the answer was lost
            return len(seen) == 1
        return False
    eero_pc.fail_exec = start_twice
    _, lines = _read(tmp_path)
    assert len(seen) == 2
    assert lines.count("reading on the pod") == 1


def test_eero_pc_going_quiet_gives_the_band_back(tmp_path, eero_pc, monkeypatch):
    monkeypatch.setattr(read_band, "LOST_AFTER_S", 0.0)
    eero_pc.fail_exec = lambda script: "tail -c" in script   # every poll is cut
    with pytest.raises(read_band.Unavailable, match="has not answered"):
        _read(tmp_path)


def test_a_busy_card_is_left_alone_and_the_pod_does_not_wait_for_it(tmp_path, eero_pc):
    eero_pc.schedulable = False
    with pytest.raises(read_band.Unavailable, match="card is not free"):
        _read(tmp_path)
    assert eero_pc.deleted == 1             # a waiting pod would take the card later
    # The next band, in another process, does not ask eero-pc again for a while.
    before = len(eero_pc.calls)
    with pytest.raises(read_band.Unavailable, match="not asking again"):
        _read(tmp_path)
    assert len(eero_pc.calls) == before


def test_homr_failing_on_eero_pc_gives_the_band_back(tmp_path, eero_pc):
    venv = tmp_path / "pod-keep" / "venvs" / "f00d"
    _executable(venv / "bin" / "python", "#!/usr/bin/env bash\necho boom\nexit 3\n")
    with pytest.raises(read_band.Unavailable, match="exited 3"):
        _read(tmp_path)


def test_scans_waiting_for_the_card_do_not_ask_again_after_it_failed(tmp_path, eero_pc):
    """Two songs scanning at once: the second waits for the card while the first
    finds eero-pc down, and then gives its band back without asking again."""
    eero_pc.down = True
    release = threading.Event()
    eero_pc.before_get = lambda: release.wait(5)
    results = []

    def scan():
        try:
            _read(tmp_path)
        except read_band.Unavailable as exc:
            results.append(str(exc))

    first = threading.Thread(target=scan)
    first.start()
    while not eero_pc.calls:
        threading.Event().wait(0.01)
    second = threading.Thread(target=scan)
    second.start()
    threading.Event().wait(0.2)     # the second is past its first check, waiting
    release.set()
    first.join(5)
    second.join(5)
    assert len(results) == 2
    assert len([c for c in eero_pc.calls if c[0] == "get"]) == 1
    assert any("not asking again" in r for r in results)


def test_a_hung_transfer_gives_up_in_bounded_time(tmp_path, eero_pc, monkeypatch):
    monkeypatch.setattr(read_band, "CALL_TIMEOUT_S", 0.2)
    monkeypatch.setattr(read_band, "LOST_AFTER_S", 0.3)
    eero_pc.pod = "ready"
    eero_pc.hang = lambda script: ".png" in script and "cat >" in script
    started = time.monotonic()
    with pytest.raises(read_band.Unavailable):
        _read(tmp_path)
    assert time.monotonic() - started < 3
    hung = [a for a in eero_pc.calls if a[0] == "exec" and ".png" in a[-1]]
    assert 1 <= len(hung) <= read_band.CALL_TRIES


@pytest.mark.parametrize("age, exits", [("5 minutes ago", True), ("now", False)])
def test_the_pod_frees_the_card_when_no_band_comes(tmp_path, age, exits):
    """The pod's own command, with its 30 s check made quick: it exits once no
    call has touched the alive file for IDLE_MIN minutes."""
    command = next(line for line in read_band._pod_yaml().splitlines() if "command:" in line)
    script = json.loads(command.split("command:", 1)[1])[2]
    alive = tmp_path / "alive"
    script = script.replace(read_band.ALIVE, str(alive)).replace("sleep 30", "sleep 0.1")
    # the alive file's age set here: a Mac's touch -d takes no '5 minutes ago'
    alive.touch()
    then = time.time() - {"5 minutes ago": 300, "now": 0}[age]
    os.utime(alive, (then, then))
    script = script.replace(f"touch {alive};", "")
    p = subprocess.Popen(["sh", "-c", script])
    try:
        p.wait(timeout=2)
        assert exits and p.returncode == 0
    except subprocess.TimeoutExpired:
        assert not exits
    finally:
        p.kill()
        p.wait()


def test_every_call_says_the_pod_is_still_wanted(tmp_path, eero_pc):
    _read(tmp_path)
    assert all(s.startswith(f"touch {read_band.ALIVE}") for s in eero_pc.execs())


def _two_venvs(tmp_path):
    local = tmp_path / "local-venv" / "lib" / "python3.12" / "site-packages"
    remote = tmp_path / "pod-keep" / "venvs" / "f00d" / "lib" / "python3.12" / "site-packages"
    for site in (local, remote):
        (site / "homr" / "transformer").mkdir(parents=True)
        (site / "homr" / "main.py").write_text("print('read')\n")
        (site / "homr" / "notes.py").write_text("NOTES = 1\n")
        (site / "homr-0.7.dist-info").mkdir()
        (site / "homr-0.7.dist-info" / "RECORD").write_text(str(site))   # not compared
    for venv in (tmp_path / "local-venv", tmp_path / "pod-keep" / "venvs" / "f00d"):
        (venv / "bin" / "python").unlink(missing_ok=True)
        _executable(venv / "bin" / "python", "#!/bin/sh\necho 3.12.13\n")
    return local, remote


@pytest.fixture
def local_sh(monkeypatch):
    monkeypatch.setattr(read_band, "_sh", lambda script, **kw: subprocess.run(
        ["sh", "-c", script], capture_output=True, input=kw.get("input")).stdout)


def test_a_venv_that_differs_from_bazzites_is_never_used(tmp_path, eero_pc, local_sh):
    _, remote = _two_venvs(tmp_path)
    p = read_band.pins("x")
    read_band._verify(p, lambda m: None)                 # the same: fine

    (remote / "homr" / "main.py").write_text("print('something else')\n")
    with pytest.raises(read_band.Unavailable, match="homr/main.py"):
        read_band._verify(p, lambda m: None)
    assert (tmp_path / "pod-keep" / "venvs" / "f00d" / ".mismatch").exists()
    (tmp_path / "pod-keep" / "venvs" / "f00d" / ".verified").unlink()
    with pytest.raises(read_band.Unavailable, match="does not match"):
        read_band._ensure_venv(p, lambda m: None)        # and it is not built again


def test_a_file_missing_on_eero_pc_fails_the_check(tmp_path, eero_pc, local_sh):
    local, remote = _two_venvs(tmp_path)
    # A model an older homr used, downloaded on bazzite only: named by its hash, so allowed.
    (local / "homr" / "transformer" / ("old-" + "a" * 40 + ".onnx")).write_bytes(b"w")
    p = read_band.pins("x")
    read_band._verify(p, lambda m: None)

    (remote / "homr" / "notes.py").unlink()
    with pytest.raises(read_band.Unavailable, match="notes.py .missing there"):
        read_band._verify(p, lambda m: None)


def test_a_pod_that_exited_when_idle_is_replaced_at_once(tmp_path, eero_pc):
    """The pod stopped itself after IDLE_MIN; the next band deletes it and starts
    another, without waiting for a deletion nobody asked for."""
    eero_pc.pod = "exited"
    text, _ = _read(tmp_path)
    assert text == "<score-partwise/>\n"
    verbs = [c[0] for c in eero_pc.calls]
    assert "wait" not in verbs
    assert verbs.index("delete") < verbs.index("apply")


def test_a_hung_wheel_transfer_gives_up_in_bounded_time(tmp_path, eero_pc, monkeypatch):
    """A big file goes in pieces, each its own bounded call: one that goes
    silent costs about LOST_AFTER_S, not the whole file's allowance."""
    monkeypatch.setattr(read_band, "CALL_TIMEOUT_S", 0.2)
    monkeypatch.setattr(read_band, "LOST_AFTER_S", 0.3)
    monkeypatch.setattr(read_band, "CHUNK", 1024)
    eero_pc.pod = "ready"
    eero_pc.hang = lambda script: ".part3" in script     # the fourth piece never answers
    started = time.monotonic()
    with pytest.raises(read_band.Unavailable):
        read_band._send(str(tmp_path / "wheel.whl"), b"w" * 10 * 1024)
    assert time.monotonic() - started < 3
    assert not (tmp_path / "wheel.whl").exists()


def test_a_big_file_arrives_whole(tmp_path, eero_pc, monkeypatch):
    monkeypatch.setattr(read_band, "CHUNK", 1000)
    eero_pc.pod = "ready"
    data = os.urandom(5500)
    n = {"exec": 0}

    def every_third(script):
        n["exec"] += 1
        return n["exec"] % 3 == 0       # cut streams along the way
    eero_pc.fail_exec = every_third
    read_band._send(str(tmp_path / "wheel.whl"), data)
    assert (tmp_path / "wheel.whl").read_bytes() == data
    assert not list(tmp_path.glob("wheel.whl.part*"))


def test_a_waiting_pod_that_could_not_be_deleted_is_deleted_by_the_next_band(
        tmp_path, eero_pc):
    eero_pc.schedulable = False
    eero_pc.delete_fails = 3            # every try of the first band fails
    with pytest.raises(read_band.Unavailable, match="card is not free"):
        _read(tmp_path)
    assert eero_pc.pod == "unschedulable"      # still there, waiting for the card
    assert os.path.exists(read_band._stray_marker())
    # The next band is in the cooldown, but deletes the waiting pod first.
    with pytest.raises(read_band.Unavailable, match="not asking again"):
        _read(tmp_path)
    assert eero_pc.pod == "missing"
    assert not os.path.exists(read_band._stray_marker())


def test_a_slow_wheel_build_keeps_the_pod_wanted(tmp_path, eero_pc, monkeypatch):
    monkeypatch.setattr(read_band, "ALIVE_EVERY_S", 0.02)
    eero_pc.pod = "ready"
    before = len(eero_pc.execs())
    assert read_band._keeping_alive(lambda: threading.Event().wait(0.3) or "wheel") == "wheel"
    pings = eero_pc.execs()[before:]
    assert len(pings) >= 3
    assert all(p.startswith(f"touch {read_band.ALIVE}") for p in pings)


def test_one_clock_for_every_try_of_a_band(tmp_path, eero_pc, monkeypatch):
    """A silent eero-pc costs LOST_AFTER_S in all, not a call's timeout plus
    more for its retries."""
    monkeypatch.setattr(read_band, "LOST_AFTER_S", 0.5)
    eero_pc.pod = "ready"
    eero_pc.hang = lambda script: True          # nothing answers from now on
    read_band._heard()
    started = time.monotonic()
    with pytest.raises(read_band.Unavailable):
        read_band._sh("true")
    assert time.monotonic() - started < 0.5 + 0.3
    with pytest.raises(read_band.Silent):        # and the next call has nothing left
        read_band._sh("true")


@pytest.mark.parametrize("name", ["system 01.png", "a;touch PWNED;b.png", "it's $(x).png"])
def test_odd_image_names_are_read_and_run_nothing(tmp_path, eero_pc, monkeypatch, name):
    monkeypatch.chdir(tmp_path)
    image = tmp_path / name
    image.write_bytes(b"png bytes")
    out = tmp_path / "out"
    out.mkdir()
    read_band.read(str(image), FLAGS, str(out), "venv", log=lambda m: None)
    assert (out / (name[:-4] + ".musicxml")).read_text() == "<score-partwise/>\n"
    assert not list(tmp_path.rglob("PWNED"))


def test_a_marked_pod_is_not_deleted_while_another_band_reads(tmp_path, eero_pc):
    """Clearing a pod an earlier band could not delete waits for the card lock,
    so it never deletes the pod a band is reading on."""
    eero_pc.pod = "ready"
    reading = threading.Event()
    go_on = threading.Event()
    venv = tmp_path / "pod-keep" / "venvs" / "f00d"
    _executable(venv / "bin" / "python",
                f'#!/usr/bin/env bash\ntouch {tmp_path}/reading; '
                f'while [ ! -f {tmp_path}/go ]; do sleep 0.01; done\n'
                'img="${!#}"; echo "<score-partwise/>" > "${img%.*}.musicxml"\n')
    results = []

    def first():
        results.append(_read(tmp_path)[0])

    a = threading.Thread(target=first)
    a.start()
    while not (tmp_path / "reading").exists():
        threading.Event().wait(0.01)
    # Meanwhile another band finds a marker an earlier failure left.
    os.makedirs(read_band.STATE_DIR, exist_ok=True)
    open(read_band._stray_marker(), "w").close()
    (tmp_path / "b").mkdir()
    b = threading.Thread(target=lambda: results.append(_read(tmp_path / "b")[0]))
    b.start()
    threading.Event().wait(0.3)
    assert eero_pc.deleted == 0                 # not while the first band reads
    (tmp_path / "go").write_text("")
    a.join(5)
    b.join(5)
    assert results == ["<score-partwise/>\n"] * 2
    assert eero_pc.deleted == 1                 # then, before the second band used a pod
    assert not os.path.exists(read_band._stray_marker())


def test_a_pod_deleted_while_a_band_is_followed_gives_the_band_back_at_once(
        tmp_path, eero_pc):
    venv = tmp_path / "pod-keep" / "venvs" / "f00d"
    _executable(venv / "bin" / "python",
                f'#!/usr/bin/env bash\ntouch {tmp_path}/reading; sleep 3\n')
    errors = []

    def band():
        try:
            _read(tmp_path)
        except read_band.Unavailable as exc:
            errors.append(exc)

    t = threading.Thread(target=band)
    t.start()
    while not (tmp_path / "reading").exists():
        threading.Event().wait(0.01)
    started = time.monotonic()
    eero_pc.pod = "missing"          # kubectl delete pod reader, by hand
    t.join(5)
    assert not t.is_alive()
    assert time.monotonic() - started < 2
    assert isinstance(errors[0], read_band.PodGone)
