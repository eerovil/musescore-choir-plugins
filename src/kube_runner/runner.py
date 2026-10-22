#!/usr/bin/env python3
"""The Kubernetes remote runner: heavy work on another machine's GPU, through kubectl.

The app and sing_song.py run ``REMOTE_RUNNER`` (in .env) before doing a heavy job here:

    runner.py read-band <image> <out dir> <homr venv> <homr flags...>
    runner.py sing-line <line.json> <out.wav> <voice>

Exit 0 means the job is done: ``<out dir>/<image name>.musicxml`` or ``<out.wav>`` is
written. Anything else, and the job runs on this host as without a runner; the last
line printed says why. Everything printed goes to the job's log. sing-line takes the
singer's settings (SONG_LANG, UFR_DIR, tuning) from the environment.

The cluster is ``REMOTE_KUBE_CONTEXT`` (kubectl's current context when unset); it
needs a node with an NVIDIA card and the ``nvidia`` runtime class. One pod per kind of
job (``homr/reader``, ``sing/singer``) keeps its venv on that node's disk, and exits by
itself, freeing the card, when no job has come for a couple of minutes. One job at a
time on the card, across every song. See docs/pdf-to-practice-track.md, "Where heavy
work runs".
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

Logger = Callable[[str], None]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))   # this checkout

#: Per kind of job: (namespace, pod, its directory in the pod, that directory on the GPU
#: machine's disk, which outlives the pod). use_kind() picks one for this run.
KINDS = {"read-band": ("homr", "reader", "/homr", "/var/tmp/homr-gpu"),
         "sing-line": ("sing", "singer", "/sing", "/var/tmp/sing-gpu")}
KIND = "read-band"
NAMESPACE = "homr"
POD = "reader"
#: python:3.12-slim, pinned: the system libraries opencv loads come with it.
IMAGE = ("python:3.12-slim@sha256:"
         "a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1")
#: On the GPU machine's disk (hostPath), so the venv outlives the pod.
KEEP = "/homr"
HOST_KEEP = "/var/tmp/homr-gpu"
SYSLIB = f"{KEEP}/syslib/usr/lib/x86_64-linux-gnu"
#: Where a band's job lives in the pod while it is read.
JOBS = "/tmp/homr-jobs"
RUN_HOMR = "from homr.main import main; main()"

#: After the GPU machine could not read, this host reads for this long without asking it.
COOLDOWN_S = float(os.getenv("REMOTE_KUBE_COOLDOWN_S", "300"))
#: The lock that keeps bands on the card one at a time, and the cooldown.
STATE_DIR = os.path.expanduser(os.getenv("HOMR_EERO_PC_STATE", "~/.cache/homr-the GPU machine"))
#: How long a new pod may take to be Ready (it is seconds when the card is free).
READY_WAIT_S = 90.0
#: A band goes back to this host when the GPU machine has not answered for this long.
LOST_AFTER_S = 60.0
#: One short call: send an image, start a job, poll, fetch.
CALL_TIMEOUT_S = 60.0
CALL_TRIES = 4
POLL_S = 1.0
POLL_TIMEOUT_S = 20.0
#: Building a venv: the wheels, the install (~1-2 min), the weights.
BUILD_TIMEOUT_S = 1800.0
#: The pod exits by itself, freeing the card, when no call has touched this
#: for IDLE_MIN minutes (within half a minute).
ALIVE = "/tmp/homr-alive"
IDLE_MIN = 2

#: Bumped when what the pod is built from changes in a way the pins do not say.
RECIPE = "1"


class Unavailable(RuntimeError):
    """the GPU machine cannot read this band; this host reads it."""


def _noop(_message: str) -> None:
    pass


def context() -> str:
    return (os.getenv("REMOTE_KUBE_CONTEXT") or "").strip()


def _kubectl_binary() -> str:
    configured = os.getenv("KUBECTL")
    if configured:
        return configured
    # song-app's service runs with a bare PATH; kubectl lives in ~/.local/bin.
    return shutil.which("kubectl") or os.path.expanduser("~/.local/bin/kubectl")


def _kubectl(args: List[str], *, input: Optional[bytes] = None,
             timeout: float = CALL_TIMEOUT_S,
             namespace: Optional[str] = None) -> subprocess.CompletedProcess:
    """One kubectl call against the GPU machine. The seam the tests replace."""
    where = ["--context", context()] if context() else []
    cmd = [_kubectl_binary(), *where, "-n", namespace or NAMESPACE] + args
    stdin = {"input": input} if input is not None else {"stdin": subprocess.DEVNULL}
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout,
                              start_new_session=True, **stdin)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, b"", b"kubectl timed out")
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 127, b"", str(exc).encode())


class Silent(Unavailable):
    """the GPU machine has not answered for LOST_AFTER_S."""


#: When the GPU machine last answered (or this host last finished work of its own). One
#: clock for every call, retry and step of a band: when it runs out, the band
#: goes back to this host, however the time was spent.
_heard_at = time.monotonic()


def _heard() -> None:
    global _heard_at
    _heard_at = time.monotonic()


class PodGone(Unavailable):
    """The pod went away under the band (deleted by hand, evicted)."""


def _call(args: List[str], *, input: Optional[bytes] = None,
          timeout: float = CALL_TIMEOUT_S,
          not_found_answers: bool = False) -> subprocess.CompletedProcess:
    """A kubectl call that may take no more of the clock than is left.

    A NotFound is an answer only where the caller asks whether the pod is
    there (``not_found_answers``); anywhere else it is not the GPU machine reading."""
    left = LOST_AFTER_S - (time.monotonic() - _heard_at)
    if left <= 0:
        raise Silent(f"the GPU machine has not answered for {LOST_AFTER_S:.0f}s")
    p = _kubectl(args, input=input, timeout=min(timeout, left))
    if p.returncode == 0 or (not_found_answers and b"NotFound" in (p.stderr or b"")):
        _heard()
    return p


def _err(p: subprocess.CompletedProcess) -> str:
    text = (p.stderr or b"").decode(errors="replace").strip() or f"exit {p.returncode}"
    return text.splitlines()[-1][:300]


def _sh(script: str, *, input: Optional[bytes] = None, timeout: Optional[float] = None,
        tries: int = CALL_TRIES) -> bytes:
    """Run a shell script in the pod, retried when the call fails.

    The scripts are written so that running one twice is the same as running it
    once, which is what makes a retry after a cut stream safe.

    Every try comes off the one clock (:func:`_call`): the GPU machine gets LOST_AFTER_S
    of silence per band, however many calls and tries it is spread over.
    Anything that runs longer than a call runs as a detached job
    (:func:`_start_job`), whose polls are answers.
    """
    if timeout is None:
        timeout = CALL_TIMEOUT_S
    script = f"touch {ALIVE} 2>/dev/null; {script}"
    last = ""
    for attempt in range(tries):
        if attempt:
            time.sleep(min(2 ** attempt, 8))
        # The script travels as an argument, any data on stdin.
        stdin = ["-i"] if input is not None else []
        p = _call(["exec", *stdin, POD, "--", "sh", "-c", script],
                  input=input, timeout=timeout)
        if p.returncode == 0:
            return p.stdout or b""
        if b"NotFound" in (p.stderr or b""):
            # No pod to try again in: give the band back now.
            raise PodGone(f"the pod on the GPU machine went away: {_err(p)}")
        last = _err(p)
    raise Unavailable(f"the GPU machine did not answer: {last}")


# --------------------------------------------------------------------------
# The pins: what this host reads with, and so what the GPU machine must read with
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Pins:
    venv: str
    python: str          # e.g. "3.12.13"
    uv: str              # e.g. "0.11.26"
    freeze: str          # this host's `uv pip freeze`
    homr_commit: str
    ort_wheel: str       # this host's path
    ort_sha: str
    ld_preload: bytes    # this host's onnxruntime/capi/_ld_preload.py
    fingerprint: str


def _uv() -> str:
    return shutil.which("uv") or os.path.expanduser("~/.local/bin/uv")


def _sha_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _site_packages(venv: str) -> str:
    import glob
    found = sorted(glob.glob(os.path.join(venv, "lib", "python3.*", "site-packages")))
    if not found:
        raise Unavailable(f"no site-packages in {venv}")
    return found[0]


def _marker(venv: str) -> Dict[str, str]:
    """homr-engine.txt, which song-app's scripts/install-homr.sh writes into the venv."""
    try:
        with open(os.path.join(venv, "homr-engine.txt"), encoding="utf-8") as f:
            return dict(line.split("=", 1) for line in f.read().splitlines() if "=" in line)
    except OSError:
        return {}


def _installed_commit(venv: str) -> str:
    import glob
    for info in sorted(glob.glob(os.path.join(_site_packages(venv), "homr-*.dist-info"))):
        try:
            with open(os.path.join(info, "direct_url.json"), encoding="utf-8") as f:
                return (json.load(f).get("vcs_info") or {}).get("commit_id", "")
        except (OSError, ValueError):
            continue
    return ""


def pins(venv: str) -> Pins:
    """this host's homr venv, as what the pod has to be built from."""
    cfg = {}
    try:
        with open(os.path.join(venv, "pyvenv.cfg"), encoding="utf-8") as f:
            cfg = dict(line.split(" = ", 1) for line in f.read().splitlines() if " = " in line)
    except OSError as exc:
        raise Unavailable(f"cannot read {venv}/pyvenv.cfg: {exc}") from exc
    marker = _marker(venv)
    ort = marker.get("ort_wheel", "")
    if not ort or not os.path.isfile(ort):
        raise Unavailable("this host's homr has no GPU onnxruntime wheel to copy "
                          f"({ort or 'none recorded'})")
    site = _site_packages(venv)
    preload = os.path.join(site, "onnxruntime", "capi", "_ld_preload.py")
    p = subprocess.run([_uv(), "pip", "freeze"], capture_output=True, text=True,
                       env={**os.environ, "VIRTUAL_ENV": venv}, timeout=60)
    if p.returncode != 0:
        raise Unavailable(f"uv pip freeze failed: {p.stderr.strip()[-300:]}")
    freeze = p.stdout
    commit = _installed_commit(venv)
    if not commit:
        raise Unavailable("cannot tell which homr commit is installed here")
    with open(preload, "rb") as f:
        ld_preload = f.read()
    ort_sha = _sha_file(ort)
    python, uv = cfg.get("version_info", "").strip(), cfg.get("uv", "").strip()
    if not python or not uv:
        raise Unavailable(f"{venv}/pyvenv.cfg does not say its python and uv versions")
    h = hashlib.sha256()
    for part in (RECIPE, IMAGE, python, uv, freeze, ort_sha):
        h.update(part.encode() + b"\0")
    h.update(ld_preload)
    result = Pins(venv=venv, python=python, uv=uv, freeze=freeze, homr_commit=commit,
                  ort_wheel=ort, ort_sha=ort_sha, ld_preload=ld_preload,
                  fingerprint=h.hexdigest()[:16])
    return result


def _venv_dir(p: Pins) -> str:
    return f"{KEEP}/venvs/{p.fingerprint}"


# --------------------------------------------------------------------------
# The pod
# --------------------------------------------------------------------------
def _pod_yaml() -> str:
    return f"""\
apiVersion: v1
kind: Namespace
metadata: {{name: {NAMESPACE}}}
---
apiVersion: v1
kind: Pod
metadata: {{name: {POD}, namespace: {NAMESPACE}, labels: {{app: musescore-choir-{NAMESPACE}}}}}
spec:
  restartPolicy: Never
  runtimeClassName: nvidia          # without it the card is not passed in
  terminationGracePeriodSeconds: 5
  containers:
  - name: {POD}
    image: {IMAGE}
    # Exits, and frees the card, when no job has come for {IDLE_MIN} minutes.
    command: ["sh", "-c", "trap 'exit 0' TERM; touch {ALIVE}; while sleep 30 & wait $!; do [ -n \\"$(find {ALIVE} -mmin -{IDLE_MIN})\\" ] || exit 0; done"]
    resources:
      limits: {{nvidia.com/gpu: 1}}   # the card, or the pod does not start
    volumeMounts:
    - {{name: keep, mountPath: {KEEP}}}
  volumes:
  - name: keep
    hostPath: {{path: {HOST_KEEP}, type: DirectoryOrCreate}}
"""

def _pod_status() -> Tuple[str, dict]:
    """("missing" | "terminating" | "exited" | "ready" | "waiting" | "unschedulable", details)."""
    import json
    p = _call(["get", "pod", POD, "-o", "json", "--request-timeout=15s"], timeout=30,
              not_found_answers=True)
    if p.returncode != 0:
        if b"NotFound" in (p.stderr or b""):
            return "missing", {}
        raise Unavailable(f"cannot reach the cluster: {_err(p)}")
    pod = json.loads(p.stdout or b"{}")
    if pod.get("metadata", {}).get("deletionTimestamp"):
        return "terminating", pod
    status = pod.get("status", {})
    if status.get("phase") in ("Succeeded", "Failed"):
        return "exited", pod   # it stopped after IDLE_MIN; nothing deletes it
    for c in status.get("conditions") or []:
        if c.get("type") == "PodScheduled" and c.get("status") == "False":
            return "unschedulable", c
        if c.get("type") == "Ready" and c.get("status") == "True":
            return "ready", pod
    return "waiting", pod


def _delete_pod(wait: bool = False, clocked: bool = False) -> bool:
    """Delete the pod; False when kubectl could not.

    Clearing up after a failure is not on the band's clock (it has to be tried
    even when the GPU machine went quiet); deleting on the way to a read is."""
    args = ["delete", "pod", POD, "--ignore-not-found", f"--wait={'true' if wait else 'false'}"]
    call = _call if clocked else _kubectl
    return call(args, timeout=90 if wait else 30).returncode == 0


def _stray_marker() -> str:
    return os.path.join(STATE_DIR, f"{KIND}-waiting-pod")


def _free_card() -> None:
    """The other kind's pod may still hold the card, idle until it exits by itself. This
    run holds the card lock, so no job is using it: delete it now (best effort)."""
    for kind, (namespace, pod, _keep, _host) in KINDS.items():
        if kind != KIND:
            _kubectl(["delete", "pod", pod, "--ignore-not-found", "--wait=true"],
                     timeout=90, namespace=namespace)


def _remove_waiting_pod() -> None:
    """Delete a pod that is waiting for the card, or remember to: the next band
    tries again before anything else (:func:`_remove_stray_pod`). If it is given
    the card meanwhile it starts, finds no band and exits after IDLE_MIN."""
    for attempt in range(3):
        if attempt:
            time.sleep(2)
        if _delete_pod():
            _clear_stray()
            return
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(_stray_marker(), "w") as f:
        f.write(str(time.time()))


def _clear_stray() -> None:
    try:
        os.remove(_stray_marker())
    except FileNotFoundError:
        pass


def _remove_stray_pod() -> None:
    """A pod an earlier band could not delete: delete it before anything uses a
    pod. Called with the card lock held, so it never deletes a pod another band
    is reading on; when it still cannot be deleted, no pod is used."""
    if not os.path.exists(_stray_marker()):
        return
    if not _delete_pod(clocked=True):
        raise Unavailable("a pod left waiting for the card could not be deleted")
    _clear_stray()


def _start_pod(log: Logger) -> None:
    state, _ = _pod_status()
    if state == "ready":
        return
    if state == "exited":
        # The container has stopped, so this is quick; waiting for a deletion
        # nobody asked for would cost the band a minute.
        if not _delete_pod(wait=True, clocked=True):
            _call(["delete", "pod", POD, "--grace-period=0", "--force"], timeout=30)
        state = "missing"
    if state == "terminating":
        # One left over from the last run, still going: let it go first, or the
        # apply below changes nothing and the old one is exec'd into.
        if _call(["wait", "--for=delete", f"pod/{POD}", "--timeout=60s"],
                    timeout=90).returncode != 0:
            _call(["delete", "pod", POD, "--grace-period=0", "--force"], timeout=30)
        state = "missing"
    if state == "missing":
        _free_card()
        log(f"the GPU machine: starting the {NAMESPACE} pod")
        p = _call(["apply", "-f", "-"], input=_pod_yaml().encode(), timeout=60)
        if p.returncode != 0:
            raise Unavailable(f"could not start the pod: {_err(p)}")
    deadline = time.monotonic() + READY_WAIT_S
    while True:
        state, detail = _pod_status()
        if state == "ready":
            return
        if state == "unschedulable" or time.monotonic() >= deadline:
            why = detail.get("message") if state == "unschedulable" else \
                "it did not start in time"
            # A pod left waiting would take the card later, from whoever has it.
            _remove_waiting_pod()
            raise Unavailable(f"the GPU machine's card is not free ({why})")
        time.sleep(2)


# --------------------------------------------------------------------------
# The venv
# --------------------------------------------------------------------------
_BOOTSTRAP = """\
set -e
if [ ! -f {keep}/syslib/.complete ]; then
    mkdir -p {keep}/apt {keep}/syslib.new
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends --download-only \\
        -o Dir::Cache::archives={keep}/apt libgl1 libglib2.0-0 >/dev/null
    for d in {keep}/apt/*.deb; do dpkg -x "$d" {keep}/syslib.new; done
    rm -rf {keep}/syslib && mv {keep}/syslib.new {keep}/syslib
    touch {keep}/syslib/.complete
fi
if [ "$({keep}/tools/bin/uv --version 2>/dev/null)" != "uv {uv}" ]; then
    rm -rf {keep}/tools
    python -m venv {keep}/tools
    {keep}/tools/bin/pip install -q --disable-pip-version-check "uv=={uv}"
fi
UV_PYTHON_INSTALL_DIR={keep}/python {keep}/tools/bin/uv python install -q {python}
mkdir -p {keep}/wheels {keep}/venvs
"""

_INSTALL = """\
set -e
export UV_PYTHON_INSTALL_DIR={keep}/python UV_CACHE_DIR={keep}/uv-cache
V={venv}
rm -rf "$V"
{keep}/tools/bin/uv venv -q --python {python} "$V"
VIRTUAL_ENV="$V" {keep}/tools/bin/uv pip install -q --no-deps -r {keep}/wheels/{fp}.freeze
# Both opencv packages write cv2/; on this host opencv-python's is the one there.
{reinstall}
cp {keep}/wheels/{fp}.ld_preload.py "$V"/lib/python3.*/site-packages/onnxruntime/capi/_ld_preload.py
"$V"/bin/python {keep}/wheels/clear_execstack.py "$V"/lib/python3.*/site-packages/onnxruntime/capi/*.so
LD_LIBRARY_PATH={syslib} "$V"/bin/python -c '{run}' --init --gpu force >/dev/null 2>&1
"""

#: Files that say how a package was installed rather than what it is.
_NOT_COMPARED = ("RECORD", "INSTALLER", "REQUESTED", "direct_url.json", "WHEEL",
                 "uv_build.json", "uv_cache.json")

_MANIFEST = """\
cd "$(ls -d {venv}/lib/python3.*/site-packages)" && find . -type f \
 ! -path '*/__pycache__/*' ! -name '*.pyc' ! -name '*.pth' {skip} -print0 \
 | LC_ALL=C sort -z | xargs -0 sha256sum
"""


def _manifest_skip() -> str:
    return " ".join(f"! -name {shlex.quote(n)}" for n in _NOT_COMPARED)


def _local_manifest(venv: str) -> Dict[str, str]:
    site = _site_packages(venv)
    out: Dict[str, str] = {}
    for root, dirs, files in os.walk(site):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if name.endswith((".pyc", ".pth")) or name in _NOT_COMPARED:
                continue
            path = os.path.join(root, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            out["./" + os.path.relpath(path, site)] = _sha_file(path)
    return out


def _parse_manifest(text: str) -> Dict[str, str]:
    out = {}
    for line in text.splitlines():
        if "  " in line:
            sha, path = line.split("  ", 1)
            out[path] = sha
    return out


def _build_homr_wheel(p: Pins, into: str, log: Logger) -> str:
    """homr's wheel at the installed commit, built here (the pod has no git)."""
    repo = os.getenv("HOMR_REPO") or "https://github.com/eerovil/homr.git"
    src = os.path.join(into, "homr-src")
    log(f"the GPU machine: building homr {p.homr_commit[:7]} into a wheel")
    for cmd in (["git", "clone", "-q", repo, src],
                ["git", "-C", src, "checkout", "-q", p.homr_commit],
                [_uv(), "build", "--wheel", "-q", "-o", os.path.join(into, "dist"), src]):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise Unavailable(f"could not build homr's wheel ({cmd[0]}): "
                              f"{(r.stderr or r.stdout).strip()[-300:]}")
    wheels = [w for w in os.listdir(os.path.join(into, "dist")) if w.endswith(".whl")]
    if len(wheels) != 1:
        raise Unavailable(f"building homr's wheel gave {wheels}")
    return os.path.join(into, "dist", wheels[0])


#: A file is sent in pieces of this size, each its own call: a call that goes
#: silent is then given up on in about LOST_AFTER_S, however big the file.
CHUNK = 4 << 20


#: How often a long step here tells the pod it is still wanted (IDLE_MIN is 2 min).
ALIVE_EVERY_S = 30.0


def _keeping_alive(work):
    """Run ``work()`` here while touching the pod's alive file every ALIVE_EVERY_S."""
    import threading
    done = threading.Event()

    def ping():
        while not done.wait(ALIVE_EVERY_S):
            try:
                _sh("true", tries=1)
            except Unavailable:
                pass
    pinger = threading.Thread(target=ping, daemon=True)
    pinger.start()
    try:
        return work()
    finally:
        done.set()
        pinger.join(5)


def _send(path_in_pod: str, data: bytes) -> None:
    q = shlex.quote(path_in_pod)
    sha = hashlib.sha256(data).hexdigest()
    pieces = max(1, -(-len(data) // CHUNK))
    for i in range(pieces):
        # Each piece is its own file, so sending one again is the same as once.
        _sh(f"cat > {q}.part{i}", input=data[i * CHUNK:(i + 1) * CHUNK])
    parts = " ".join(f"{q}.part{i}" for i in range(pieces))
    _sh(f"cat {parts} > {q}.new && rm -f {parts} && "
        f"[ \"$(sha256sum < {q}.new | cut -c1-64)\" = {sha} ] && mv {q}.new {q}")


def _build(p: Pins, log: Logger) -> None:
    venv = _venv_dir(p)
    log("the GPU machine: building its homr venv to match this host's (once per homr "
        "install, a few minutes)")
    _detached(_BOOTSTRAP.format(keep=KEEP, uv=p.uv, python=p.python), "the setup")
    with tempfile.TemporaryDirectory(prefix="homr-wheel-") as tmp:
        # A cold clone and build can outlast IDLE_MIN; keep the pod wanted meanwhile.
        wheel = _keeping_alive(lambda: _build_homr_wheel(p, tmp, log))
        _heard()   # that was this host's time, not the GPU machine's silence
        with open(wheel, "rb") as f:
            _send(f"{KEEP}/wheels/{os.path.basename(wheel)}", f.read())
        homr_line = f"homr @ file://{KEEP}/wheels/{os.path.basename(wheel)}"
    ort_name = os.path.basename(p.ort_wheel)
    have = _sh(f"sha256sum {KEEP}/wheels/{ort_name} 2>/dev/null | cut -c1-64; true")
    if have.decode().strip() != p.ort_sha:
        log("the GPU machine: copying the GPU onnxruntime wheel")
        with open(p.ort_wheel, "rb") as f:
            _send(f"{KEEP}/wheels/{ort_name}", f.read())
    freeze, reinstall = [], ""
    for line in p.freeze.splitlines():
        name = line.split("@")[0].split("==")[0].strip().lower()
        if name == "homr":
            line = homr_line
        elif name == "onnxruntime-gpu":
            line = f"onnxruntime-gpu @ file://{KEEP}/wheels/{ort_name}"
        elif name == "opencv-python" and "==" in line:
            reinstall = (f'VIRTUAL_ENV="$V" {KEEP}/tools/bin/uv pip install -q --no-deps '
                         f'--reinstall {shlex.quote(line.strip())}')
        freeze.append(line)
    _send(f"{KEEP}/wheels/{p.fingerprint}.freeze", ("\n".join(freeze) + "\n").encode())
    _send(f"{KEEP}/wheels/{p.fingerprint}.ld_preload.py", p.ld_preload)
    # The same patch install-homr.sh applies on this host; the check below proves it.
    with open(os.path.join(ROOT, "scripts", "onnxruntime-sm52", "clear_execstack.py"),
              "rb") as f:
        _send(f"{KEEP}/wheels/clear_execstack.py", f.read())
    log("the GPU machine: installing (a minute or two)")
    _detached(_INSTALL.format(keep=KEEP, venv=venv, python=p.python, fp=p.fingerprint,
                              reinstall=reinstall, syslib=SYSLIB, run=RUN_HOMR),
              "the install")
    _verify(p, log)


def _detached(script: str, what: str) -> None:
    """Run a long setup step like a band: detached, followed by short calls, so a
    cut stream does not stop it and a silent the GPU machine is given up on in
    LOST_AFTER_S however long the step itself may run."""
    job = _new_job()
    try:
        _start_job(job, script)
        _follow(job, what, _noop, BUILD_TIMEOUT_S)
    finally:
        _end_job(job)


def _verify(p: Pins, log: Logger) -> None:
    """Every file the GPU machine's homr reads with is this host's, byte for byte."""
    venv = _venv_dir(p)
    log("the GPU machine: checking its homr file by file against this host's")
    version = _sh(f"{venv}/bin/python -c 'import sys; print(sys.version)'").decode().strip()
    # Hashing ~3 GB takes ~10 s there.
    remote = _parse_manifest(_sh(_MANIFEST.format(venv=venv, skip=_manifest_skip()),
                                 timeout=180).decode())
    local = _local_manifest(p.venv)
    _heard()
    local_version = subprocess.run(
        [os.path.join(p.venv, "bin", "python"), "-c", "import sys; print(sys.version)"],
        capture_output=True, text=True, timeout=60).stdout.strip()
    differ = sorted(path for path, sha in remote.items() if local.get(path) != sha)
    # And nothing of this host's may be missing there, except a model file this
    # host downloaded for an older homr: its name carries its content hash, so a
    # homr that wanted it would fetch exactly that file.
    differ += sorted(f"{path} (missing there)" for path in local
                     if path not in remote and not _spare_weights(path))
    if version != local_version:
        differ.insert(0, f"python {version!r} against {local_version!r}")
    if not remote:
        differ.append("no files at all")
    if differ:
        why = (f"{len(differ)} file(s) differ from this host's homr, e.g. "
               + ", ".join(differ[:3]))
        _sh(f"printf '%s\\n' {shlex.quote(why)} > {venv}/.mismatch")
        raise Unavailable(why)
    _sh(f"touch {venv}/.verified && cd {KEEP}/venvs && for d in *; do "
        f"[ \"$d\" = {p.fingerprint} ] || rm -rf \"$d\"; done")
    log(f"the GPU machine: its homr matches this host's ({len(remote)} files)")


def _spare_weights(path: str) -> bool:
    import re
    return bool(re.search(r"-[0-9a-f]{40}(_fp16)?\.onnx$", path))


def _ensure_venv(p: Pins, log: Logger) -> None:
    venv = _venv_dir(p)
    state = _sh(f"if [ -f {venv}/.verified ]; then echo ok; "
                f"elif [ -f {venv}/.mismatch ]; then cat {venv}/.mismatch; else echo none; fi")
    state = state.decode().strip()
    if state == "ok":
        return
    if state != "none":
        raise Unavailable(f"the GPU machine's homr does not match this host's: {state}")
    _build(p, log)


# --------------------------------------------------------------------------
# A band
# --------------------------------------------------------------------------
def _cooling() -> Optional[str]:
    try:
        with open(os.path.join(STATE_DIR, f"{KIND}-cooldown.json"), encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        return None
    return state.get("reason", "") if time.time() < state.get("until", 0) else None


def _cool(reason: str) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, f"{KIND}-cooldown.json")
    with open(path + ".part", "w", encoding="utf-8") as f:
        json.dump({"until": time.time() + COOLDOWN_S, "reason": reason}, f)
    os.replace(path + ".part", path)


def read(image_path: str, flags: List[str], out_dir: str, venv: str,
         log: Logger = _noop, timeout: float = 600) -> List[str]:
    """Read ``image_path`` on the GPU machine with homr ``flags``; the MusicXML lands in
    ``out_dir``. Returns homr's lines.

    Raises :class:`Unavailable` whenever the GPU machine did not read it, so song-app
    reads it on this host instead.
    """
    if "--gpu" not in flags or flags[flags.index("--gpu") + 1:][:1] != ["force"]:
        # this host reads on its CPU then, and the card would not read the same.
        raise Unavailable("this host's homr does not read on the GPU")
    _not_cooling()
    p = pins(venv)

    def work():
        _ensure_venv(p, log)
        return _read_band(p, image_path, flags, out_dir, log, timeout)
    return _on_card(work, log)


def _not_cooling() -> None:
    cooling = _cooling()
    if cooling and not os.path.exists(_stray_marker()):
        raise Unavailable(f"{cooling} (not asking again for a few minutes)")


def _on_card(work: Callable[[], List[str]], log: Logger) -> List[str]:
    """Run ``work()`` in a ready pod, holding the card lock; a failure cools this kind off."""
    os.makedirs(STATE_DIR, exist_ok=True)
    # One job on the card at a time, across every song.
    with open(os.path.join(STATE_DIR, "card.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _heard()   # waiting for the card was not the GPU machine's silence
        # A waiting pod an earlier job left goes first, cooldown or not.
        try:
            _remove_stray_pod()
        except Unavailable as exc:
            _cool(str(exc))
            raise
        # Jobs waiting here while another one failed must not ask again.
        cooling = _cooling()
        if cooling:
            raise Unavailable(f"{cooling} (not asking again for a few minutes)")
        try:
            _start_pod(log)
            return work()
        except Unavailable as exc:
            _cool(str(exc))
            raise


def _read_band(p: Pins, image_path: str, flags: List[str], scratch: str,
               log: Logger, timeout: float) -> List[str]:
    name = os.path.basename(image_path)
    base = os.path.splitext(name)[0]
    job = _new_job()
    try:
        with open(image_path, "rb") as f:
            _sh(f"mkdir -p {job} && cat > {job}/{shlex.quote(name)}", input=f.read())
        argv = " ".join(shlex.quote(a) for a in
                        [f"{_venv_dir(p)}/bin/python", "-c", RUN_HOMR] + flags
                        + [f"{job}/{name}"])
        _start_job(job, f"LD_LIBRARY_PATH={SYSLIB} {argv}\n")
        log(f"Reading {name} with homr on the GPU machine's GPU")
        lines = _follow(job, "homr", log, timeout)
        produced = shlex.quote(f"{job}/{base}.musicxml")
        out = _sh(f"if [ -f {produced} ]; then sha256sum < {produced} | cut -c1-64; "
                  f"base64 {produced}; else echo none; fi").decode()
        head, _, body = out.partition("\n")
        if head.strip() == "none":
            raise Unavailable("homr on the GPU machine wrote no MusicXML: "
                              + " | ".join(lines[-3:]))
        data = base64.b64decode(body)
        if hashlib.sha256(data).hexdigest() != head.strip():
            raise Unavailable("the MusicXML from the GPU machine arrived damaged")
        with open(os.path.join(scratch, base + ".musicxml"), "wb") as f:
            f.write(data)
        return lines
    finally:
        _end_job(job)


def _new_job() -> str:
    return f"{JOBS}/{uuid.uuid4().hex}"


def _start_job(job: str, script: str) -> None:
    """Run ``script`` in the pod detached from any call, so a cut stream does not
    stop it. Starting it twice (a retry whose first answer was lost) runs it once."""
    run = (f"mkdir {job}/started 2>/dev/null || exit 0\n"
           f"cd {job}\necho $$ > pid\n"
           f"sh job.sh > log 2>&1\n"
           f"echo $? > exit.part && mv exit.part exit\n")
    _sh(f"mkdir -p {job} && cat > {job}/job.sh", input=script.encode())
    _sh(f"cat > {job}/run.sh", input=run.encode())
    _sh(f"cd {job}; setsid sh run.sh </dev/null >/dev/null 2>&1 & echo started")


def _end_job(job: str) -> None:
    """Stop the job if it still runs, and clear it away."""
    try:
        _sh(f"[ -f {job}/pid ] && kill -9 -$(cat {job}/pid) 2>/dev/null; "
            f"rm -rf {job}", tries=2)
    except Unavailable:
        pass


def _follow(job: str, what: str, log: Logger, timeout: float) -> List[str]:
    """Stream the job's log until it exits; short calls, each one retried."""
    offset, partial, lines = 0, b"", []
    started = time.monotonic()
    while True:
        if time.monotonic() - started > timeout:
            raise Unavailable(f"{what} on the GPU machine did not finish within {timeout:.0f}s")
        try:
            out = _sh(f"cat {job}/exit 2>/dev/null || echo -; "
                      f"tail -c +{offset + 1} {job}/log 2>/dev/null; true",
                      tries=1, timeout=POLL_TIMEOUT_S)
        except (Silent, PodGone):
            raise
        except Unavailable:
            time.sleep(POLL_S)      # a cut call; the clock decides when to stop
            continue
        status, _, text = out.partition(b"\n")
        offset += len(text)
        chunks = (partial + text).split(b"\n")
        partial = chunks.pop()
        for raw in chunks:
            line = raw.decode(errors="replace").rstrip("\r")
            if line:
                lines.append(line)
                log(line)
        status = status.strip()
        if status != b"-":
            if partial.strip():
                lines.append(partial.decode(errors="replace"))
                log(lines[-1])
            if status != b"0":
                raise Unavailable(f"{what} on the GPU machine exited {status.decode()}: "
                                  + " | ".join(lines[-3:]))
            return lines
        time.sleep(POLL_S)


# --------------------------------------------------------------------------
# A line to sing
# --------------------------------------------------------------------------
#: The singer this checkout sings with: the pod sings with the same file.
SINGER = os.path.join(ROOT, "src", "sing_song", "sing_ufr.py")
#: scripts/install-singer.sh's packages for an NVIDIA card (CUDA 11: old cards too).
SING_PACKAGES = ("onnxruntime-gpu==1.17.1 numpy==1.26.4 soundfile==0.14.0 nvidia-cudnn-cu11==8.9.6.50 "
                 "nvidia-cuda-runtime-cu11==11.8.89 nvidia-cuda-nvrtc-cu11==11.8.89 "
                 "nvidia-cublas-cu11==11.11.3.6 nvidia-cufft-cu11==10.9.0.58 nvidia-curand-cu11==10.3.0.86")
#: The chunks sing_ufr.py works in on a 4 GB card, as sing_song.py sings on one here.
GPU_ENV = {"GPU": "1", "VOC_CHUNK": "1500", "AC_CHUNK": "6000"}
#: The UFR voice folders the singer reads.
VOICE_DIRS = ("core", "dsdur", "tiger")
LINE_TIMEOUT_S = 600.0


def _singer_env() -> Dict[str, str]:
    """The settings sing_ufr.py reads, as this run was given them (the song's language, any
    tuning), on the card, with the pod's own voice files."""
    with open(SINGER, encoding="utf-8") as f:
        reads = set(re.findall(r"environ(?:\.get\(|\[)\"(\w+)\"|\"(\w+)\" (?:not )?in os\.environ", f.read()))
    reads = {a or b for a, b in reads} - {"UFR_DIR", "DUMP_TOKENS", "PRINT_DURS", "GPU"}
    keep = {k: v for k, v in os.environ.items() if k in reads}
    return dict(keep, **GPU_ENV, UFR_DIR=f"{KEEP}/ufr")


def _ensure_singer(log: Logger) -> None:
    """The singer's venv and the voices in the pod's directory, made the first time."""
    state = _sh(f"[ -x {KEEP}/venv/bin/python ] && echo venv; [ -f {KEEP}/ufr/.complete ] && echo ufr; true")
    have = state.decode().split()
    if "venv" not in have:
        log("the GPU machine: building the singer's venv (once, a few minutes)")
        _detached(f"set -e; rm -rf {KEEP}/venv.new; python -m venv {KEEP}/venv.new; "
                  f"{KEEP}/venv.new/bin/pip install -q --no-cache-dir {SING_PACKAGES}; "
                  f"mv {KEEP}/venv.new {KEEP}/venv", "the singer's venv")
    if "ufr" not in have:
        ufr = os.environ.get("UFR_DIR", "")
        if not all(os.path.isdir(os.path.join(ufr, d)) for d in VOICE_DIRS):
            raise Unavailable(f"UFR_DIR ({ufr or 'unset'}) has no voices to copy")
        log("the GPU machine: copying the voices (once, ~430 MB)")
        with tempfile.TemporaryDirectory(prefix="ufr-", dir="/var/tmp") as tmp:
            tar = os.path.join(tmp, "ufr.tar")
            subprocess.run(["tar", "-C", ufr, "-cf", tar, *VOICE_DIRS], check=True)
            _heard()
            with open(tar, "rb") as f:
                _send_file(f"{KEEP}/ufr.tar", f)
        _detached(f"set -e; rm -rf {KEEP}/ufr; mkdir -p {KEEP}/ufr.new; tar -C {KEEP}/ufr.new -xf {KEEP}/ufr.tar; "
                  f"rm {KEEP}/ufr.tar; mv {KEEP}/ufr.new {KEEP}/ufr; touch {KEEP}/ufr/.complete", "the voices")


def _send_file(path_in_pod: str, f) -> None:
    """:func:`_send` for a file too big to hold: piece by piece off the disk."""
    q = shlex.quote(path_in_pod)
    sha, pieces = hashlib.sha256(), 0
    for chunk in iter(lambda: f.read(CHUNK), b""):
        sha.update(chunk)
        _sh(f"cat > {q}.part{pieces}", input=chunk)
        pieces += 1
    parts = " ".join(f"{q}.part{i}" for i in range(pieces))
    _sh(f"cat {parts} > {q}.new && rm -f {parts} && "
        f"[ \"$(sha256sum < {q}.new | cut -c1-64)\" = {sha.hexdigest()} ] && mv {q}.new {q}", timeout=180)


def sing(line_json: str, out_wav: str, voice: str, log: Logger = _noop,
         timeout: float = LINE_TIMEOUT_S) -> List[str]:
    """Sing the line in ``line_json`` with ``voice`` on the GPU machine into ``out_wav``.
    Raises :class:`Unavailable` whenever it did not, so the line is sung on this host."""
    _not_cooling()

    def work():
        _ensure_singer(log)
        return _sing_line(line_json, out_wav, voice, log, timeout)
    return _on_card(work, log)


def _sing_line(line_json: str, out_wav: str, voice: str, log: Logger, timeout: float) -> List[str]:
    job = _new_job()
    try:
        with open(line_json, "rb") as f:
            _sh(f"mkdir -p {job} && cat > {job}/line.json", input=f.read())
        with open(SINGER, "rb") as f:
            _sh(f"cat > {job}/sing_ufr.py", input=f.read())
        sets = " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(_singer_env().items()))
        _start_job(job, f"export LD_LIBRARY_PATH=$(ls -d {KEEP}/venv/lib/python3.*/site-packages/nvidia/*/lib "
                        f"2>/dev/null | tr '\\n' :)\nenv {sets} nice -n 19 {KEEP}/venv/bin/python -I {job}/sing_ufr.py "
                        f"{job}/line.json {job}/out.wav 100 {shlex.quote(voice)}\n")
        lines = _follow(job, "the singer", log, timeout)
        out = _sh(f"if [ -f {job}/out.wav ]; then sha256sum < {job}/out.wav | cut -c1-64; "
                  f"base64 {job}/out.wav; else echo none; fi", timeout=120).decode()
        head, _, body = out.partition("\n")
        if head.strip() == "none":
            raise Unavailable("the singer on the GPU machine wrote no WAV: " + " | ".join(lines[-3:]))
        data = base64.b64decode(body)
        if hashlib.sha256(data).hexdigest() != head.strip():
            raise Unavailable("the WAV from the GPU machine arrived damaged")
        with open(out_wav + ".part", "wb") as f:
            f.write(data)
        os.replace(out_wav + ".part", out_wav)
        return lines
    finally:
        _end_job(job)


def use_kind(kind: str) -> None:
    """This run's pod and paths: the reader's or the singer's."""
    global KIND, NAMESPACE, POD, KEEP, HOST_KEEP, SYSLIB
    KIND = kind
    NAMESPACE, POD, KEEP, HOST_KEEP = KINDS[kind]
    SYSLIB = f"{KEEP}/syslib/usr/lib/x86_64-linux-gnu"


def main(argv: List[str]) -> int:
    kind, args = (argv[0], argv[1:]) if argv else ("", [])
    if not (kind == "read-band" and len(args) >= 3 or kind == "sing-line" and len(args) == 3):
        print(__doc__, file=sys.stderr)
        return 64
    log = lambda line: print(line, flush=True)  # noqa: E731
    # Stopped (sing_song.py cancelled, or a caller's deadline): end like an error, so the
    # job's `finally` stops it in the pod, where it runs detached, before the card lock goes.
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
    use_kind(kind)
    try:
        if kind == "read-band":
            image, out_dir, venv, flags = args[0], args[1], args[2], args[3:]
            read(image, flags, out_dir, venv, log=log)
        else:
            sing(args[0], args[1], args[2], log=log)
    except Unavailable as exc:
        print(str(exc), flush=True)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
