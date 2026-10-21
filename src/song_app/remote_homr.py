"""Read a band with homr on eero-pc's GPU when it is free (issue #437).

Every scan used to read on this host under its one heavy slot, so a batch of
new songs queued behind each other: on eerovil/eeron-omat-taskit#103 one song
waited about 35 minutes for the slot. eero-pc has a GTX 970 too, which
sing-song already borrows the same way (a pod reached with ``kubectl --context
eero-pc``, deleted after the run). This module reads a band there.

**eero-pc first, this host when it cannot.** A band read with the installed homr
goes to eero-pc, one at a time across every song in the app; this host's heavy
slot is not taken for it. When eero-pc cannot read — k3s is down (game mode),
another pod holds the card, its homr does not match this host's, or a call
keeps failing — :func:`read` raises :class:`Unavailable` and :func:`omr.read_page`
reads the band here under the heavy slot as before. After such a failure
eero-pc is left alone for :data:`COOLDOWN_S`, so every band of a song does not
pay the wait again.

**The same reading either way.** The pod's venv is rebuilt from this host's
pins whenever they change, never from anything newer: the installed venv's
``uv pip freeze`` (the homr commit, built here into a wheel, and every other
package at its version), the same onnxruntime wheel built for the 970, the same
CUDA libraries, the same python build. After the build every file of the pod's
site-packages is checked against this host's by sha256, and a venv that differs
is never used (:func:`_verify`). Measured on three Insomnis systems: the two
cards write byte-identical MusicXML. A venv is kept per pin set under
``/var/tmp/homr-gpu`` on eero-pc, so a pod starts in seconds once built.

**A cut stream costs one call, not the band.** On #103 eero-pc's ``kubectl
exec`` stream was cut ("connection reset by peer") after a couple of minutes,
cause unknown. So nothing here holds a stream open for the length of a read:
homr runs detached in the pod, and the app sends the image, starts the job,
polls its log and fetches the MusicXML in calls of a second or less, each
retried. Starting a job twice starts it once. Only when eero-pc stops answering
for :data:`LOST_AFTER_S` does the band go back to this host.

**The card is freed afterwards.** The pod is deleted once no band has used it
for :data:`IDLE_S`, and when the app shuts down (:func:`release`), so
eero-pc's other pods can have the card.

Off unless ``HOMR_KUBE_CONTEXT`` names the kubectl context (``eero-pc`` on this
host's ``.env``). Only the installed homr reading on the GPU goes there: a
working copy of a branch, and a homr reading on the CPU, read here.
"""

from __future__ import annotations

import base64
import hashlib
import os
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

Logger = Callable[[str], None]

NAMESPACE = "homr"
POD = "reader"
#: python:3.12-slim, pinned: the system libraries opencv loads come with it.
IMAGE = ("python:3.12-slim@sha256:"
         "a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1")
#: On eero-pc's disk (hostPath), so the venv outlives the pod.
KEEP = "/homr"
HOST_KEEP = "/var/tmp/homr-gpu"
SYSLIB = f"{KEEP}/syslib/usr/lib/x86_64-linux-gnu"
#: Where a band's job lives in the pod while it is read.
JOBS = "/tmp/homr-jobs"
RUN_HOMR = "from homr.main import main; main()"

#: Delete the pod when no band has used it for this long.
IDLE_S = float(os.getenv("HOMR_REMOTE_IDLE_S", "120"))
#: After eero-pc could not read, read here for this long without asking.
COOLDOWN_S = float(os.getenv("HOMR_REMOTE_COOLDOWN_S", "300"))
#: How long a new pod may take to be Ready (it is seconds when the card is free).
READY_WAIT_S = 90.0
#: A band goes back to this host when eero-pc has not answered for this long.
LOST_AFTER_S = 60.0
#: One short call: send an image, start a job, poll, fetch.
CALL_TIMEOUT_S = 60.0
CALL_TRIES = 4
POLL_S = 1.0
POLL_TIMEOUT_S = 20.0
#: Building a venv: the wheels, the install (~1-2 min), the weights.
BUILD_TIMEOUT_S = 1800.0
#: The slowest transfer still counted as eero-pc answering (a LAN does ~100x this).
TRANSFER_BYTES_S = 1_000_000
#: The pod exits by itself when no call has touched this for EXPIRE_MIN minutes.
ALIVE = "/tmp/homr-alive"
EXPIRE_MIN = 10

#: Bumped when what the pod is built from changes in a way the pins do not say.
RECIPE = "1"


class Unavailable(RuntimeError):
    """eero-pc cannot read this band; read it on this host."""


def _noop(_message: str) -> None:
    pass


def context() -> str:
    return (os.getenv("HOMR_KUBE_CONTEXT") or "").strip()


def wanted(engine) -> bool:
    """Whether a read with ``engine`` should be tried on eero-pc at all."""
    return bool(context()) and engine is not None and engine.default \
        and engine.gpu == "force"


def _kubectl_binary() -> str:
    configured = os.getenv("KUBECTL")
    if configured:
        return configured
    # The app's service runs with a bare PATH; kubectl lives in ~/.local/bin here.
    return shutil.which("kubectl") or os.path.expanduser("~/.local/bin/kubectl")


def _kubectl(args: List[str], *, input: Optional[bytes] = None,
             timeout: float = CALL_TIMEOUT_S) -> subprocess.CompletedProcess:
    """One kubectl call against eero-pc. The seam the tests replace."""
    cmd = [_kubectl_binary(), "--context", context(), "-n", NAMESPACE] + args
    stdin = {"input": input} if input is not None else {"stdin": subprocess.DEVNULL}
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout,
                              start_new_session=True, **stdin)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, b"", b"kubectl timed out")
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 127, b"", str(exc).encode())


def _err(p: subprocess.CompletedProcess) -> str:
    text = (p.stderr or b"").decode(errors="replace").strip() or f"exit {p.returncode}"
    return text.splitlines()[-1][:300]


def _sh(script: str, *, input: Optional[bytes] = None, timeout: Optional[float] = None,
        tries: int = CALL_TRIES) -> bytes:
    """Run a shell script in the pod, retried when the call fails.

    The scripts are written so that running one twice is the same as running it
    once, which is what makes a retry after a cut stream safe.

    Bounded as a whole, retries included: a call may take ``timeout`` (by
    default :data:`CALL_TIMEOUT_S`, more for the bytes sent at
    :data:`TRANSFER_BYTES_S`), and all its tries together that plus
    :data:`LOST_AFTER_S`. Anything that runs longer than a call runs as a
    detached job (:func:`_start_job`), whose silence is what is bounded.
    """
    if timeout is None:
        timeout = CALL_TIMEOUT_S + len(input or b"") / TRANSFER_BYTES_S
    budget = time.monotonic() + timeout + LOST_AFTER_S
    # Each call says it is still wanted; the pod exits by itself when nobody
    # has for EXPIRE_MIN minutes, even if deleting it failed.
    script = f"touch {ALIVE} 2>/dev/null; {script}"
    last = "no time left"
    for attempt in range(tries):
        left = budget - time.monotonic()
        if attempt:
            time.sleep(max(0.0, min(2 ** attempt, 8, left - 1)))
            left = budget - time.monotonic()
        if left <= 0:
            break
        # The script travels as an argument, any data on stdin.
        stdin = ["-i"] if input is not None else []
        p = _kubectl(["exec", *stdin, POD, "--", "sh", "-c", script],
                     input=input, timeout=min(timeout, left))
        if p.returncode == 0:
            return p.stdout or b""
        last = _err(p)
    raise Unavailable(f"eero-pc did not answer: {last}")


# --------------------------------------------------------------------------
# The pins: what this host reads with, and so what eero-pc must read with
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


_pins_cache: Dict[tuple, Pins] = {}


def pins(venv: str) -> Pins:
    """This host's homr venv, as what the pod has to be built from."""
    from . import omr
    cfg = {}
    try:
        with open(os.path.join(venv, "pyvenv.cfg"), encoding="utf-8") as f:
            cfg = dict(line.split(" = ", 1) for line in f.read().splitlines() if " = " in line)
    except OSError as exc:
        raise Unavailable(f"cannot read {venv}/pyvenv.cfg: {exc}") from exc
    marker = omr._marker(venv)
    ort = marker.get("ort_wheel", "")
    if not ort or not os.path.isfile(ort):
        raise Unavailable("this host's homr has no GPU onnxruntime wheel to copy "
                          f"({ort or 'none recorded'})")
    site = _site_packages(venv)
    preload = os.path.join(site, "onnxruntime", "capi", "_ld_preload.py")
    stamp = (venv, os.stat(site).st_mtime_ns, os.stat(preload).st_mtime_ns,
             ort, os.stat(ort).st_mtime_ns)
    if stamp in _pins_cache:
        return _pins_cache[stamp]
    p = subprocess.run([_uv(), "pip", "freeze"], capture_output=True, text=True,
                       env={**os.environ, "VIRTUAL_ENV": venv}, timeout=60)
    if p.returncode != 0:
        raise Unavailable(f"uv pip freeze failed: {p.stderr.strip()[-300:]}")
    freeze = p.stdout
    commit = omr._installed_vcs(venv).get("commit", "")
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
    _pins_cache.clear()
    _pins_cache[stamp] = result
    return result


def _venv_dir(p: Pins) -> str:
    return f"{KEEP}/venvs/{p.fingerprint}"


# --------------------------------------------------------------------------
# The pod
# --------------------------------------------------------------------------
POD_YAML = f"""\
apiVersion: v1
kind: Namespace
metadata: {{name: {NAMESPACE}}}
---
apiVersion: v1
kind: Pod
metadata: {{name: {POD}, namespace: {NAMESPACE}, labels: {{app: musescore-choir-homr}}}}
spec:
  restartPolicy: Never
  runtimeClassName: nvidia          # without it the card is not passed in
  terminationGracePeriodSeconds: 5
  containers:
  - name: reader
    image: {IMAGE}
    # Exits (and frees the card) when no call from the app has come for
    # {EXPIRE_MIN} minutes: the app deletes it sooner, this is in case that failed.
    command: ["sh", "-c", "trap 'exit 0' TERM; touch {ALIVE}; while sleep 30 & wait $!; do [ -n \\"$(find {ALIVE} -mmin -{EXPIRE_MIN})\\" ] || exit 0; done"]
    resources:
      limits: {{nvidia.com/gpu: 1}}   # the card, or the pod does not start
    volumeMounts:
    - {{name: keep, mountPath: {KEEP}}}
  volumes:
  - name: keep
    hostPath: {{path: {HOST_KEEP}, type: DirectoryOrCreate}}
"""

_lock = threading.Lock()          # one band (or build) on the card at a time
_state_lock = threading.Lock()    # the fields below
_cool_until = 0.0
_cool_reason = ""
_uses = 0                         # bumped on every band; the idle timer compares it
_idle_timer: Optional[threading.Timer] = None
_bad: Dict[str, str] = {}         # fingerprint -> why its venv may not be used


def _pod_status() -> Tuple[str, dict]:
    """("missing" | "terminating" | "ready" | "waiting" | "unschedulable", details)."""
    import json
    p = _kubectl(["get", "pod", POD, "-o", "json", "--request-timeout=15s"], timeout=30)
    if p.returncode != 0:
        if b"NotFound" in (p.stderr or b""):
            return "missing", {}
        raise Unavailable(f"cannot reach eero-pc's cluster: {_err(p)}")
    pod = json.loads(p.stdout or b"{}")
    if pod.get("metadata", {}).get("deletionTimestamp"):
        return "terminating", pod
    status = pod.get("status", {})
    if status.get("phase") in ("Succeeded", "Failed"):
        return "terminating", pod
    for c in status.get("conditions") or []:
        if c.get("type") == "PodScheduled" and c.get("status") == "False":
            return "unschedulable", c
        if c.get("type") == "Ready" and c.get("status") == "True":
            return "ready", pod
    return "waiting", pod


def _delete_pod(wait: bool = False) -> bool:
    """Delete the pod; False when kubectl could not (the caller tries again)."""
    args = ["delete", "pod", POD, "--ignore-not-found", f"--wait={'true' if wait else 'false'}"]
    return _kubectl(args, timeout=90 if wait else 30).returncode == 0


def _start_pod(log: Logger) -> None:
    state, _ = _pod_status()
    if state == "ready":
        return
    if state == "terminating":
        # One left over from the last run, still going: let it go first, or the
        # apply below changes nothing and the old one is exec'd into.
        if _kubectl(["wait", "--for=delete", f"pod/{POD}", "--timeout=60s"],
                    timeout=90).returncode != 0:
            _kubectl(["delete", "pod", POD, "--grace-period=0", "--force"], timeout=30)
        state = "missing"
    if state == "missing":
        log("eero-pc: starting the homr pod")
        p = _kubectl(["apply", "-f", "-"], input=POD_YAML.encode(), timeout=60)
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
            _delete_pod()
            raise Unavailable(f"eero-pc's card is not free ({why})")
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
    from . import homr_install
    repo = homr_install.repo()
    src = os.path.join(into, "homr-src")
    log(f"eero-pc: building homr {p.homr_commit[:7]} into a wheel")
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


def _send(path_in_pod: str, data: bytes) -> None:
    q = shlex.quote(path_in_pod)
    sha = hashlib.sha256(data).hexdigest()
    _sh(f"cat > {q}.part && [ \"$(sha256sum < {q}.part | cut -c1-64)\" = {sha} ] "
        f"&& mv {q}.part {q}", input=data, timeout=600)


def _build(p: Pins, log: Logger) -> None:
    venv = _venv_dir(p)
    log("eero-pc: building its homr venv to match this host's (once per homr "
        "install, a few minutes)")
    _detached(_BOOTSTRAP.format(keep=KEEP, uv=p.uv, python=p.python), "the setup")
    with tempfile.TemporaryDirectory(prefix="homr-wheel-") as tmp:
        wheel = _build_homr_wheel(p, tmp, log)
        with open(wheel, "rb") as f:
            _send(f"{KEEP}/wheels/{os.path.basename(wheel)}", f.read())
        homr_line = f"homr @ file://{KEEP}/wheels/{os.path.basename(wheel)}"
    ort_name = os.path.basename(p.ort_wheel)
    have = _sh(f"sha256sum {KEEP}/wheels/{ort_name} 2>/dev/null | cut -c1-64; true")
    if have.decode().strip() != p.ort_sha:
        log("eero-pc: copying the GPU onnxruntime wheel")
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
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", "..", "scripts", "onnxruntime-sm52",
                           "clear_execstack.py"), "rb") as f:
        _send(f"{KEEP}/wheels/clear_execstack.py", f.read())
    log("eero-pc: installing (a minute or two)")
    _detached(_INSTALL.format(keep=KEEP, venv=venv, python=p.python, fp=p.fingerprint,
                              reinstall=reinstall, syslib=SYSLIB, run=RUN_HOMR),
              "the install")
    _verify(p, log)


def _detached(script: str, what: str) -> None:
    """Run a long setup step like a band: detached, followed by short calls, so a
    cut stream does not stop it and a silent eero-pc is given up on in
    LOST_AFTER_S however long the step itself may run."""
    job = _new_job()
    try:
        _start_job(job, script)
        _follow(job, what, _noop, BUILD_TIMEOUT_S)
    finally:
        _end_job(job)


def _verify(p: Pins, log: Logger) -> None:
    """Every file eero-pc's homr reads with is this host's, byte for byte."""
    venv = _venv_dir(p)
    log("eero-pc: checking its homr file by file against this host's")
    version = _sh(f"{venv}/bin/python -c 'import sys; print(sys.version)'").decode().strip()
    # Hashing ~3 GB takes ~10 s there.
    remote = _parse_manifest(_sh(_MANIFEST.format(venv=venv, skip=_manifest_skip()),
                                 timeout=180).decode())
    local = _local_manifest(p.venv)
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
    log(f"eero-pc: its homr matches this host's ({len(remote)} files)")


def _spare_weights(path: str) -> bool:
    import re
    return bool(re.search(r"-[0-9a-f]{40}(_fp16)?\.onnx$", path))


def _ensure_venv(p: Pins, log: Logger) -> None:
    if p.fingerprint in _bad:
        raise Unavailable(_bad[p.fingerprint])
    venv = _venv_dir(p)
    state = _sh(f"if [ -f {venv}/.verified ]; then echo ok; "
                f"elif [ -f {venv}/.mismatch ]; then cat {venv}/.mismatch; else echo none; fi")
    state = state.decode().strip()
    if state == "ok":
        return
    if state != "none":
        _bad[p.fingerprint] = f"eero-pc's homr does not match this host's: {state}"
        raise Unavailable(_bad[p.fingerprint])
    try:
        _build(p, log)
    except Unavailable as exc:
        if "differ" in str(exc):
            _bad[p.fingerprint] = str(exc)
        raise


# --------------------------------------------------------------------------
# A band
# --------------------------------------------------------------------------
def _cooling() -> Optional[str]:
    with _state_lock:
        if time.monotonic() < _cool_until:
            return _cool_reason
    return None


def _cool(reason: str) -> None:
    global _cool_until, _cool_reason
    with _state_lock:
        _cool_until = time.monotonic() + COOLDOWN_S
        _cool_reason = reason


def read(image_path: str, flags: List[str], scratch: str, venv: str,
         log: Logger = _noop, timeout: float = 600) -> List[str]:
    """Read ``image_path`` on eero-pc with homr ``flags``; the MusicXML lands in
    ``scratch`` beside where homr would have written it here. Returns homr's lines.

    Raises :class:`Unavailable` whenever eero-pc did not read it, so the caller
    reads it here instead. Nothing it raises means the band itself is unreadable.
    """
    cooling = _cooling()
    if cooling:
        raise Unavailable(f"{cooling} (not asking again for a few minutes)")
    p = pins(venv)
    global _uses
    with _lock:
        # Scans waiting here while another band failed must not ask again.
        cooling = _cooling()
        if cooling:
            raise Unavailable(f"{cooling} (not asking again for a few minutes)")
        with _state_lock:
            _uses += 1
        try:
            _start_pod(log)
            _ensure_venv(p, log)
            return _read_band(p, image_path, flags, scratch, log, timeout)
        except Unavailable as exc:
            _cool(str(exc))
            raise
        finally:
            _schedule_idle_stop()


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
        log(f"Reading {name} with homr on eero-pc's GPU")
        lines = _follow(job, "homr", log, timeout)
        produced = f"{job}/{base}.musicxml"
        out = _sh(f"if [ -f {produced} ]; then sha256sum < {produced} | cut -c1-64; "
                  f"base64 {produced}; else echo none; fi").decode()
        head, _, body = out.partition("\n")
        if head.strip() == "none":
            raise Unavailable("homr on eero-pc wrote no MusicXML: "
                              + " | ".join(lines[-3:]))
        data = base64.b64decode(body)
        if hashlib.sha256(data).hexdigest() != head.strip():
            raise Unavailable("the MusicXML from eero-pc arrived damaged")
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
    started = last_heard = time.monotonic()
    while True:
        if time.monotonic() - started > timeout:
            raise Unavailable(f"{what} on eero-pc did not finish within {timeout:.0f}s")
        try:
            out = _sh(f"cat {job}/exit 2>/dev/null || echo -; "
                      f"tail -c +{offset + 1} {job}/log 2>/dev/null; true",
                      tries=1, timeout=POLL_TIMEOUT_S)
        except Unavailable:
            if time.monotonic() - last_heard > LOST_AFTER_S:
                raise Unavailable(f"eero-pc has not answered for {LOST_AFTER_S:.0f}s")
            time.sleep(POLL_S)
            continue
        last_heard = time.monotonic()
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
                raise Unavailable(f"{what} on eero-pc exited {status.decode()}: "
                                  + " | ".join(lines[-3:]))
            return lines
        time.sleep(POLL_S)


# --------------------------------------------------------------------------
# Freeing the card
# --------------------------------------------------------------------------
def _schedule_idle_stop() -> None:
    global _idle_timer
    with _state_lock:
        if _idle_timer is not None:
            _idle_timer.cancel()
        seen = _uses
        _idle_timer = threading.Timer(IDLE_S, _stop_if_idle, args=(seen,))
        _idle_timer.daemon = True
        _idle_timer.start()


def _stop_if_idle(seen: int) -> None:
    with _state_lock:
        if _uses != seen:
            return
    if not _lock.acquire(blocking=False):
        return   # a band is reading; it schedules the next check when it ends
    try:
        with _state_lock:
            if _uses != seen:
                return
        if not _delete_pod():
            # The card would stay held: try again later (the pod also expires
            # by itself after EXPIRE_MIN minutes without a call).
            _schedule_idle_stop()
    finally:
        _lock.release()


def release() -> None:
    """Free eero-pc's card now (the app is shutting down)."""
    global _idle_timer
    with _state_lock:
        if _idle_timer is not None:
            _idle_timer.cancel()
            _idle_timer = None
        started = _uses > 0
    if started and context():
        for _ in range(3):
            if _delete_pod():
                return
            time.sleep(2)
