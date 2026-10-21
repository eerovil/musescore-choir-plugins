"""Reading a band on eero-pc's GPU, and falling back to this host (#437).

eero-pc is faked: ``kubectl exec`` runs its script in a local shell, with the
pod's directories moved under tmp_path, and ``get pod`` answers from a little
state machine. So what is under test is this app's side — the order of the
calls, what survives a cut stream, when the band comes back here, and when the
card is given back — not the cluster.
"""

import json
import os
import stat
import subprocess
import threading
import time

import pytest

from src.song_app import heavy_slot, omr, remote_homr

PARSE = "<score-partwise><!-- read on {where} --></score-partwise>\n"


class FakeEeroPc:
    """``kubectl --context eero-pc -n homr ...``, as this test wants it to answer."""

    def __init__(self, tmp_path):
        self.pod = "missing"          # missing | ready | unschedulable
        self.schedulable = True
        self.calls = []
        self.deleted = 0
        self.fail_exec = lambda script: False   # True: this exec is "connection reset"
        self.down = False
        self.delete_fails = 0           # how many deletes fail before one works
        self.hang = lambda script: False   # True: this exec never answers
        self.before_get = lambda: None

    def __call__(self, args, *, input=None, timeout=None):
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


@pytest.fixture
def eero_pc(tmp_path, monkeypatch):
    """eero-pc with its homr venv built and verified, and this host's homr beside it."""
    fake = FakeEeroPc(tmp_path)
    keep = tmp_path / "pod-keep"
    monkeypatch.setattr(remote_homr, "KEEP", str(keep))
    monkeypatch.setattr(remote_homr, "JOBS", str(tmp_path / "pod-jobs"))
    monkeypatch.setattr(remote_homr, "_kubectl", fake)
    monkeypatch.setattr(remote_homr, "POLL_S", 0.01)
    monkeypatch.setattr(remote_homr, "COOLDOWN_S", 300.0)
    monkeypatch.setattr(remote_homr, "IDLE_S", 3600.0)
    monkeypatch.setattr(remote_homr, "_cool_until", 0.0)
    monkeypatch.setattr(remote_homr, "_bad", {})
    monkeypatch.setattr(remote_homr, "_uses", 0)
    monkeypatch.setenv("HOMR_KUBE_CONTEXT", "eero-pc")
    # The fallback runs unqueued: no AgentDeck to ask for this host's slot.
    monkeypatch.delenv("AGENTDECK_API_URL", raising=False)
    monkeypatch.delenv("AGENTDECK_URL", raising=False)
    monkeypatch.setattr(remote_homr.time, "sleep", lambda s: None)

    pins = remote_homr.Pins(venv=str(tmp_path / "local-venv"), python="3.12.13", uv="0.11.26",
                            freeze="", homr_commit="b89a164", ort_wheel="", ort_sha="",
                            ld_preload=b"", fingerprint="f00d")
    monkeypatch.setattr(remote_homr, "pins", lambda venv: pins)
    venv = keep / "venvs" / "f00d"
    # homr in the pod: python -c RUN <flags> <image>; writes <image>.musicxml beside it.
    _executable(venv / "bin" / "python",
                '#!/usr/bin/env bash\necho "reading on the pod"\n'
                'img="${!#}"; printf \'%s\' "' + PARSE.format(where="eero-pc").replace('"', '\\"')
                + '" > "${img%.*}.musicxml"\n')
    (venv / ".verified").write_text("")

    # This host's homr, which the fallback runs.
    local = tmp_path / "local-venv" / "bin" / "homr"
    _executable(local, '#!/usr/bin/env bash\nimg="${!#}"; printf \'%s\' "'
                + PARSE.format(where="bazzite").replace('"', '\\"') + '" > "${img%.*}.musicxml"\n')
    fake.engine = omr.Engine(key="default", label="installed", command=[str(local)],
                             default=True, gpu="force")
    return fake


def _page(tmp_path):
    image = tmp_path / "system-01.png"
    image.write_bytes(b"png bytes")
    return str(image)


def _read(tmp_path, engine, log=None):
    lines = []
    out = omr.read_page(_page(tmp_path), out_dir=str(tmp_path / "out"), engine=engine,
                        log=log or lines.append)
    with open(out) as f:
        return f.read(), lines


@pytest.fixture
def no_heavy_slot(monkeypatch):
    asked = []
    original = heavy_slot.heavy_slot

    def slot(label, **kw):
        asked.append(label)
        return original(label, **kw)
    monkeypatch.setattr(omr.heavy_slot, "heavy_slot", slot)
    return asked


def test_a_band_is_read_on_eero_pc_without_this_hosts_slot(tmp_path, eero_pc, no_heavy_slot):
    text, lines = _read(tmp_path, eero_pc.engine)
    assert "read on eero-pc" in text
    assert "homr-engine" in text        # stamped here like any other parse
    assert "reading on the pod" in lines
    assert no_heavy_slot == []
    assert not os.listdir(tmp_path / "pod-jobs")   # the job is cleared away


def test_a_cut_stream_costs_a_call_not_the_band(tmp_path, eero_pc, no_heavy_slot):
    n = {"exec": 0}

    def every_other(script):
        n["exec"] += 1
        return n["exec"] % 2 == 0       # "connection reset by peer" on every second call
    eero_pc.fail_exec = every_other
    text, _ = _read(tmp_path, eero_pc.engine)
    assert "read on eero-pc" in text
    assert no_heavy_slot == []


def test_starting_a_job_twice_starts_it_once(tmp_path, eero_pc):
    seen = []

    def start_twice(script):
        if "setsid sh run.sh" in script:
            seen.append(script)
            subprocess.run(["sh", "-c", script])    # ran, but the answer was lost
            return len(seen) == 1
        return False
    eero_pc.fail_exec = start_twice
    text, lines = _read(tmp_path, eero_pc.engine)
    assert len(seen) == 2
    assert lines.count("reading on the pod") == 1


def test_eero_pc_going_quiet_sends_the_band_back_here(tmp_path, eero_pc, no_heavy_slot,
                                                      monkeypatch):
    monkeypatch.setattr(remote_homr, "LOST_AFTER_S", 0.0)
    eero_pc.fail_exec = lambda script: "tail -c" in script   # every poll is cut
    text, lines = _read(tmp_path, eero_pc.engine)
    assert "read on bazzite" in text
    assert no_heavy_slot                    # read here under the slot, as before
    assert any("eero-pc did not read it" in line and "has not answered" in line
               for line in lines)


def test_a_busy_card_is_left_alone_and_the_pod_does_not_wait_for_it(
        tmp_path, eero_pc, no_heavy_slot):
    eero_pc.schedulable = False
    text, lines = _read(tmp_path, eero_pc.engine)
    assert "read on bazzite" in text
    assert eero_pc.deleted == 1             # a waiting pod would take the card later
    assert any("card is not free" in line for line in lines)
    # The next band does not ask eero-pc again for a while.
    before = len(eero_pc.calls)
    text, lines = _read(tmp_path, eero_pc.engine)
    assert "read on bazzite" in text
    assert len(eero_pc.calls) == before


def test_k3s_down_reads_here(tmp_path, eero_pc, no_heavy_slot):
    eero_pc.down = True
    text, lines = _read(tmp_path, eero_pc.engine)
    assert "read on bazzite" in text
    assert any("cannot reach eero-pc" in line for line in lines)


def test_homr_failing_on_eero_pc_is_read_again_here(tmp_path, eero_pc):
    venv = tmp_path / "pod-keep" / "venvs" / "f00d"
    _executable(venv / "bin" / "python", "#!/usr/bin/env bash\necho boom\nexit 3\n")
    text, lines = _read(tmp_path, eero_pc.engine)
    assert "read on bazzite" in text
    assert any("exited 3" in line for line in lines)


@pytest.mark.parametrize("engine", [
    omr.Engine(key="default", label="cpu", command=["homr"], default=True, gpu="no"),
    omr.Engine(key="branch", label="a branch", command=["python"], gpu="force"),
])
def test_only_the_installed_homr_on_the_gpu_goes_there(engine, monkeypatch):
    monkeypatch.setenv("HOMR_KUBE_CONTEXT", "eero-pc")
    assert not remote_homr.wanted(engine)


def test_off_without_a_context(monkeypatch):
    monkeypatch.setenv("HOMR_KUBE_CONTEXT", "")
    assert not remote_homr.wanted(
        omr.Engine(key="default", label="x", command=["homr"], default=True, gpu="force"))


def test_the_card_is_freed_when_idle_and_not_while_used(tmp_path, eero_pc, monkeypatch):
    monkeypatch.setattr(remote_homr, "IDLE_S", 0.05)
    _read(tmp_path, eero_pc.engine)
    deadline = time.monotonic() + 5
    while eero_pc.deleted == 0 and time.monotonic() < deadline:
        threading.Event().wait(0.01)
    assert eero_pc.deleted == 1
    assert eero_pc.pod == "missing"
    # A band in between keeps the pod: the timer from before it finds a newer use.
    eero_pc.deleted = 0
    remote_homr._stop_if_idle(seen=-1)
    assert eero_pc.deleted == 0


def test_a_venv_that_differs_from_this_hosts_is_never_used(tmp_path, eero_pc, monkeypatch):
    local = tmp_path / "local-venv" / "lib" / "python3.12" / "site-packages"
    remote = tmp_path / "pod-keep" / "venvs" / "f00d" / "lib" / "python3.12" / "site-packages"
    for site in (local, remote):
        (site / "homr").mkdir(parents=True)
        (site / "homr" / "main.py").write_text("print('read')\n")
        (site / "homr-0.7.dist-info").mkdir()
        (site / "homr-0.7.dist-info" / "RECORD").write_text(str(site))   # not compared
    for venv in (tmp_path / "local-venv", tmp_path / "pod-keep" / "venvs" / "f00d"):
        (venv / "bin" / "python").unlink(missing_ok=True)
        _executable(venv / "bin" / "python", "#!/bin/sh\necho 3.12.13\n")
    eero_pc.pod = "ready"
    p = remote_homr.pins("x")
    monkeypatch.setattr(remote_homr, "_sh", lambda script, **kw: subprocess.run(
        ["sh", "-c", script], capture_output=True, input=kw.get("input")).stdout)
    remote_homr._verify(p, lambda m: None)                 # the same: fine

    (remote / "homr" / "main.py").write_text("print('something else')\n")
    with pytest.raises(remote_homr.Unavailable, match="homr/main.py"):
        remote_homr._verify(p, lambda m: None)
    assert (tmp_path / "pod-keep" / "venvs" / "f00d" / ".mismatch").exists()
    (tmp_path / "pod-keep" / "venvs" / "f00d" / ".verified").unlink()
    with pytest.raises(remote_homr.Unavailable, match="does not match"):
        remote_homr._ensure_venv(p, lambda m: None)        # and it is not built again


def test_a_file_missing_on_eero_pc_fails_the_check(tmp_path, eero_pc, monkeypatch):
    local = tmp_path / "local-venv" / "lib" / "python3.12" / "site-packages"
    remote = tmp_path / "pod-keep" / "venvs" / "f00d" / "lib" / "python3.12" / "site-packages"
    for site in (local, remote):
        (site / "homr" / "transformer").mkdir(parents=True)
        (site / "homr" / "main.py").write_text("print('read')\n")
        (site / "homr" / "notes.py").write_text("NOTES = 1\n")
    # A model an older homr used, downloaded here only: named by its hash, so allowed.
    (local / "homr" / "transformer" / ("old-" + "a" * 40 + ".onnx")).write_bytes(b"w")
    for venv in (tmp_path / "local-venv", tmp_path / "pod-keep" / "venvs" / "f00d"):
        (venv / "bin" / "python").unlink(missing_ok=True)
        _executable(venv / "bin" / "python", "#!/bin/sh\necho 3.12.13\n")
    monkeypatch.setattr(remote_homr, "_sh", lambda script, **kw: subprocess.run(
        ["sh", "-c", script], capture_output=True, input=kw.get("input")).stdout)
    p = remote_homr.pins("x")
    remote_homr._verify(p, lambda m: None)

    (remote / "homr" / "notes.py").unlink()
    with pytest.raises(remote_homr.Unavailable, match="notes.py .missing there"):
        remote_homr._verify(p, lambda m: None)


def test_scans_waiting_for_eero_pc_do_not_ask_again_after_it_failed(tmp_path, eero_pc):
    """Two songs scanning at once: the second waits for the card while the first
    finds eero-pc down, and then reads here without asking eero-pc again."""
    eero_pc.down = True
    release = threading.Event()
    eero_pc.before_get = lambda: release.wait(5)
    results = []

    def scan(n):
        try:
            remote_homr.read(_page(tmp_path), [], str(tmp_path), "venv")
        except remote_homr.Unavailable as exc:
            results.append(str(exc))

    first = threading.Thread(target=scan, args=(1,))
    first.start()
    while not eero_pc.calls:
        threading.Event().wait(0.01)
    second = threading.Thread(target=scan, args=(2,))
    second.start()
    threading.Event().wait(0.2)     # the second is past its first check, waiting
    release.set()
    first.join(5)
    second.join(5)
    assert len(results) == 2
    assert len([c for c in eero_pc.calls if c[0] == "get"]) == 1
    assert any("not asking again" in r for r in results)


def test_a_hung_transfer_falls_back_in_bounded_time(tmp_path, eero_pc, monkeypatch,
                                                    no_heavy_slot):
    monkeypatch.setattr(remote_homr, "CALL_TIMEOUT_S", 0.2)
    monkeypatch.setattr(remote_homr, "LOST_AFTER_S", 0.3)
    eero_pc.pod = "ready"
    eero_pc.hang = lambda script: ".png" in script and "cat >" in script
    started = time.monotonic()
    text, lines = _read(tmp_path, eero_pc.engine)
    assert time.monotonic() - started < 3
    assert "read on bazzite" in text
    hung = [a for a in eero_pc.calls if a[0] == "exec" and ".png" in a[-1]]
    assert 1 <= len(hung) <= remote_homr.CALL_TRIES


def test_a_failed_delete_is_tried_again(tmp_path, eero_pc, monkeypatch):
    monkeypatch.setattr(remote_homr, "IDLE_S", 0.05)
    eero_pc.delete_fails = 2
    _read(tmp_path, eero_pc.engine)
    deadline = time.monotonic() + 5
    while eero_pc.deleted == 0 and time.monotonic() < deadline:
        threading.Event().wait(0.01)
    assert eero_pc.deleted == 1 and eero_pc.delete_fails == 0
    assert eero_pc.pod == "missing"


@pytest.mark.parametrize("age, exits", [("20 minutes ago", True), ("now", False)])
def test_the_pod_frees_the_card_when_nobody_calls(tmp_path, age, exits):
    """The pod's own command, with its 30 s check made quick: it exits once no
    call has touched the alive file for EXPIRE_MIN minutes."""
    command = next(line for line in remote_homr.POD_YAML.splitlines() if "command:" in line)
    script = json.loads(command.split("command:", 1)[1])[2]
    alive = tmp_path / "alive"
    script = script.replace(remote_homr.ALIVE, str(alive)).replace("sleep 30", "sleep 0.1")
    script = script.replace(f"touch {alive};", f"touch -d '{age}' {alive};")
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
    _read(tmp_path, eero_pc.engine)
    assert all(s.startswith(f"touch {remote_homr.ALIVE}") for s in eero_pc.execs())
