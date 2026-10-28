"""Build the singer's voice folder (core/, dsdur/, tiger/) from the UFR pack as it downloads.

The UFR DiffSinger pack (https://utaufrance.com/telechargement-du-pack-diffsinger-ufr/, V1.0)
ships for OpenUtau: one zip with a 0_CORE/ folder of the shared models and one folder per
voice (UFRV01_LYSE/, UFRV02_REMY/, ...). sing_ufr.py reads its own smaller layout, and this
copies the files it reads into it:

    0_CORE/dsacoustic/millefeuille_v001.onnx            -> core/acoustic.onnx
    0_CORE/dsacoustic/millefeuille_v001.phonemes.json   -> core/phonemes.json
    0_CORE/dsacoustic/millefeuille_v001.languages.json  -> core/languages.json
    0_CORE/dsvocoder/potluck/<the one .onnx>            -> core/potluck.onnx
    0_CORE/dsdur/<file>                                 -> dsdur/<file>
    <voice>/dsdur/embs/millefeuille_v001.<name>.emb     -> dsdur/millefeuille_v001.<name>.emb
    <voice>/dsacoustic/millefeuille_v001.<name>.emb     -> tiger/<name>.emb

The rest (phonemizers, pitch models, other vocoders, pictures) the singer does not use.

    python -I ufr_pack.py <the downloaded .zip, or the folder it unzipped to> <out folder> [<.env>]

With a .env file named, UFR_DIR in it is set to the out folder.
"""
import glob
import os
import shutil
import stat
import sys
import tempfile
import zipfile

#: the voices sing_song.py sings with (main.py's VOICES); a pack without them is no use
NEEDED = ("core/acoustic.onnx", "core/phonemes.json", "core/languages.json", "core/potluck.onnx",
          "dsdur/millefeuille_v001.linguistic.onnx", "dsdur/millefeuille_v001.dur.onnx",
          "tiger/lyse_core.emb", "tiger/remy_adult.emb")
PREFIX = "millefeuille_v001."


def is_ours(path):
    """True when `path` is already a folder in the singer's layout."""
    return os.path.isfile(os.path.join(path, "core", "phonemes.json"))


def is_download(path):
    """True when `path` is a folder the pack unzipped to, in OpenUtau's layout."""
    return any(os.path.isdir(p) for d in ("", "*", "*/*") for p in glob.glob(os.path.join(glob.escape(path), d, "0_CORE")))


def target(rel):
    """Where the pack's file `rel` (a path below the folder holding 0_CORE/, with /) goes in
    ours, or None when the singer does not read it."""
    parts = rel.split("/")
    name = parts[-1]
    if parts[0] == "0_CORE":
        if parts[1:] == ["dsacoustic", PREFIX + "onnx"]:
            return "core/acoustic.onnx"
        if parts[1:] == ["dsacoustic", PREFIX + "phonemes.json"]:
            return "core/phonemes.json"
        if parts[1:] == ["dsacoustic", PREFIX + "languages.json"]:
            return "core/languages.json"
        if parts[1:3] == ["dsvocoder", "potluck"] and len(parts) == 4 and name.endswith(".onnx"):
            return "core/potluck.onnx"
        if parts[1] == "dsdur" and len(parts) == 3:
            return f"dsdur/{name}"
        return None
    if not (name.startswith(PREFIX) and name.endswith(".emb")):
        return None
    if parts[1:-1] == ["dsdur", "embs"]:
        return f"dsdur/{name}"
    if parts[1:-1] == ["dsacoustic"]:
        return f"tiger/{name[len(PREFIX):-len('.emb')]}.emb"
    return None


def listing(src):
    """[(path relative to the folder holding 0_CORE/, open())] of the files in the download."""
    if zipfile.is_zipfile(src):
        z = zipfile.ZipFile(src)
        files = [(n, (lambda n=n: z.open(n))) for n in z.namelist() if not n.endswith("/")]
    elif os.path.isdir(src):
        files = []
        for top, _, names in os.walk(src):
            for n in names:
                p = os.path.join(top, n)
                files.append((os.path.relpath(p, src).replace(os.sep, "/"), (lambda p=p: open(p, "rb"))))
    else:
        raise SystemExit(f"{src} is neither the UFR pack's zip nor a folder")
    cores = sorted({n[:n.index("0_CORE/")] for n, _ in files if n.startswith("0_CORE/") or "/0_CORE/" in n},
                   key=len)
    if not cores:
        raise SystemExit(f"{src} has no 0_CORE folder: not the UFR pack as it downloads")
    root = cores[0]
    return [(n[len(root):], f) for n, f in files if n.startswith(root)]


def build(src, out):
    """Copy what the singer reads from the download `src` into `out`, replacing it whole (only
    when it is a folder in the singer's layout, an empty one, or not there yet)."""
    if os.path.isdir(src) and is_ours(src):
        raise SystemExit(f"{src} is already in the singer's layout: set UFR_DIR to it")
    picked = {}
    for rel, opener in listing(src):
        to = target(rel)
        if to:
            picked[to] = opener
    missing = [n for n in NEEDED if n not in picked]
    if missing:
        raise SystemExit(f"{src} lacks what the singer needs: {', '.join(missing)}")
    out = os.path.abspath(out)
    if os.path.isdir(out) and os.listdir(out) and not is_ours(out):
        raise SystemExit(f"{out} holds something else: give an empty or new folder")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    # built beside it in a folder of its own, so a failed copy or swap leaves the old voices
    work = tempfile.mkdtemp(prefix=f".{os.path.basename(out)}-", dir=os.path.dirname(out))
    new = os.path.join(work, "new")
    keep = None   # the old voices while the new ones go in; never in `work`, which always goes
    try:
        for to, opener in sorted(picked.items()):
            os.makedirs(os.path.join(new, os.path.dirname(to)), exist_ok=True)
            with opener() as f, open(os.path.join(new, to), "wb") as w:
                shutil.copyfileobj(f, w, 1 << 20)
        if os.path.exists(out):
            keep = tempfile.mkdtemp(prefix=f".{os.path.basename(out)}-old-", dir=os.path.dirname(out))
            os.replace(out, os.path.join(keep, "voices"))
        try:
            os.replace(new, out)
        except OSError:
            if keep and os.path.exists(os.path.join(keep, "voices")):
                try:
                    os.replace(os.path.join(keep, "voices"), out)
                except OSError as e:
                    kept, keep = os.path.join(keep, "voices"), None
                    raise SystemExit(f"could not put the old voices back: they are in {kept}; "
                                     f"move that folder to {out}") from e
            raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if keep:   # the swap or the restore went through: nothing of value is left in it
            shutil.rmtree(keep, ignore_errors=True)
    return sorted(picked)


def set_env(path, key, value):
    """Set `key` in the .env file `path` (adding the line when it has none), through a private
    temporary file beside it that takes the old file's permissions: it holds API tokens."""
    with open(path) as f:
        lines = f.read().splitlines()
    line = f'{key}="{value}"'
    at = [i for i, l in enumerate(lines) if l.startswith(f"{key}=")]
    for i in at:
        lines[i] = line
    if not at:
        lines.append(line)
    fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(path)}-", dir=os.path.dirname(os.path.abspath(path)))
    try:
        os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit("python -I ufr_pack.py <the downloaded .zip or its folder> <out folder> [<.env>]")
    names = build(sys.argv[1], sys.argv[2])
    if len(sys.argv) == 4:
        set_env(sys.argv[3], "UFR_DIR", os.path.abspath(sys.argv[2]))
    voices = [n for n in names if n.startswith("tiger/")]
    print(f"{sys.argv[2]}: {len(names)} files, {len(voices)} voices (sing_song.py sings with lyse_core and remy_adult)")
