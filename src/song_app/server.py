"""FastAPI backend for the `song` app — a thin, state-aware door over the toolkit."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import traceback
from typing import Dict, List, Optional, Set

import dotenv
from lxml import etree
from fastapi import FastAPI, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import (FileResponse, JSONResponse, PlainTextResponse,
                               Response)
from fastapi.staticfiles import StaticFiles

from . import (agentdeck, bar_readings, health, heavy_slot, homr_install, job_state, omr,
               pdf_systems, pipeline, playlists, problems, pwa_assets, scan, site_refresh,
               state, system_finder, verification)
from src.clean_score.utils.score_fixes import FixError
from src.scrollvideo.score import format_groups, parse_groups
from src.media_root import media_dir

SCRIPT_DIR = state.SCRIPT_DIR
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# Load environment (MUSESCORE_CLI_PATH etc.), .env then .env.default.
_env = os.path.join(SCRIPT_DIR, ".env")
dotenv.load_dotenv(_env if os.path.exists(_env) else os.path.join(SCRIPT_DIR, ".env.default"))

app = FastAPI(title="song")
app.include_router(agentdeck.router)


# --------------------------------------------------------------------------
# Freshness
# --------------------------------------------------------------------------
# The app deploys over itself every couple of minutes, and a recorded video is
# rewritten under the same name whenever a part is re-rendered. Neither the SPA
# files nor the videos carried any Cache-Control, which does NOT mean "do not
# cache": a browser given no instruction is free to invent a freshness lifetime
# from Last-Modified, and Chrome on Android invents a generous one. The phone
# then serves old code and old video off its own disk without ever asking us,
# which is why only restarting the browser helped.
#
# "no-cache" is not "no-store" — the copy is kept, but the browser has to ask
# before reusing it. Starlette's FileResponse already sends an ETag and honours
# If-None-Match, so an unchanged file still costs one small 304 rather than a
# re-download.
REVALIDATE = {"Cache-Control": "no-cache"}


class RevalidatingStaticFiles(StaticFiles):
    """StaticFiles that makes the browser revalidate instead of guessing."""

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        response = super().file_response(*args, **kwargs)
        response.headers.update(REVALIDATE)
        return response


# --------------------------------------------------------------------------
# WebSocket connection manager — progress logs + state-changed pings per slug.
# --------------------------------------------------------------------------
class Hub:
    def __init__(self) -> None:
        self.conns: Dict[str, Set[WebSocket]] = {}
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    async def connect(self, slug: str, ws: WebSocket) -> None:
        await ws.accept()
        self.conns.setdefault(slug, set()).add(ws)

    def disconnect(self, slug: str, ws: WebSocket) -> None:
        self.conns.get(slug, set()).discard(ws)

    async def _send(self, slug: str, msg: Dict) -> None:
        for ws in list(self.conns.get(slug, set())):
            try:
                await ws.send_json(msg)
            except Exception:
                self.disconnect(slug, ws)

    def emit(self, slug: str, msg: Dict) -> None:
        """Thread-safe broadcast (callable from worker threads).

        Never raises. Progress reporting must not be able to kill the work it is
        reporting on: a closed loop (the server restarted, or the browser went
        away mid-render) would otherwise surface inside the worker thread and
        abort a render that was going perfectly well.
        """
        if self.loop is None or self.loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._send(slug, msg), self.loop)
        except RuntimeError:
            self.loop = None


hub = Hub()


def _require(slug: str) -> state.Song:
    song = state.load(slug)
    if not song:
        raise HTTPException(404, f"No song '{slug}'")
    return song


def _lock_path(song: state.Song) -> str:
    return song.path(".recording.lock")


def _scan_lock_path(song: state.Song) -> str:
    return song.path(".scanning.lock")


def _holds_lock(path: str) -> bool:
    """True if the lock at `path` belongs to a live run in *this* server process.

    A lock written by a previous (now-dead) server is treated as stale and cleared,
    so a crash can't leave a song permanently locked.
    """
    if not os.path.exists(path):
        return False
    try:
        with open(path) as f:
            pid = int((f.read().strip() or "0"))
    except (OSError, ValueError):
        pid = 0
    if pid == os.getpid():
        return True
    os.remove(path)  # stale lock from a different/old process
    return False


def is_recording(song: state.Song) -> bool:
    return _holds_lock(_lock_path(song))


def is_scanning(song: state.Song) -> bool:
    """A scan is minutes long, so a page refresh must not start a second one."""
    return _holds_lock(_scan_lock_path(song))


def _media_version(path: str) -> str:
    """A short stamp that changes whenever the file does (mtime + size)."""
    try:
        st = os.stat(path)
    except OSError:
        return "0"
    return f"{int(st.st_mtime)}-{st.st_size}"


def _media_list(song: state.Song) -> List[Dict]:
    """Merged per-voice videos (and the raw recording) available for review."""
    vdir = song.media_path("video")
    if not os.path.isdir(vdir):
        return []
    out = []
    for name in sorted(os.listdir(vdir)):
        if not name.lower().endswith((".mov", ".mp4")):
            continue
        prefix = song.slug + " "
        is_merged = name.startswith(prefix)
        # Re-recording a part rewrites the same file name, so without the stamp
        # the URL is identical and a phone happily plays yesterday's video.
        version = _media_version(os.path.join(vdir, name))
        out.append({
            "name": name,
            "label": name[len(prefix):].rsplit(".", 1)[0] if is_merged else "raw recording",
            "merged": is_merged,
            "url": f"/api/songs/{song.slug}/media/{name}?v={version}",
        })
    out.sort(key=lambda m: (not m["merged"], m["label"]))
    return out


def _derived(song: state.Song) -> Dict:
    """State plus computed flags the frontend needs."""
    cleaned = song.cleaned_path()
    pdf = song.source_path("pdf")
    issues = song.data.get("health", {}).get("issues", [])
    systems = len([b for b in pdf_systems.load_bounds(song.dir) if b.measure_start])
    # Reading a song is where the app finds out that something it derived has
    # stopped being true, and it is the one place that check lives. It writes
    # only when something really was discarded, so an ordinary read costs a
    # comparison. A song that never scanned derives nothing here and returns at
    # once, which is every song that predates the stage.
    discarded = scan.reconcile(song)
    needs_initial_bpm = bool(
        cleaned and os.path.exists(cleaned) and not pipeline.has_opening_tempo(cleaned))
    return {
        **song.data,
        "slug": song.slug,
        "stages": state.STAGES,
        "stage_index": state.STAGES.index(song.stage) if song.stage in state.STAGES else 0,
        "has_pdf": bool(pdf and os.path.exists(pdf)),
        "has_cleaned": bool(cleaned and os.path.exists(cleaned)),
        "needs_initial_bpm": needs_initial_bpm,
        # Printed-system bounds, labelled with the measures they cover: what lets
        # the viewer show one system and the lyric editor ask per system.
        "systems": systems,
        "open_issues": [i for i in issues if i.get("status") == "open"],
        # Recorded fixes nothing can apply on its own — a sentence waiting for a
        # person or an agent. Read live, so it goes as soon as the entry does.
        "pending_fixes": pipeline.free_text_fixes(song.dir),
        # Slurs a person recorded off the page. Shown for the same reason: a record
        # nobody can see is a file nobody opens.
        "recorded_slurs": pipeline.recorded_slurs(song.dir),
        "recording": is_recording(song),
        "scanning": is_scanning(song),
        # What the scan stage has read, what is still a hole, and what the app
        # threw away this read because its input had moved under it.
        "scan_status": scan.status(song),
        "scan_discarded": [scan.said(line) for line in discarded],
        "media": _media_list(song),
        "jobs": job_state.load(song.dir),
        "verification_summary": verification.summary(song, systems),
    }


def _job_emit(slug: str, kind: str, line: str, entry_type: str = "log") -> None:
    try:
        # Progress can arrive while another thread saves .song.json. The slug already
        # determines the job path, so do not parse unrelated song state merely to log.
        job_state.append(state.song_dir(slug), kind, line, entry_type)
    except Exception:
        traceback.print_exc()
    hub.emit(slug, {"type": entry_type, "line": line})


def _job_finish(song: state.Song, kind: str, error: Optional[str] = None) -> None:
    try:
        job_state.finish(song.dir, kind, error=error)
    except Exception:
        traceback.print_exc()


# --------------------------------------------------------------------------
# Library + create
# --------------------------------------------------------------------------
def _import_one(name: str) -> bool:
    """Infer a .song.json for a legacy songs/<name>/ folder. Returns True if created."""
    d = os.path.join(state.SONGS_DIR, name)
    if not os.path.isdir(d) or os.path.exists(os.path.join(d, state.STATE_FILE)):
        return False
    files = os.listdir(d)

    def is_score(f: str) -> bool:
        lf = f.lower()
        return (lf.endswith((".mscz", ".musicxml", ".xml", ".mscx"))
                and "_cleaned" not in lf and not lf.endswith(".nolyrics.mscx"))

    order = {".mscz": 0, ".musicxml": 1, ".xml": 2, ".mscx": 3}
    inputs = sorted((f for f in files if is_score(f)),
                    key=lambda f: order.get(os.path.splitext(f)[1].lower(), 9))
    inp = inputs[0] if inputs else None
    cleaned = next((f for f in files if f.lower().endswith("_cleaned.mscx")), None)
    pdf = next((f for f in files if f.lower().endswith(".pdf") and not f.endswith(".render.pdf")), None)
    lyrics = "lyrics.json" if "lyrics.json" in files else None

    vdir = os.path.join(media_dir(d), "video")
    outputs = []
    if os.path.isdir(vdir):
        outputs = [f for f in sorted(os.listdir(vdir))
                   if f.lower().endswith((".mov", ".mp4")) and f.startswith(name + " ")]

    if not (inp or cleaned or outputs or pdf):
        return False  # not a recognisable song folder

    # per-system if an answer set was recorded for this input score
    src_name = inp or (cleaned[: -len("_cleaned.mscx")] + ".mscx" if cleaned else "")
    mode = "per-system" if pipeline.has_system_answers(src_name) else "normal"

    try:
        created = os.path.getmtime(d)
    except OSError:
        created = 0
    data: Dict = {"name": name, "slug": name, "mode": mode, "sources": {}, "created_at": created}
    if inp:
        data["sources"]["xml"] = inp
    if pdf:
        data["sources"]["pdf"] = pdf
    if cleaned:
        cp = os.path.join(d, cleaned)
        data["cleaned"] = cleaned
        data["cleaned_fingerprint"] = state.file_fingerprint(cp)
        found = health.scan(cp)
        data["health"] = {
            "checked_against": data["cleaned_fingerprint"],
            "issues": [{**i, "status": "open"} for i in found],
        }
    if lyrics:
        data["lyrics"] = {"json": lyrics, "warnings": []}
    if outputs:
        data["record"] = {"exported": True, "outputs": outputs, "audio_delay_ms": 1300}

    # A folder with an input score is past scanning whatever else it has, so the
    # 48 legacy songs land where they always did. Only a folder that has nothing
    # but a PDF is a song waiting to be read off the page.
    data["stage"] = ("upload" if outputs else "review" if cleaned
                     else "clean" if inp else "scan" if pdf else "register")
    state.Song(name, data).save()
    return True


def import_legacy() -> int:
    """Create state files for any legacy folders that don't have one. Idempotent."""
    if not os.path.isdir(state.SONGS_DIR):
        return 0
    count = 0
    for name in sorted(os.listdir(state.SONGS_DIR)):
        try:
            if _import_one(name):
                count += 1
        except Exception:
            traceback.print_exc()
    return count


@app.get("/healthz")
def healthz() -> Dict:
    """Is this process serving? The deploy watcher asks after every restart, and it
    reads `status`, so the shape matters as much as the 200. Deliberately shallow: it
    must answer while a clean or a render is occupying the worker threads, so it
    touches no song, no MuseScore and no disk."""
    return {"status": "ok"}


@app.get("/api/songs")
def api_songs() -> List[Dict]:
    return [s.to_summary() for s in state.list_songs()]


@app.post("/api/import")
def api_import() -> Dict:
    return {"imported": import_legacy()}


def name_from_filename(filename: str) -> str:
    """`Laulun_aika.pdf` -> `Laulun aika`: the stem, underscores as spaces."""
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    return " ".join(stem.replace("_", " ").split())


@app.post("/api/songs")
async def api_create(
    name: str = Form(""),
    per_system: bool = Form(False),
    voicing: str = Form(""),
    xml: UploadFile = None,
    pdf: UploadFile = None,
) -> Dict:
    """Register a song from a score, a PDF, or both.

    The score used to be required and the PDF optional. That is now the other way
    round in the sense that matters: **a PDF on its own is a song**, and it starts
    at `scan`, because the app can now read a score off the page. Handing in a
    score still starts at `clean` — importing MusicXML from elsewhere stays the
    manual route (#86), and a song that arrives with a score is past scanning by
    definition.
    """
    has_xml = xml is not None and bool(xml.filename)
    has_pdf = pdf is not None and bool(pdf.filename)
    if not (has_xml or has_pdf):
        raise HTTPException(400, "A MuseScore/MusicXML file or a PDF is required")
    # A blank name falls back to the file's own name, the PDF first since that
    # is the ordinary way in (#241).
    name = name.strip() or name_from_filename(pdf.filename if has_pdf else xml.filename)
    if not name:
        raise HTTPException(400, "Name is required")

    if voicing and voicing not in ("men", "women", "mixed"):
        raise HTTPException(400, "voicing must be men, women or mixed")
    song = state.create(name, per_system, voicing)
    sources = song.data.setdefault("sources", {})
    if has_xml:
        xml_name = os.path.basename(xml.filename)
        with open(song.path(xml_name), "wb") as f:
            f.write(await xml.read())
        sources["xml"] = xml_name
    if has_pdf:
        pdf_name = os.path.basename(pdf.filename)
        with open(song.path(pdf_name), "wb") as f:
            f.write(await pdf.read())
        sources["pdf"] = pdf_name
    song.set_stage("clean" if has_xml else "scan")
    song.save()
    return {"slug": song.slug}


@app.get("/api/songs/{slug}")
def api_song(slug: str) -> Dict:
    return _derived(_require(slug))


# --------------------------------------------------------------------------
# Scan stage — read the score off the PDF, one printed system at a time
# --------------------------------------------------------------------------
def _run_scan(slug: str, opts: Dict,
              reader: Optional[homr_install.Reader] = None) -> None:
    song = _require(slug)
    log = lambda m: _job_emit(slug, "scan", m)
    try:
        # homr's own output is the progress: a system is ~20s and a song two to
        # five minutes, so its lines go straight to the song's log the way a
        # clean's and a render's do, unparsed.
        result = scan.run(song, log=log, only=opts.get("systems"),
                          engine=opts.get("engine"))
        holes = result["holes"]
        if not holes:
            _label_bounds(song, _bounds_score(_require(slug)))
        log(f"Read {result['read']} of {result['systems']} system(s)."
            + (f" Still to read: {', '.join(str(i) for i in holes)}." if holes
               else " Check it against the page, then say it is right."))
        _job_finish(song, "scan")
    except Exception as exc:
        traceback.print_exc()
        _job_emit(slug, "scan", str(exc), "error")
        _job_finish(song, "scan", str(exc))
    finally:
        if reader:
            reader.release()
        lock = _scan_lock_path(song)
        if os.path.exists(lock):
            os.remove(lock)
        hub.emit(slug, {"type": "state"})


@app.post("/api/songs/{slug}/scan")
async def api_scan(slug: str, body: Dict = None) -> Dict:
    """Start a scan. `systems` re-reads named systems; omitted reads the holes."""
    song = _require(slug)
    if not song.source_path("pdf"):
        raise HTTPException(400, "This song has no PDF to scan")
    if not pdf_systems.load_bounds(song.dir):
        raise HTTPException(
            400, "Set the printed-system boundaries in the Systems viewer first")
    gaps = scan.pages_without_bands(song)
    if gaps:
        raise HTTPException(
            400, "Page(s) " + ", ".join(str(p) for p in gaps) + " have no "
            "printed systems marked. Mark every page in the Systems viewer first.")
    # Held from here until the scan's worker finishes: an install cannot start
    # while it is, and this cannot start while an install runs.
    try:
        reader = homr_install.begin_read()
    except homr_install.Refused as exc:
        raise HTTPException(409, str(exc)) from None
    try:
        return _start_scan(song, slug, body, reader)
    except BaseException:
        reader.release()
        raise


def _start_scan(song: state.Song, slug: str, body: Optional[Dict],
                reader: homr_install.Reader) -> Dict:
    opts = dict(body or {})
    try:
        opts["systems"] = [int(i) for i in (opts.get("systems") or [])]
    except (TypeError, ValueError):
        raise HTTPException(400, "systems must be whole numbers") from None
    # A *named* engine is resolved here rather than in the worker: one that is not
    # there has to be a refused request, not a scan that starts, takes a lock and
    # then fails on the first band. Asking for no engine in particular is left
    # alone — `omr` picks the installed one when the read happens, and a host with
    # no homr at all should fail where it always did, saying so in the song's log.
    key = opts.get("engine")
    opts["engine"] = None
    if key and key != omr.DEFAULT_ENGINE:
        try:
            opts["engine"] = omr.engine_for(key)
        except omr.HomrMissing as exc:
            raise HTTPException(400, str(exc)) from None
    if is_scanning(song) or not job_state.start_if_idle(
            song.dir, "scan", ("scan", "clean", "render", "upload"),
            pdf_systems.file_version(song.source_path("pdf"))):
        raise HTTPException(409, "Another scan, clean, render, or upload is "
                                 "already running for this song.")
    # The durable start above is the atomic gate; the PID lock is what makes a
    # page refresh unable to start a second scan over the top of this one.
    try:
        with open(_scan_lock_path(song), "w") as f:
            f.write(str(os.getpid()))
    except Exception as exc:
        _job_finish(song, "scan", str(exc))
        raise
    asyncio.get_running_loop().run_in_executor(None, _run_scan, slug, opts, reader)
    return {"started": True}



@app.get("/api/homr/install")
def api_homr_install_status(refresh: bool = False) -> Dict:
    """Which homr is installed, whether the fork has moved on, and the install log.

    `refresh` asks GitHub again rather than trusting the ten-minute cache.
    """
    return homr_install.status(refresh=refresh)


@app.post("/api/homr/install")
async def api_homr_install() -> Dict:
    """Run scripts/install-homr.sh: install homr, or update it to the fork's main.

    A press, never automatic, and refused while any song job is running — the
    script replaces files inside the venv a scan would be reading from.
    """
    loop = asyncio.get_running_loop()
    try:
        homr_install.start(lambda work: loop.run_in_executor(None, work))
    except homr_install.Refused as exc:
        raise HTTPException(409, str(exc)) from None
    return {"started": True}


@app.get("/api/homr-engines")
def api_homr_engines() -> Dict:
    """The homr installs this host has, for the Scan panel's picker.

    App-wide, not per song: which engine reads a page is a property of the host,
    and the choice itself lasts one scan run.
    """
    # The commit and the dirty flag ride along because they are what a parse is
    # recorded against (#154) — the label is the hint, not the record.
    return {"engines": [{"key": e.key, "label": e.label, "default": e.default,
                         "commit": e.commit or "", "dirty": bool(e.dirty)}
                        for e in omr.engines()]}


@app.get("/api/songs/{slug}/scan-system/{index}")
def api_scan_system(slug: str, index: int, dpi: int = 200):
    """One scanned system, engraved — the parse as a picture, beside its band.

    Rendering the fragment rather than the assembled score is what keeps the
    comparison honest while a system is still a hole: the fragments either side of
    it are shown as they were read, and the missing one is missing.
    """
    song = _require(slug)
    fragment = scan.fragment_path(song, index)
    if not fragment:
        raise HTTPException(404, "That system has not been read")
    try:
        path = pipeline.scan_system_render(song.dir, fragment, max(50, min(dpi, 600)))
    except Exception as exc:
        raise HTTPException(500, str(exc))
    return FileResponse(path, media_type="image/png", headers=dict(REVALIDATE))


# --------------------------------------------------------------------------
# Clean stage
# --------------------------------------------------------------------------
@app.get("/api/songs/{slug}/systems")
def api_systems(slug: str) -> Dict:
    """Per-system grid for the clean panel (per-system mode only)."""
    song = _require(slug)
    xml = song.source_path("xml")
    if not xml:
        raise HTTPException(400, "No source file")
    mscx = pipeline.convert_to_mscx(xml, song.dir)
    return {"grid": pipeline.system_grid(mscx)}


@app.put("/api/songs/{slug}/systems")
def api_save_systems(slug: str, answers: Dict = None) -> Dict:
    """Persist grid answers: {system_index: {staff_id: 'T1,T2'}}."""
    song = _require(slug)
    xml = song.source_path("xml")
    mscx = pipeline.convert_to_mscx(xml, song.dir)
    parsed = {int(si): {int(sid): v for sid, v in staves.items()}
              for si, staves in (answers or {}).items()}
    pipeline.save_system_answers(mscx, parsed)
    # An answer is about the staves of one scanned system, so it is derived from
    # that system's fragment and has to say which one, or a re-scan cannot know
    # it has invalidated it.
    scan.stamp_answers(song, list(parsed))
    song.save()
    return {"ok": True}


def _error_text(exc: BaseException) -> str:
    """What a failed job says. Our own errors are sentences; anything else names its type.

    A bare `str(KeyError(3))` is `"3"`, and that was the whole of what one failed
    clean told anybody — the traceback only reaches the service journal (#357).
    """
    if isinstance(exc, (RuntimeError, FixError)):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def _run_clean(slug: str) -> None:
    song = _require(slug)
    xml = song.source_path("xml")
    log = lambda m: _job_emit(slug, "clean", m)
    try:
        # A pick made on a reading the scan has since replaced is about a bar that is
        # not there any more; replaying it would fail the clean, or worse, fit.
        bar_readings.drop_stale_picks(song, log)
        opens: Dict = {}
        cleaned, source_mscx = pipeline.run_clean(
            xml, song.dir, per_system=(song.mode == "per-system"), log=log,
            voicing=song.data.get("voicing") or None, check=opens,
        )
        # A song scanned before the scan stage labelled its bands gets them here,
        # off the same converted input.
        _label_bounds(song, source_mscx)
        rel = os.path.relpath(cleaned, song.dir)
        song.data["cleaned"] = rel
        song.data["cleaned_fingerprint"] = state.file_fingerprint(cleaned)
        note_check = verification.compare_notes(source_mscx, cleaned)
        song.data.setdefault("verification", {})["notes"] = {
            **note_check, "checked_against": song.data["cleaned_fingerprint"],
        }
        song.data["verification"]["musescore"] = {
            **opens, "checked_against": song.data["cleaned_fingerprint"],
        }
        # Run the health check.
        found = _health_scan(song, cleaned)
        prev = song.data.get("health", {}).get("issues", [])
        song.data["health"] = {
            "checked_against": song.data["cleaned_fingerprint"],
            "issues": health.merge_issues(found, prev),
        }
        song.set_stage("fix")
        song.save()
        # Findings, not rows: a collapsed meter row stands for many, and this line is
        # the first number anyone sees about a score. See health.finding_count.
        open_issues = _derived(song)["open_issues"]
        n = health.finding_count(open_issues)
        final = f"Done. {n} issue(s) to review." if n else "Done. No issues found."
        log(final)
        _job_finish(song, "clean")
        hub.emit(slug, {"type": "state"})
    except Exception as exc:  # surface to the UI rather than dying silently
        traceback.print_exc()
        _job_emit(slug, "clean", _error_text(exc), "error")
        _job_finish(song, "clean", _error_text(exc))
        hub.emit(slug, {"type": "state"})


@app.post("/api/songs/{slug}/clean")
async def api_clean(slug: str) -> Dict:
    song = _require(slug)
    if is_recording(song) or is_scanning(song) or not job_state.start_if_idle(
            song.dir, "clean", ("scan", "clean", "render", "upload"),
            state.file_fingerprint(song.source_path("xml"))):
        raise HTTPException(409, "Another scan, clean, render, or upload is already running for this song.")
    asyncio.get_running_loop().run_in_executor(None, _run_clean, slug)
    return {"started": True}


# --------------------------------------------------------------------------
# Fix stage — health check
# --------------------------------------------------------------------------
def _health_scan(song: state.Song, cleaned: str) -> List[Dict]:
    """The health findings, plus anything MuseScore 3 still refuses in this score.

    MuseScore's verdict comes from the clean (`pipeline.check_opens_in_musescore`) and
    holds only for the file it was given: a score saved since then has been through
    MuseScore, so its own check has had its say and those rows go.
    """
    found = health.scan(cleaned)
    opens = song.data.get("verification", {}).get("musescore") or {}
    if opens.get("rejected") and opens.get("checked_against") == state.file_fingerprint(cleaned):
        found += pipeline.musescore_findings(cleaned, opens["rejected"])
    return found


def _rescan(song: state.Song) -> None:
    """Re-check the cleaned score's health and record what it was checked against.

    This owns two fields and nothing else, so it writes only those, onto the state
    as it is on disk *now*. Saving the copy the caller loaded used to undo whatever
    a route had saved in the meantime: the file watcher loaded a song, a lyric
    import rewrote the score and saved its `lyrics` record and stage, and the
    watcher's save put the pre-import state back (#252). `song` is refreshed to
    what was written, so a caller answering with `_derived(song)` is not stale.
    """
    cleaned = song.cleaned_path()
    if not cleaned or not os.path.exists(cleaned):
        return
    fingerprint = state.file_fingerprint(cleaned)
    found = health.scan(cleaned)
    with state.song_lock(song.slug):
        fresh = state.load(song.slug) or song
        current = fresh.data.get("health", {})
        # The score moved again while it was being checked, so these findings are
        # about a file that is gone. Whoever moved it rescans it (the watcher sees
        # the save); writing these now would record the older file over theirs.
        moved = state.file_fingerprint(cleaned) != fingerprint
        # Somebody already checked this exact file and said so — typically the
        # route whose write woke the watcher. Theirs is the record; leave it.
        if not moved and not (fresh.data.get("cleaned_fingerprint") == fingerprint
                              and current.get("checked_against") == fingerprint):
            fresh.data["cleaned_fingerprint"] = fingerprint
            fresh.data["health"] = {
                "checked_against": fingerprint,
                "issues": health.merge_issues(found, current.get("issues", [])),
            }
            fresh.save()
    song.data = fresh.data


@app.post("/api/songs/{slug}/rescan")
def api_rescan(slug: str) -> Dict:
    song = _require(slug)
    _rescan(song)
    return _derived(song)


def _cleaned_or_400(song: state.Song) -> str:
    cleaned = song.cleaned_path()
    if not cleaned or not os.path.exists(cleaned):
        raise HTTPException(400, "Clean the score first")
    return cleaned


@app.get("/api/songs/{slug}/bar")
def api_bar(slug: str, staff: int = 0, measure: int = 0) -> Dict:
    """A bar of the cleaned score to point at, plus the parts and bars to choose from.

    One route rather than two: the panel needs the choices before it can ask for a
    bar, and both come off the same parse of the same file.
    """
    song = _require(slug)
    cleaned = _cleaned_or_400(song)
    try:
        parts, measures = pipeline.score_parts_and_measures(cleaned)
    except Exception as exc:
        raise HTTPException(500, str(exc))
    out = {"parts": parts, "measures": measures,
           # Said before the write, not after: re-slurring a bar takes a syllable out
           # of it, so lyrics already imported come back one too long.
           "lyrics_imported": bool(song.data.get("lyrics", {}).get("json"))}
    if not staff or not measure:
        return out
    try:
        return {**out, **pipeline.bar_for_fix(cleaned, staff, measure)}
    except FixError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.post("/api/songs/{slug}/fixes/slur")
def api_record_slur(slug: str, body: Dict) -> Dict:
    """Record a missing slur against the cleaned score, and apply it.

    The judgement is a person's — a slur joins different pitches, so nothing upstream
    will guess one back. All this does is put it somewhere that survives a re-clean.
    """
    song = _require(slug)
    cleaned = _cleaned_or_400(song)
    body = body or {}
    try:
        staff, measure = int(body.get("staff", 0)), int(body.get("measure", 0))
        index, span = int(body.get("index", 0)), int(body.get("span", 1))
    except (TypeError, ValueError):
        raise HTTPException(400, "staff, measure, index and span have to be numbers")
    try:
        done = pipeline.record_slur_fix(
            song.dir, cleaned, staff, measure, index, span, body.get("why", ""))
    except FixError as exc:
        raise HTTPException(400, str(exc))
    except (OSError, RuntimeError) as exc:
        raise HTTPException(500, str(exc))
    # Straight to the log, not through job_state: this is instant, and opening a job
    # for it would leave a "fix" job showing as running with nothing to finish it.
    hub.emit(slug, {"type": "log", "line": f"Recorded a missing slur — {done['applied']}"})
    # Our own write, so claim it: otherwise the file watcher reads the score as
    # edited in MuseScore and re-checks it a second time.
    _rescan(song)
    return _derived(song)


@app.get("/api/songs/{slug}/readings")
def api_readings(slug: str) -> Dict:
    """The unsure bars homr offered other readings of, matched to the cleaned score.

    Its own route rather than part of the song state: finding them parses every
    fragment and the cleaned score, which only the Fix panel needs.
    """
    song = _require(slug)
    cleaned = _cleaned_or_400(song)
    try:
        found = bar_readings.offers(song, cleaned)
    except (OSError, RuntimeError, etree.XMLSyntaxError) as exc:
        raise HTTPException(500, str(exc))
    return {"offers": found}


@app.get("/api/songs/{slug}/readings/{offer}/{letter}.svg")
def api_reading_svg(slug: str, offer: str, letter: str):
    """One reading of one bar, engraved."""
    song = _require(slug)
    _cleaned_or_400(song)
    try:
        svg = bar_readings.option_svg(song, offer, letter)
    except FixError as exc:
        raise HTTPException(404, str(exc))
    except (OSError, RuntimeError, StopIteration) as exc:
        raise HTTPException(500, str(exc))
    return Response(svg, media_type="image/svg+xml", headers=dict(REVALIDATE))


@app.post("/api/songs/{slug}/readings/pick")
def api_pick_reading(slug: str, body: Dict) -> Dict:
    """Apply the reading a person picked against the page, or record that none fits."""
    song = _require(slug)
    _cleaned_or_400(song)
    body = body or {}
    offer, choice = str(body.get("offer") or ""), str(body.get("choice") or "")
    if not offer or not choice:
        raise HTTPException(400, "say which bar (offer) and which reading (choice)")
    # A clean or render reads the score this rewrites.
    if is_scanning(song) or any(
            job_state.is_running(song.dir, kind) for kind in ("clean", "render", "upload")):
        raise HTTPException(409, "A scan, clean, render, or upload is running for this "
                                 "song — wait for it to finish, then pick again.")
    try:
        done = bar_readings.record_pick(song, offer, choice)
    except FixError as exc:
        raise HTTPException(400, str(exc))
    except (OSError, RuntimeError) as exc:
        raise HTTPException(500, str(exc))
    hub.emit(slug, {"type": "log", "line": f"Picked a reading — {done['applied']}"})
    # Our own write, as with a recorded slur: claim it, or the watcher re-checks it.
    _rescan(song)
    return _derived(song)


@app.get("/api/songs/{slug}/problems")
def api_problems(slug: str) -> Dict:
    """Every problem of the cleaned score, one row per bar and part, with its choices.

    Its own route for the same reason `/readings` is: it parses the cleaned score and
    every fragment, which only the Fix panel needs.
    """
    song = _require(slug)
    _cleaned_or_400(song)
    try:
        rows = problems.problems(song)
    except (OSError, RuntimeError, etree.XMLSyntaxError) as exc:
        raise HTTPException(500, str(exc))
    return {"rows": rows}


@app.post("/api/songs/{slug}/problems/pick")
def api_pick_problem(slug: str, body: Dict) -> Dict:
    """Apply a person's answer to one problem's choice: a reading, a slur, a repeat or its brackets."""
    song = _require(slug)
    _cleaned_or_400(song)
    body = body or {}
    choice, kind, letter = (str(body.get(k) or "") for k in ("choice", "kind", "letter"))
    if not choice or not letter:
        raise HTTPException(400, "say which problem (choice) and which answer (letter)")
    if is_scanning(song) or any(
            job_state.is_running(song.dir, k) for k in ("clean", "render", "upload")):
        raise HTTPException(409, "A scan, clean, render, or upload is running for this "
                                 "song — wait for it to finish, then pick again.")
    try:
        if kind == "slur":
            done = problems.record_slur_choice(song, choice, letter)
        elif kind == "repeat":
            done = problems.record_repeat_choice(song, choice, letter)
        elif kind == "volta":
            done = problems.record_volta_choice(song, choice, letter)
        else:
            done = bar_readings.record_pick(song, choice, letter)
    except FixError as exc:
        raise HTTPException(400, str(exc))
    except (OSError, RuntimeError) as exc:
        raise HTTPException(500, str(exc))
    hub.emit(slug, {"type": "log", "line": f"Picked {letter} — {done['applied']}"})
    _rescan(song)
    return _derived(song)


@app.post("/api/songs/{slug}/issues/{issue_id}/dismiss")
def api_dismiss(slug: str, issue_id: str) -> Dict:
    song = _require(slug)
    for i in song.data.get("health", {}).get("issues", []):
        if i["id"] == issue_id:
            i["status"] = "dismissed"
    song.save()
    return _derived(song)


def _rename_uploads_task(slug: str, old_name: str, new_name: str) -> None:
    song = _require(slug)
    log = lambda m: hub.emit(slug, {"type": "log", "line": m})
    try:
        from src.stemmanauha.upload_to_youtube import rename_uploads
        uploads = song.data.get("record", {}).get("uploads", [])
        log("Updating YouTube titles…")
        updated = rename_uploads(uploads, old_name, new_name, log=log)
        song.data.setdefault("record", {})["uploads"] = updated
        song.save()
        log("YouTube titles updated.")
    except Exception as exc:
        traceback.print_exc()
        hub.emit(slug, {"type": "error", "line": f"YouTube rename failed: {exc}"})
    finally:
        hub.emit(slug, {"type": "state"})


@app.post("/api/songs/{slug}/rename")
async def api_rename(slug: str, body: Dict) -> Dict:
    """Change the song's display name; retitle uploaded YouTube videos if any."""
    song = _require(slug)
    new_name = (body or {}).get("name", "").strip()
    if not new_name:
        raise HTTPException(400, "Name is required")
    old_name = song.name
    song.data["name"] = new_name
    song.save()
    uploads = song.data.get("record", {}).get("uploads", [])
    if uploads and new_name != old_name:
        asyncio.get_running_loop().run_in_executor(
            None, _rename_uploads_task, slug, old_name, new_name)
    return _derived(song)


@app.post("/api/songs/{slug}/mode")
def api_set_mode(slug: str, body: Dict) -> Dict:
    """Switch a song between normal and per-system cleaning."""
    song = _require(slug)
    mode = (body or {}).get("mode")
    if mode not in ("normal", "per-system"):
        raise HTTPException(400, "mode must be 'normal' or 'per-system'")
    song.data["mode"] = mode
    song.save()
    return _derived(song)


@app.post("/api/songs/{slug}/stage/{stage}")
def api_set_stage(slug: str, stage: str) -> Dict:
    """Manual stage navigation (left rail)."""
    song = _require(slug)
    if stage not in state.STAGES:
        raise HTTPException(400, "Unknown stage")
    song.set_stage(stage)
    song.save()
    return _derived(song)


@app.post("/api/songs/{slug}/approve-review")
def api_approve_review(slug: str, body: Dict = None) -> Dict:
    """Record which cleaned score a person approved, then advance to Record."""
    song = _require(slug)
    cleaned = song.cleaned_path()
    approved_against = state.file_fingerprint(cleaned) if cleaned else None
    if not approved_against:
        raise HTTPException(400, "There is no cleaned score to approve")
    expected = (body or {}).get("cleaned_fingerprint")
    if not expected or expected != approved_against:
        raise HTTPException(409, "The score changed; review the current version before approving")
    song.data["review"] = {"approved_against": approved_against}
    if "scan" in song.data:
        # What was approved is a score read off the page, so the approval is
        # derived from the scan too: re-reading a system means nobody has seen
        # what would be recorded now.
        song.data["review"]["scan_revision"] = scan.revision(song)
    song.set_stage("record")
    song.save()
    return _derived(song)


# --------------------------------------------------------------------------
# Lyrics stage
# --------------------------------------------------------------------------
@app.get("/api/playlists")
def api_playlists() -> List[Dict]:
    return state.load_playlists()


def _youtube_failed(exc: Exception) -> HTTPException:
    """YouTube refusing or unreachable, said in words (#338)."""
    from src.stemmanauha.upload_to_youtube import QuotaExceeded
    if isinstance(exc, QuotaExceeded):
        return HTTPException(503, str(exc))
    return HTTPException(503, f"YouTube could not be reached: {exc}")


@app.post("/api/playlists")
def api_remember_playlist(body: Dict) -> List[Dict]:
    """Offer one more of the account's playlists for songs to be picked into."""
    playlist_id = (body.get("playlist_id") or "").strip()
    if not playlist_id:
        raise HTTPException(400, "Which playlist?")
    state.save_playlist(playlist_id, body.get("title") or None)
    return state.load_playlists()


@app.delete("/api/playlists/{playlist_id}")
def api_forget_playlist(playlist_id: str) -> List[Dict]:
    """Stop offering a playlist. It is not touched on YouTube."""
    state.forget_playlist(playlist_id)
    return state.load_playlists()


@app.get("/api/youtube-playlists")
def api_youtube_playlists() -> List[Dict]:
    try:
        return playlists.account_playlists()
    except Exception as exc:
        raise _youtube_failed(exc)


@app.get("/api/songs/{slug}/playlists")
def api_song_playlists(slug: str) -> Dict:
    song = _require(slug)
    try:
        return playlists.song_playlists(song)
    except playlists.PlaylistError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise _youtube_failed(exc)


@app.post("/api/songs/{slug}/playlists")
def api_song_playlist_membership(slug: str, body: Dict) -> Dict:
    """Put this song's videos into a playlist, or take them out (#338)."""
    song = _require(slug)
    if is_recording(song):
        raise HTTPException(409, "A recording or upload is running; try again when it is done.")
    log = lambda m: hub.emit(slug, {"type": "log", "line": m})
    try:
        row = playlists.set_membership(song, (body.get("playlist_id") or "").strip(),
                                       bool(body.get("member")), log,
                                       title=body.get("title") or None)
    except playlists.PlaylistError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise _youtube_failed(exc)
    # The site lists the choir's playlists, so it would otherwise wait for its schedule.
    site_refresh.refresh_stemmanauhat(log)
    return row


@app.get("/api/prompt")
def api_prompt() -> Dict:
    path = os.path.join(SCRIPT_DIR, "lyric_json_prompt.txt")
    with open(path, "r", encoding="utf-8") as f:
        return {"prompt": f.read()}


@app.get("/api/songs/{slug}/lyric-grid")
def api_lyric_grid(slug: str) -> Dict:
    """Structure for the manual lyric editor: the parts, the printed systems, the text."""
    song = _require(slug)
    cleaned = song.cleaned_path()
    if not cleaned or not os.path.exists(cleaned):
        raise HTTPException(400, "Clean the score first")
    return pipeline.lyric_grid(cleaned, song.dir)


@app.get("/api/songs/{slug}/lyrics-json")
def api_lyrics_json(slug: str):
    song = _require(slug)
    path = song.lyrics_json_path()
    if not path or not os.path.exists(path):
        return PlainTextResponse("")
    with open(path, "r", encoding="utf-8") as f:
        return PlainTextResponse(f.read())


@app.post("/api/songs/{slug}/lyrics")
def api_lyrics(slug: str, body: Dict) -> Dict:
    """Import lyrics: either pasted JSON (`json`) or the manual editor's cells (`cells`)."""
    song = _require(slug)
    cleaned = song.cleaned_path()
    if not cleaned or not os.path.exists(cleaned):
        raise HTTPException(400, "Clean the score first")
    body = body or {}
    cells = body.get("cells")
    if cells:
        blocks = pipeline.lyric_blocks(cleaned, cells, song.dir)
        if not blocks:
            raise HTTPException(400, "Nothing typed yet")
        json_text = json.dumps(blocks, ensure_ascii=False, indent=2)
    else:
        json_text = body.get("json", "")
        if not json_text.strip():
            raise HTTPException(400, "Paste the lyric JSON first")
    json_path = song.path("lyrics.json")
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(json_text)
    previous_fingerprint = state.file_fingerprint(cleaned)
    try:
        result = pipeline.run_lyric_import(json_path, cleaned, replace=True)
    except Exception as exc:
        raise HTTPException(400, f"Import failed: {exc}")
    current_fingerprint = state.file_fingerprint(cleaned)
    # Claim the write before the slow health check below (#336). The file watcher
    # sees the score change at once, and until the new fingerprint is on disk it
    # takes the import for an edit made in MuseScore: it checks the score again and
    # tells the page the score moved, so the page fetches every system's picture
    # again instead of the one or two the import changed.
    with state.song_lock(song.slug):
        claimed = state.load(song.slug) or song
        claimed.data["cleaned_fingerprint"] = current_fingerprint
        claimed.save()
    song.data["lyrics"] = {
        "json": "lyrics.json",
        "imported_against": current_fingerprint,
        "warnings": [m.to_dict() for m in result.mismatches],
    }
    # Import may add full-measure rests to otherwise empty measures, so health must
    # be checked again rather than rebound to the new fingerprint without evidence.
    # Pitch events do not change during lyric placement, so this narrower result can
    # safely follow the controlled XML edit without repeating the source comparison.
    # Nor do note lengths, which are all MuseScore's own check looks at.
    for name in ("notes", "musescore"):
        data = song.data.get("verification", {}).get(name, {})
        if data.get("checked_against") == previous_fingerprint:
            data["checked_against"] = current_fingerprint
    previous_issues = song.data.get("health", {}).get("issues", [])
    song.data["health"] = {
        "checked_against": current_fingerprint,
        "issues": health.merge_issues(_health_scan(song, cleaned), previous_issues),
    }
    song.data["cleaned_fingerprint"] = current_fingerprint
    if result.ok:
        song.set_stage("review")
    song.save()
    # The bars that did not take their words, said in the reply itself rather than
    # left for the caller to dig out of the song's `lyrics.warnings` (#340).
    return {**_derived(song), "mismatches": song.data["lyrics"]["warnings"]}


# --------------------------------------------------------------------------
# Files + local app actions
# --------------------------------------------------------------------------
def _song_pdf(song) -> str:
    pdf = song.source_path("pdf")
    if not pdf or not os.path.exists(pdf):
        raise HTTPException(404, "No PDF")
    return pdf


def _bounds_score(song) -> str:
    """The score to label bounds against: the converted input, which still has
    its line breaks (normal-mode cleaning strips them)."""
    xml = song.source_path("xml")
    if not xml or not os.path.exists(xml):
        return ""
    try:
        return pipeline.convert_to_mscx(xml, song.dir)
    except Exception:
        return ""


def _label_bounds(song, mscx_path: str) -> None:
    """Give the stored bands their bars, now there is a score to read them off."""
    try:
        pipeline.label_system_bounds(song.dir, mscx_path)
    except Exception:  # a label is a convenience; never fail a scan or clean on it
        traceback.print_exc()


@app.get("/api/songs/{slug}/bounds")
def api_bounds(slug: str) -> Dict:
    """Printed-system boundaries, plus what the editor needs to draw them."""
    song = _require(slug)
    pdf = _song_pdf(song)
    systems = pipeline.system_bounds(song.dir)
    declared = pipeline.declared_system_count(_bounds_score(song))
    return {
        "pages": pipeline.page_count(pdf),
        "systems": systems,
        "declared": declared,       # how many systems the score says there are
    }


@app.put("/api/songs/{slug}/bounds")
def api_save_bounds(slug: str, body: Dict = None) -> Dict:
    """Persist edited boundaries: {"systems": [{page, top, bottom}, ...]}."""
    song = _require(slug)
    _song_pdf(song)
    bands = (body or {}).get("systems")
    if bands is None:
        raise HTTPException(400, "No systems given")
    try:
        saved = pipeline.save_system_bounds(song.dir, bands, _bounds_score(song))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, f"Bad bounds: {exc}")
    # Moving a band is changing the input a fragment was read from. Both the
    # bands and the grid answers are keyed by position, so an inserted band
    # silently re-points everything after it — which is why nothing here compares
    # indices; each fragment is checked against the geometry now at its own.
    song = _require(slug)
    return {"systems": saved, "discarded": scan.reconcile(song)}


@app.post("/api/songs/{slug}/find-systems")
async def api_find_systems(slug: str, body: Dict = None) -> Dict:
    """Propose a band for every printed system, without saving any of them.

    A proposal, not an answer: it comes back to the editor as draggable bands
    the same as any other, and the person looking at the page saves it or does
    not. Nothing here writes `.systems.json`, so a wrong reading costs a drag
    rather than a scan of the wrong music.

    Two ways to find them. `{"method": "quick"}`, the default, reads the page
    itself in well under a second a page and needs no homr. `{"method": "homr"}`
    asks homr, which is seconds a page, so its progress goes to the song's live
    log while the request is still open.
    """
    song = _require(slug)
    pdf = _song_pdf(song)
    method = (body or {}).get("method") or "quick"
    if method not in ("quick", "homr"):
        raise HTTPException(400, f"Unknown method {method!r}: use quick or homr")
    if method == "quick":
        log = lambda m: hub.emit(slug, {"type": "log", "line": m})
        try:
            found = await asyncio.get_running_loop().run_in_executor(
                None, lambda: pipeline.quick_system_bands(song.dir, pdf, log=log))
        except Exception as exc:
            traceback.print_exc()
            raise HTTPException(500, str(exc)) from None
        return {"systems": found}
    try:
        reader = homr_install.begin_read()
    except homr_install.Refused as exc:
        raise HTTPException(409, str(exc)) from None
    with reader:
        return await _find_with_homr(slug, pdf, body)


async def _find_with_homr(slug: str, pdf: str, body: Optional[Dict]) -> Dict:
    key = (body or {}).get("engine")
    engine = None
    if key and key != omr.DEFAULT_ENGINE:
        try:
            engine = omr.engine_for(key)
        except omr.HomrMissing as exc:
            raise HTTPException(400, str(exc)) from None

    def run() -> List[Dict]:
        log = lambda m: hub.emit(slug, {"type": "log", "line": m})
        return [b.to_dict() for b in
                system_finder.find_bands(pdf, engine=engine, log=log)]

    try:
        found = await asyncio.get_running_loop().run_in_executor(None, run)
    except omr.HomrError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(500, str(exc)) from None
    return {"systems": found}


@app.get("/api/songs/{slug}/page/{page}")
def api_page(slug: str, page: int, dpi: int = 150, grid: bool = False):
    """One rasterised page of the original PDF, for the bounds editor."""
    song = _require(slug)
    pdf = _song_pdf(song)
    if page < 1 or page > pipeline.page_count(pdf):
        raise HTTPException(404, "No such page")
    try:
        path = pipeline.page_image(song.dir, pdf, page, max(50, min(dpi, 600)), grid)
    except Exception as exc:
        raise HTTPException(500, str(exc))
    return FileResponse(path, media_type="image/png", headers=dict(REVALIDATE))


def _printed_systems(song) -> list:
    """The bars that begin a printed system, for the scrolling video's bar numbers.

    Read off the converted input rather than the cleaned score: normal-mode
    cleaning strips the line breaks, so the cleaned file usually cannot say where
    the page's systems were. Empty when the source never had breaks — the renderer
    then falls back to numbering at a regular interval.
    """
    return pipeline.cleaned_system_starts(song.dir, _bounds_score(song))


def _cleaned_breaks(song) -> tuple:
    """(cleaned .mscx, printed line breaks) for the compare view, or (None, [])."""
    cleaned = song.cleaned_path()
    if not cleaned or not os.path.exists(cleaned):
        return None, []
    return cleaned, pipeline.cleaned_line_breaks(song.dir, _bounds_score(song))


@app.get("/api/songs/{slug}/compare")
def api_compare(slug: str) -> Dict:
    """Printed systems paired with the same systems of the cleaned score."""
    song = _require(slug)
    _song_pdf(song)
    cleaned, breaks = _cleaned_breaks(song)
    if not cleaned:
        raise HTTPException(400, "Clean the score first")
    try:
        systems = pipeline.compare_systems(song.dir, cleaned, breaks)
    except Exception as exc:
        raise HTTPException(500, str(exc))
    return {"systems": systems}


@app.get("/api/songs/{slug}/cleaned-system/{index}")
def api_cleaned_system(slug: str, index: int, dpi: int = 200):
    """One system of the cleaned score, cropped from its render."""
    song = _require(slug)
    cleaned, breaks = _cleaned_breaks(song)
    if not cleaned:
        raise HTTPException(404, "No cleaned score yet")
    try:
        path = pipeline.cleaned_system_crop(
            song.dir, cleaned, breaks, index, max(50, min(dpi, 600)))
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))
    return FileResponse(path, media_type="image/png", headers=dict(REVALIDATE))


@app.get("/api/songs/{slug}/system/{index}")
def api_system_image(slug: str, index: int, dpi: int = 400):
    """One printed system, cropped from the stored bounds."""
    song = _require(slug)
    pdf = _song_pdf(song)
    try:
        path = pipeline.system_crop(song.dir, pdf, index, max(50, min(dpi, 600)))
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))
    return FileResponse(path, media_type="image/png", headers=dict(REVALIDATE))


@app.get("/api/songs/{slug}/pdf")
def api_pdf(slug: str):
    song = _require(slug)
    pdf = song.source_path("pdf")
    if not pdf or not os.path.exists(pdf):
        raise HTTPException(404, "No PDF")
    return FileResponse(pdf, media_type="application/pdf", headers=dict(REVALIDATE))


@app.get("/api/songs/{slug}/render")
def api_render(slug: str, doc: str = "cleaned"):
    """Render a score variant to PDF via MuseScore, for the viewer tabs.

    doc = original          -> the OCR'd input score (converted to .mscx)
          cleaned_nolyrics  -> the cleaned score with lyrics stripped
          cleaned           -> the cleaned score as-is (with lyrics)
    """
    song = _require(slug)
    try:
        breaks = None
        tidy = False
        if doc == "original":
            xml = song.source_path("xml")
            if not xml or not os.path.exists(xml):
                raise HTTPException(404, "No source score")
            mscx = pipeline.convert_to_mscx(xml, song.dir)
        else:
            cleaned = song.cleaned_path()
            if not cleaned or not os.path.exists(cleaned):
                raise HTTPException(404, "No cleaned score yet")
            mscx = pipeline.strip_lyrics_copy(cleaned) if doc == "cleaned_nolyrics" else cleaned
            # Lay the cleaned score out like the page it came from, so the two can
            # be read side by side. The breaks come from the converted input, which
            # usually has them -- when it does not, the render is unchanged.
            xml = song.source_path("xml")
            if xml and os.path.exists(xml):
                breaks = pipeline.cleaned_line_breaks(
                    song.dir, pipeline.convert_to_mscx(xml, song.dir)) or None
            tidy = True
        rendered = pipeline.render_score_pdf(mscx, breaks, tidy=tidy)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc))
    return FileResponse(rendered, media_type="application/pdf", headers=dict(REVALIDATE))


@app.post("/api/songs/{slug}/open-score")
def api_open_score(slug: str) -> Dict:
    song = _require(slug)
    cleaned = song.cleaned_path()
    if not cleaned or not os.path.exists(cleaned):
        raise HTTPException(400, "Nothing to open")
    subprocess.Popen(["open", "-a", "MuseScore 3", cleaned])
    return {"ok": True}


@app.get("/api/songs/{slug}/score-file")
def api_score_file(slug: str):
    """The cleaned score, as a download.

    `open-score` above only works for somebody sitting at this host. This is the
    other half of the same idea for everybody else: take the file to whatever
    machine has MuseScore on it.
    """
    song = _require(slug)
    cleaned = _cleaned_or_400(song)
    return FileResponse(cleaned, media_type="application/octet-stream",
                        filename=os.path.basename(cleaned), headers=dict(REVALIDATE))


@app.post("/api/songs/{slug}/score-file")
async def api_upload_score_file(slug: str, file: UploadFile = None) -> Dict:
    """Put a fixed score back, and re-check it.

    The same thing the file watcher does when the score is saved in MuseScore on
    this host — which is what makes this an edit rather than a new stage: the
    health check runs against the new file, and an approval given against the old
    one lapses because the fingerprint it was recorded against has moved.
    """
    song = _require(slug)
    cleaned = _cleaned_or_400(song)
    if not file or not file.filename:
        raise HTTPException(400, "No file was uploaded")
    if is_recording(song) or is_scanning(song) or any(
            job_state.is_running(song.dir, kind) for kind in ("clean", "render", "upload")):
        raise HTTPException(409, "A scan, clean, render, or upload is running for this song — "
                                 "replacing the score underneath it would be read half-written.")
    try:
        summary = pipeline.accept_uploaded_score(cleaned, file.filename, await file.read())
    except pipeline.ScoreUploadError as exc:
        raise HTTPException(400, str(exc))
    except OSError as exc:
        raise HTTPException(500, str(exc))
    hub.emit(slug, {"type": "log", "line": (
        f"Replaced the cleaned score with {os.path.basename(file.filename)} — "
        f"{summary['staves']} staves, {summary['measures']} bars")})
    # Our own write, so claim it: otherwise the file watcher reads it as a MuseScore
    # edit and checks the same file a second time.
    _rescan(song)
    return _derived(song)


@app.post("/api/songs/{slug}/reveal-pdf")
def api_reveal_pdf(slug: str) -> Dict:
    song = _require(slug)
    pdf = song.source_path("pdf")
    if not pdf or not os.path.exists(pdf):
        raise HTTPException(404, "No PDF")
    subprocess.Popen(["open", "-R", pdf])
    return {"ok": True}


MARGIN_LIMITS = (-40.0, 100.0)

# What the margins start at when a song has never been asked. The renderer's own
# default is 0 — "leave the framing alone" — but the app wants a little white
# space under the bottom staff, so the lowest lyrics are not against the frame
# edge. `static/app.js` prefills the same numbers; keep the two in step.
DEFAULT_TOP_MARGIN_PERCENT = 0.0
DEFAULT_BOTTOM_MARGIN_PERCENT = 5.0


def _margin(value, label: str) -> float:
    """One video margin, as a number inside the range the renderer accepts."""
    try:
        margin = float(value if value is not None else 0.0)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{label} video margin must be a number") from None
    low, high = MARGIN_LIMITS
    if not low <= margin <= high:
        raise HTTPException(
            400, f"{label} video margin must be between {low:g}% and {high:g}%")
    return margin


def _staff_groups(cleaned: str, text) -> str:
    """A staff grouping ("S1+S2, A1+A2") checked against this score, written the
    one way it is stored. Blank is no grouping; a part the video does not have is
    a 400 saying which."""
    try:
        return format_groups(pipeline.staff_groups(cleaned, str(text or "")))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


def _remember_record_settings(song: state.Song, **wanted) -> None:
    """Keep the Record settings this song was last shown or rendered with.

    The framing (margins, and the staff grouping of #246 — it changes what is
    drawn, not what is heard), the output quality, the tempo the app supplies and
    the encoder choice. Written when a render is asked for, when the panel's
    Preview or Save settings is pressed (#301), and when a preview succeeds, so
    nudging a value to see what it looks like is enough to keep it — that is the
    moment the choice is actually made. Only a real change is written, and never
    while a job is running: the state file is saved whole, so a needless write
    here could land on top of what a finishing render just recorded. Pass a
    freshly loaded song for the same reason.
    """
    rec = song.data.get("record", {})
    if all(rec.get(key) == value for key, value in wanted.items()):
        return
    if is_recording(song):
        return
    song.data.setdefault("record", {}).update(wanted)
    song.save()


def _scroll_settings(song: state.Song, opts: Dict) -> Dict:
    """The scrolling renderer's settings out of a request, checked the way a render
    checks them; anything the request leaves out falls back to what this song
    chose last, then to the app-wide default.

    One place for both the render and Save settings, so a value the render would
    refuse is refused on save too, with the same message. The staff grouping and
    the tempo need the cleaned score; without one they are left out. The tempo is
    left out too when the score carries its own, since it would be ignored.
    """
    remembered = song.data.get("record", {})
    quality = opts.get("quality", remembered.get("quality"))
    out = {
        "quality": quality if quality in pipeline.SCROLL_QUALITY else "4k",
        "hardware_encoding": opts.get(
            "hardware_encoding", remembered.get("hardware_encoding")) is not False,
    }
    for key, label, default in (
            ("top_margin", "Top", DEFAULT_TOP_MARGIN_PERCENT),
            ("bottom_margin", "Bottom", DEFAULT_BOTTOM_MARGIN_PERCENT)):
        out[key] = _margin(opts.get(key, remembered.get(key, default)), label)
    cleaned = song.cleaned_path()
    if cleaned and os.path.exists(cleaned):
        out["staff_groups"] = _staff_groups(
            cleaned, opts.get("staff_groups", remembered.get("staff_groups", "")))
        if not pipeline.has_opening_tempo(cleaned):
            try:
                bpm = int(opts.get("bpm", remembered.get("bpm", 80)))
            except (TypeError, ValueError):
                raise HTTPException(400, "BPM must be a whole number") from None
            if not 20 <= bpm <= 300:
                raise HTTPException(400, "BPM must be between 20 and 300")
            out["bpm"] = bpm
    return out


@app.get("/api/songs/{slug}/scroll-preview")
async def api_scroll_preview(slug: str, quality: str = "4k",
                             top_margin: float = DEFAULT_TOP_MARGIN_PERCENT,
                             bottom_margin: float = DEFAULT_BOTTOM_MARGIN_PERCENT,
                             bpm: Optional[int] = None, staff_groups: str = ""):
    """The scrolling render as pictures the browser can play, before any video exists.

    This is the picture without the encoding: the same engraving, viewport, clock,
    scroll curve and *pixels* `build_videos` would use, drawn by the same code at a
    height a page can carry. It does not count as a render — no stage moves and no
    video appears; the only files it leaves behind are its own cache. The one thing
    it does record is the settings it was asked for (`_remember_record_settings`), because
    nudging a margin and looking at the result *is* how the choice gets made, and
    having to render before it would stick lost it every time.

    It also fails where a render would, and that is half its value: a D.C./D.S.
    jump that cannot be matched to the page or margins that leave no picture come
    back here as an ordinary error message, seconds in, instead of after minutes of engraving and encoding.
    """
    song = _require(slug)
    cleaned = song.cleaned_path()
    if not cleaned or not os.path.exists(cleaned):
        raise HTTPException(400, "No cleaned score yet — clean the song first.")
    settings = {
        "quality": quality if quality in pipeline.SCROLL_QUALITY else "4k",
        "top_margin_percent": _margin(top_margin, "Top"),
        "bottom_margin_percent": _margin(bottom_margin, "Bottom"),
        # A score with its own opening tempo ignores this, so it is only part of
        # what the preview is *of* when the app is the one supplying the tempo.
        "initial_bpm": bpm if bpm and not pipeline.has_opening_tempo(cleaned) else None,
        # The picture shows a bar number where the page started a system, so the
        # preview has to be told the same grouping the render is told.
        "system_starts": _printed_systems(song),
    }
    groups = _staff_groups(cleaned, staff_groups)
    settings["staff_groups"] = parse_groups(groups)
    try:
        payload = await asyncio.get_running_loop().run_in_executor(
            None, lambda: pipeline.scroll_preview(song.dir, cleaned, **settings))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc) or exc.__class__.__name__)
    # Only once the picture came out: a framing the renderer refuses is not one to
    # come back to. Reloaded, because preparing can take seconds.
    remember = {"quality": settings["quality"],
                "top_margin": settings["top_margin_percent"],
                "bottom_margin": settings["bottom_margin_percent"],
                "staff_groups": groups}
    if settings["initial_bpm"]:
        remember["bpm"] = settings["initial_bpm"]
    _remember_record_settings(_require(slug), **remember)
    return JSONResponse(payload, headers=dict(REVALIDATE))


@app.get("/api/songs/{slug}/scroll-preview/{name}")
async def api_scroll_preview_tile(slug: str, name: str):
    """One tile of the prepared preview strip.

    The payload names these; preparing writes them. They are served rather than
    inlined because a score's strip is megabytes of PNG, and a phone should get it
    as images the browser can cache and decode, not as base64 inside JSON.
    """
    song = _require(slug)
    path = pipeline.scroll_preview_tile(song.dir, name)
    if not path:
        raise HTTPException(404, "No such preview tile")
    return FileResponse(path, media_type="image/png", headers=dict(REVALIDATE))


@app.get("/api/songs/{slug}/scroll-preview-audio")
async def api_scroll_preview_audio(slug: str, revision: str, mix: str = "ALL",
                                   quality: str = "4k",
                                   top_margin: float = DEFAULT_TOP_MARGIN_PERCENT,
                                   bottom_margin: float = DEFAULT_BOTTOM_MARGIN_PERCENT,
                                   bpm: Optional[int] = None, staff_groups: str = ""):
    """One selected MuseScore mix, prepared lazily for the browser preview."""
    song = _require(slug)
    cleaned = song.cleaned_path()
    if not cleaned or not os.path.exists(cleaned):
        raise HTTPException(400, "No cleaned score yet — clean the song first.")
    settings = {
        "quality": quality if quality in pipeline.SCROLL_QUALITY else "4k",
        "top_margin_percent": _margin(top_margin, "Top"),
        "bottom_margin_percent": _margin(bottom_margin, "Bottom"),
        "initial_bpm": bpm if bpm and not pipeline.has_opening_tempo(cleaned) else None,
        "system_starts": _printed_systems(song),
    }
    settings["staff_groups"] = parse_groups(_staff_groups(cleaned, staff_groups))
    try:
        path, reused = await asyncio.get_running_loop().run_in_executor(
            None, lambda: pipeline.scroll_preview_audio(
                song.dir, cleaned, mix, revision, **settings))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc) or exc.__class__.__name__)
    headers = {**REVALIDATE, "X-Scroll-Audio-Cache": "hit" if reused else "miss"}
    return FileResponse(path, media_type="audio/wav", headers=headers)


# --------------------------------------------------------------------------
# Record stage
# --------------------------------------------------------------------------
def _run_record(slug: str, opts: Dict) -> None:
    song = _require(slug)
    start_fingerprint = opts.get("_source_fingerprint")
    previous_record = song.data.get("record", {})
    previous_rendered_against = previous_record.get("rendered_against")
    legacy_screen_against = previous_rendered_against \
        if previous_record.get("renderer") in (None, "screen") else None
    previous_audio_against = previous_record.get(
        "audio_rendered_against", legacy_screen_against)
    previous_video_against = previous_record.get(
        "video_rendered_against", legacy_screen_against)
    job_kind = "upload" if opts.get("upload_only") else "render"
    log = lambda m: _job_emit(slug, job_kind, m)
    progress = lambda m: _job_emit(slug, job_kind, m, "progress")

    def on_uploaded(info: Dict) -> None:
        current_song = _require(slug)
        rec = current_song.data.setdefault("record", {})
        rec.setdefault("uploads", []).append(info)
        rec["playlist_id"] = info.get("playlist_id")
        current_song.save()
        hub.emit(slug, {"type": "state"})

    try:
        merge_only = bool(opts.get("merge_only"))
        upload_only = bool(opts.get("upload_only"))

        # Two renderers write the same "<slug> <part>" files into media/video, so
        # everything downstream (review, upload, retitling) is renderer-agnostic.
        # "scroll" renders from the score; "screen" drives MuseScore and records it.
        if (opts.get("renderer") or "scroll") == "scroll" and not (merge_only or upload_only):
            cleaned = song.cleaned_path()
            if not cleaned or not os.path.exists(cleaned):
                raise FileNotFoundError("No cleaned score yet — clean the song first.")
            quality = opts.get("quality") or "4k"
            hardware_encoding = opts.get("hardware_encoding") is not False
            top_margin = float(opts.get("top_margin", DEFAULT_TOP_MARGIN_PERCENT))
            bottom_margin = float(opts.get("bottom_margin",
                                           DEFAULT_BOTTOM_MARGIN_PERCENT))
            margin_options = {
                "top_margin_percent": top_margin,
                "bottom_margin_percent": bottom_margin,
            }
            log(f"Rendering the scrolling video ({quality})…")
            # Minutes of every core the host has, so it waits its turn behind
            # anything else heavy running here — an agent's test suite, a scan,
            # another song's render. The slot is AgentDeck's host-wide pool; a
            # deck that is down or busy costs a log line, never the render.
            # Losing a slot we were granted is the other way round: the render is
            # guarded through its own progress reports and stops, because another
            # job may already have been told the cores are free.
            with heavy_slot.heavy_slot(f"song app render {song.slug}", log=log) as slot:
                outputs = pipeline.run_scroll_video(song.dir, cleaned, song.slug,
                                                    quality=quality,
                                                    hardware_encoding=hardware_encoding,
                                                    initial_bpm=opts.get("bpm"),
                                                    system_starts=_printed_systems(song),
                                                    staff_groups=parse_groups(
                                                        opts.get("staff_groups")),
                                                    log=slot.guard(log),
                                                    progress=slot.guard(progress),
                                                    **margin_options)
                slot.check()
            song = _require(slug)
            rec = song.data.setdefault("record", {})
            rec["exported"] = True
            rec["renderer"] = "scroll"
            rec["quality"] = quality
            rec["hardware_encoding"] = hardware_encoding
            rec["top_margin"] = top_margin
            rec["bottom_margin"] = bottom_margin
            rec["staff_groups"] = opts.get("staff_groups") or ""
            rec["outputs"] = [os.path.basename(p) for p in outputs]
            rec["rendered_against"] = start_fingerprint
            rec["verification"] = verification.verify_media(
                song, rec["outputs"], verification.singing_parts(cleaned))
            rec["error"] = None
            song.set_stage("upload")
            song.save()
            log(f"Done. {len(outputs)} video(s) ready.")
            _job_finish(song, job_kind)
            return

        from src.stemmanauha.create_video import run
        youtube = bool(opts.get("youtube")) or upload_only
        if youtube:  # fresh upload run — clear any stale record of prior uploads
            song.data.setdefault("record", {})["uploads"] = []
            song.save()
            if opts.get("playlist"):  # remember the chosen target playlist
                state.save_playlist(opts["playlist"], opts.get("playlist_title"))
        log("Uploading to YouTube…" if upload_only
            else "Re-merging with new offset…" if merge_only
            else "Starting recording pipeline…")
        redo_mp3 = bool(opts.get("redo_mp3"))
        redo_video = bool(opts.get("redo_video"))
        if not (merge_only or upload_only):
            stale_audio = previous_audio_against != start_fingerprint
            stale_video = previous_video_against != start_fingerprint
            if stale_audio:
                redo_mp3 = True
            if stale_video:
                redo_video = True
            if stale_audio or stale_video:
                log("The score changed; refreshing its MP3 audio and screen recording.")
        results = run(
            song_dir=song.dir,
            youtube=youtube,
            extra_playlist_id=opts.get("playlist") or None,
            audio_delay_ms=int(opts.get("audio_delay_ms", 1300)),
            redo_mp3=redo_mp3,
            redo_video=redo_video,
            merge_only=merge_only,
            upload_only=upload_only,
            log=log, progress=progress,
            display_name=song.name, on_uploaded=on_uploaded,
            existing_outputs=opts.get("_existing_outputs"),
        )
        song = _require(slug)
        rec = song.data.setdefault("record", {})
        if not upload_only:
            rec["exported"] = True
            rec["renderer"] = "screen"
            rec["audio_delay_ms"] = int(opts.get("audio_delay_ms", 1300))
            rec["outputs"] = [os.path.basename(str(r)) for r in (results or [])]
            cleaned = song.cleaned_path()
            if redo_mp3:
                rec["audio_rendered_against"] = start_fingerprint
            if redo_video:
                rec["video_rendered_against"] = start_fingerprint
            audio_against = rec.get("audio_rendered_against", previous_audio_against)
            video_against = rec.get("video_rendered_against", previous_video_against)
            rec["rendered_against"] = start_fingerprint \
                if audio_against == video_against == start_fingerprint \
                else previous_rendered_against
            rec["verification"] = verification.verify_media(
                song, rec["outputs"], verification.singing_parts(cleaned))
        if youtube and rec.get("uploads"):
            # One refresh picks up every video this run uploaded (#321).
            rec["site_refresh"] = {"at": time.time(),
                                   "ok": site_refresh.refresh_stemmanauhat(log)}
        rec["error"] = None
        # After recording, move on to the Upload stage; uploading stays there.
        if not merge_only:
            song.set_stage("upload")
        song.save()
        log("Upload complete." if upload_only else f"Done. {len(results or [])} video(s) ready.")
        _job_finish(song, job_kind)
    except Exception as exc:
        traceback.print_exc()
        song.data.setdefault("record", {})["error"] = str(exc)
        song.save()
        _job_emit(slug, job_kind, str(exc), "error")
        _job_finish(song, job_kind, str(exc))
    finally:
        lock = _lock_path(song)
        if os.path.exists(lock):
            os.remove(lock)
        hub.emit(slug, {"type": "state"})


RECORD_SETTINGS = ("quality", "hardware_encoding", "top_margin", "bottom_margin",
                   "staff_groups", "bpm")


@app.post("/api/songs/{slug}/record-settings")
async def api_record_settings(slug: str, body: Dict = None) -> Dict:
    """Save the scrolling renderer's settings without rendering (#301).

    The Record panel's Save settings button, and its Preview button before it
    opens the preview. Refused while a job runs: a render writes these same
    fields when it finishes, and the state file is saved whole.
    """
    song = _require(slug)
    if is_recording(song) or is_scanning(song) or job_state.is_running(song.dir, "clean"):
        raise HTTPException(409, "A scan, clean or render is running — save the "
                                 "settings after it finishes.")
    settings = _scroll_settings(song, body or {})
    _remember_record_settings(song, **settings)
    return {"record": {key: song.data.get("record", {}).get(key) for key in RECORD_SETTINGS}}


@app.post("/api/songs/{slug}/record")
async def api_record(slug: str, body: Dict = None) -> Dict:
    song = _require(slug)
    if is_recording(song) or is_scanning(song):
        raise HTTPException(409, "Another scan, clean, render, or upload is already running for this song.")
    opts = body or {}
    source_fingerprint = state.file_fingerprint(song.cleaned_path())
    if not (opts.get("upload_only") or opts.get("merge_only")):
        if not source_fingerprint:
            raise HTTPException(400, "Clean the score before rendering")
        if song.data.get("review", {}).get("approved_against") != source_fingerprint:
            raise HTTPException(409, "Review and approve the current score before rendering")
    scrolling_render = (opts.get("renderer") or "scroll") == "scroll" \
        and not (opts.get("merge_only") or opts.get("upload_only"))
    if scrolling_render:
        # Remembered at request time, falling back to what this song chose last
        # before the app-wide default. Writing them only after a render succeeded
        # meant a margin nudged against a render that then failed was gone by the
        # next page load, and the panel offered the default again.
        settings = _scroll_settings(song, opts)
        opts.pop("bpm", None)  # only a tempo the score lacks is passed on
        opts.update(settings)
        _remember_record_settings(song, **settings)
    else:
        opts.pop("bpm", None)
    kind = "upload" if opts.get("upload_only") else "render"
    if not job_state.start_if_idle(
            song.dir, kind, ("scan", "clean", "render", "upload"), source_fingerprint):
        raise HTTPException(409, "Another scan, clean, render, or upload is already running for this song.")
    # The durable start above is the atomic gate; the PID lock keeps the existing
    # process-aware recording indicator and stale-lock recovery behavior.
    try:
        with open(_lock_path(song), "w") as f:
            f.write(str(os.getpid()))
    except Exception as exc:
        _job_finish(song, kind, str(exc))
        raise
    opts["_source_fingerprint"] = source_fingerprint
    if opts.get("upload_only"):
        opts["_existing_outputs"] = [
            song.media_path("video", os.path.basename(name))
            for name in song.data.get("record", {}).get("outputs", [])
        ]
    asyncio.get_running_loop().run_in_executor(None, _run_record, slug, opts)
    return {"started": True}


@app.get("/api/songs/{slug}/media/{name}")
def api_media(slug: str, name: str):
    song = _require(slug)
    safe = os.path.basename(name)
    path = song.media_path("video", safe)
    if not os.path.exists(path):
        raise HTTPException(404, "No such media")
    kind = "video/mp4" if safe.lower().endswith(".mp4") else "video/quicktime"
    return FileResponse(path, media_type=kind, headers=dict(REVALIDATE))


@app.post("/api/songs/{slug}/youtube-delete")
def api_youtube_delete(slug: str) -> Dict:
    """Delete this song's uploaded videos from YouTube so they can be re-uploaded."""
    song = _require(slug)
    uploads = song.data.get("record", {}).get("uploads", [])
    ids = [u.get("video_id") for u in uploads if u.get("video_id")]
    if not ids:
        raise HTTPException(400, "Nothing uploaded to delete")
    try:
        from src.stemmanauha.upload_to_youtube import delete_videos
        delete_videos(ids, log=lambda m: hub.emit(slug, {"type": "log", "line": m}))
    except Exception as exc:
        raise HTTPException(500, f"Delete failed: {exc}")
    song.data["record"]["uploads"] = []
    song.data["record"]["playlist_id"] = None
    song.save()
    # The site would otherwise list the deleted videos until its schedule runs.
    site_refresh.refresh_stemmanauhat(lambda m: hub.emit(slug, {"type": "log", "line": m}))
    return _derived(song)


@app.post("/api/songs/{slug}/reveal-media")
def api_reveal_media(slug: str) -> Dict:
    song = _require(slug)
    vdir = song.media_path("video")
    if not os.path.isdir(vdir):
        raise HTTPException(404, "No media yet")
    subprocess.Popen(["open", vdir])
    return {"ok": True}


# --------------------------------------------------------------------------
# WebSocket + file watcher
# --------------------------------------------------------------------------
@app.websocket("/ws/{slug}")
async def ws_endpoint(ws: WebSocket, slug: str) -> None:
    await hub.connect(slug, ws)
    try:
        while True:
            await ws.receive_text()  # keepalive; client doesn't send commands
    except WebSocketDisconnect:
        hub.disconnect(slug, ws)


async def _watch_cleaned() -> None:
    """Watch songs/ for saved edits to *_cleaned.mscx and re-run the health check."""
    from watchfiles import awatch
    async for changes in awatch(state.SONGS_DIR):
        touched: Set[str] = set()
        for _change, path in changes:
            if path.endswith("_cleaned.mscx"):
                slug = os.path.basename(os.path.dirname(path))
                touched.add(slug)
        for slug in touched:
            if _on_cleaned_saved(slug):
                hub.emit(slug, {"type": "state"})


def _on_cleaned_saved(slug: str) -> bool:
    """React to a saved cleaned score; True when the health record was re-taken."""
    song = state.load(slug)
    if not song:
        return False
    # Only react if the file actually changed since our last scan.
    fp = state.file_fingerprint(song.cleaned_path())
    if not fp or fp == song.data.get("cleaned_fingerprint"):
        return False
    _rescan(song)
    return True


@app.on_event("startup")
async def _startup() -> None:
    hub.loop = asyncio.get_running_loop()
    try:
        n = import_legacy()
        if n:
            print(f"Imported {n} existing song folder(s).")
    except Exception:
        traceback.print_exc()
    for song in state.list_songs():
        job_state.interrupt_running(song.dir)
    if os.path.isdir(state.SONGS_DIR):
        asyncio.create_task(_watch_cleaned())


# --------------------------------------------------------------------------
# Static frontend (mounted last so /api/* wins)
# --------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"), headers=dict(REVALIDATE))


@app.get("/pwa-assets.js")
def pwa_assets_js():
    """The service worker's shell list and cache generation, computed now.

    It used to be a checked-in file with a script to regenerate it, and that
    made every edit to `app.js` or `style.css` a two-part change: touch the
    asset, remember the script. Forgetting the second half broke the suite in a
    way that says nothing about what was actually changed. The generation is
    derived from the files on disk, so deriving it per request cannot be stale —
    and the service worker asks for it over the network anyway.
    """
    return Response(pwa_assets.rendered_config(), media_type="text/javascript",
                    headers=dict(REVALIDATE))


app.mount("/", RevalidatingStaticFiles(directory=STATIC_DIR, html=True), name="static")
