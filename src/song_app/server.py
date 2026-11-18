"""FastAPI backend for the `song` app — a thin, state-aware door over the toolkit."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import tempfile
import traceback
import urllib.parse
from contextlib import contextmanager
from typing import Dict, List, Optional, Set

import dotenv
from lxml import etree
from fastapi import FastAPI, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import (FileResponse, JSONResponse, PlainTextResponse,
                               Response)
from fastapi.staticfiles import StaticFiles

from . import (agentdeck, bar_readings, health, heavy_slot, homr_install, job_state, omr,
               pdf_systems, pipeline, problems, publish, pwa_assets, scan,
               state, system_finder, verification)
from . import clean_backup
from . import resing
from src.sing_song import places as song_places
from src.sing_song import languages
from src.sing_song import language_check
from src.sing_song import progress
from src.clean_score import lyric_txt
from src.clean_score.utils.score_fixes import FixError
from src.score_export.score import START as START_TEMPO
from src.score_export import staff_groups

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
# The app deploys over itself every couple of minutes. The SPA files carried no
# Cache-Control, which does NOT mean "do not
# cache": a browser given no instruction is free to invent a freshness lifetime
# from Last-Modified, and Chrome on Android invents a generous one. The phone
# then serves old code off its own disk without ever asking us,
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


@app.exception_handler(job_state.SongGone)
async def _song_gone(_request, _exc) -> JSONResponse:
    """A song deleted while a request was working on it (#428): nothing was written."""
    return JSONResponse({"detail": "This song was deleted."}, status_code=404)


def _require(slug: str) -> state.Song:
    song = state.load(slug)
    if not song:
        raise HTTPException(404, f"No song '{slug}'")
    return song


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


def is_scanning(song: state.Song) -> bool:
    """A scan is minutes long, so a page refresh must not start a second one."""
    return _holds_lock(_scan_lock_path(song))


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
        # The language it is sung in (#484): its own file, not .song.json; None when unsaved.
        "language": song.language,
        # The lyric warnings with the ids Dismiss sends back (#482).
        "lyrics": {**(song.data.get("lyrics") or {}),
                   "warnings": _with_ids((song.data.get("lyrics") or {}).get("warnings"))}
        if song.data.get("lyrics") else song.data.get("lyrics"),
        "stages": state.STAGES,
        "stage_index": state.STAGES.index(song.stage) if song.stage in state.STAGES else 0,
        "has_pdf": bool(pdf and os.path.exists(pdf)),
        "has_cleaned": bool(cleaned and os.path.exists(cleaned)),
        "needs_initial_bpm": needs_initial_bpm,
        # the score's printed rit./accel. marks, for the Publish panel (#483)
        "gradual_marks": pipeline.gradual_marks(
            cleaned, (song.data.get("publish") or {}).get("tempo_changes"),
            (song.data.get("publish") or {}).get("bpm"))
        if cleaned and os.path.exists(cleaned) else [],
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
        "scanning": is_scanning(song),
        # What the scan stage has read, what is still a hole, and what the app
        # threw away this read because its input had moved under it.
        "scan_status": scan.status(song),
        "scan_discarded": [scan.said(line) for line in discarded],
        # The composer, lyricist and arranger the score itself carries, which
        # the Publish panel offers until someone saves their own (#400).
        "score_credits": publish.score_credits(cleaned) if cleaned and os.path.exists(cleaned) else {},
        # The part names the site will show, which the Publish panel lets a
        # person correct: cleaning names parts by guessing from pitch (#402).
        "publish_parts": _publish_parts(song, cleaned),
        # The parts the site draws on one staff, as the page prints them (#420).
        "publish_staff_groups": _publish_staff_groups(song, cleaned),
        # Whether this host can publish to the Cloudflare site at all (#382).
        "publish_configured": publish.Config.from_env() is not None,
        # The choirs it is listed for, a pre-#384 single `site` record included.
        "published": song.published(),
        "jobs": job_state.load(song.dir),
        # The score the last clean replaced, which Undo puts back (#391).
        "clean_backup": clean_backup.latest(song),
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


# Every job that writes a song's cleaned score or reads it at length, and the one
# tuple each of them is started against (#482): whichever reaches `state.start_job`
# first runs and the other answers 409, decided in one lock-held check. `lyrics` is
# the lyric import; `fix` is a short route rewriting the score (an upload, a recorded
# slur, a picked reading or problem answer, an undo of the last clean).
WRITERS = ("scan", "clean", "check", "publish", "lyrics", "fix")


@contextmanager
def _fix_job(song: state.Song, refused: str):
    """Run a short score-rewriting route as a `fix` job, finished on every exit."""
    if is_scanning(song) or not state.start_job(song, "fix", WRITERS):
        raise HTTPException(409, refused)
    error = None
    try:
        yield
    except BaseException as exc:
        error = str(getattr(exc, "detail", "") or exc) or type(exc).__name__
        raise
    finally:
        _job_finish(song, "fix", error)


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

    if not (inp or cleaned or pdf):
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

    # A folder with an input score is past scanning whatever else it has, so the
    # 48 legacy songs land where they always did. Only a folder that has nothing
    # but a PDF is a song waiting to be read off the page.
    data["stage"] = ("review" if cleaned else "clean" if inp else "scan" if pdf else "register")
    state.Song(name, data).save()
    return True


def import_legacy() -> int:
    """Create state files for any legacy folders that don't have one. Idempotent."""
    if not os.path.isdir(state.SONGS_DIR):
        return 0
    count = 0
    for name in sorted(os.listdir(state.SONGS_DIR)):
        if name.startswith("."):
            continue  # .trash and the app's own files, never a song
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
    language: str = Form(""),
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
    if language and language not in language_check.LANGUAGES:
        raise HTTPException(400, f"language must be one of {', '.join(language_check.LANGUAGES)}")
    # Read every upload first, then make the song and write them in one step under
    # its lock (#428): no await may fall between claiming a slug and filling it.
    sources = {}
    if has_xml:
        sources["xml"] = (xml.filename, await xml.read())
    if has_pdf:
        sources["pdf"] = (pdf.filename, await pdf.read())
    song = state.create(name, per_system, voicing, sources=sources,
                        stage="clean" if has_xml else "scan", language=language)
    return {"slug": song.slug}


@app.get("/api/languages")
def api_languages() -> Dict:
    """The languages a song can be sung in (#484), code -> name, Finnish first."""
    return {"languages": [{"code": c, "name": n} for c, n in language_check.LANGUAGES.items()]}


@app.post("/api/songs/{slug}/language")
def api_set_language(slug: str, body: Dict) -> Dict:
    """Save the language the song is sung in (#484). Not held back by a running job: it is its
    own file, which no job writes. A singing run already going keeps the language it started
    with, and the reply says so; the song list then shows the song as needing a re-sing."""
    song = _require(slug)
    code = (body or {}).get("language")
    if code not in language_check.LANGUAGES:
        raise HTTPException(400, f"language must be one of {', '.join(language_check.LANGUAGES)}")
    with state.song_lock(slug):   # a reading saved meanwhile reads the language under it (#484)
        old = song.language
        language_check.save(song.dir, code, "app")
    reply = {"language": code, "previous": old, "note": None}
    run = progress.read(song.dir)
    if run and run.get("state") in progress.RUNNING and run.get("sing", True) \
            and run.get("language") and run.get("language") != code:
        reply["note"] = (f"a singing run (pid {run.get('pid')}) is singing this song in "
                         f"{language_check.describe(run['language'])}; it will be marked for re-singing")
    return reply


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
    if is_scanning(song) or not state.start_job(
            song, "scan", WRITERS,
            pdf_systems.file_version(song.source_path("pdf"))):
        raise HTTPException(409, "Another scan, clean, or publish is "
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
    try:
        mscx = pipeline.convert_to_mscx(xml, song.dir)
    except RuntimeError as exc:
        # MuseScore refused the input; say why rather than a bare 500 (#481).
        raise HTTPException(422, str(exc))
    # The bars whose voices run past the barline, read off the converted file on
    # every load, so the Clean panel shows them on its first view and after a
    # reload alike (#481). The clean's own log names them again as it runs.
    grid = pipeline.system_grid(mscx)
    # Which reading of each system this grid shows. The panel sends them back
    # with its answers, so answers typed against a system read again since the
    # grid was loaded are refused rather than stamped onto the new reading (#498).
    stamps = scan.system_stamps(song, range(len(grid)))
    return {"grid": grid, "overlong": pipeline.overlong_in(mscx),
            "stamps": {str(i): stamp for i, stamp in stamps.items()}}


@app.put("/api/songs/{slug}/systems")
def api_save_systems(slug: str, answers: Dict = None) -> Dict:
    """Persist grid answers.

    The body is `{"answers": {system_index: {staff_id: 'T1,T2'}}, "stamps":
    {system_index: reading}}`, the stamps being what `GET /systems` said the grid
    showed. A bare answers mapping is still taken, checked against the readings
    as this request found them.
    """
    song = _require(slug)
    body = answers or {}
    client_stamps = body.get("stamps") if isinstance(body.get("answers"), dict) else None
    if isinstance(body.get("answers"), dict):
        body = body["answers"]
    # A system's answers may hold `per_system.ALL_REST` beside the staff ids:
    # "No voices here" (#482).
    parsed = pipeline.per_system.parse_answers(body)
    # The readings these answers are about: the ones the grid showed when the
    # panel loaded it, or failing that the fragments as this request found them,
    # before the slow conversion below.
    if isinstance(client_stamps, dict):
        answered = {i: client_stamps.get(str(i)) for i in parsed}
    else:
        answered = scan.system_stamps(song, list(parsed))
    xml = song.source_path("xml")
    mscx = pipeline.convert_to_mscx(xml, song.dir)
    # An answer is about the staves of one scanned system, so it is derived from
    # that system's fragment and has to say which one, or a re-scan cannot know
    # it has invalidated it. Checked and written on the state as it is now, under
    # the lock: a system read again meanwhile has other staves, and stamping the
    # new reading onto answers about the old one would make them look current
    # (#498).
    with state.song_lock(slug):
        fresh = state.load(slug) or song
        changed = sorted(i for i, stamp in scan.system_stamps(fresh, list(parsed)).items()
                         if stamp != answered[i])
        if changed:
            raise HTTPException(
                409, "System " + ", ".join(str(i + 1) for i in changed)
                + " was read again while these answers were saved. "
                "Reload the grid and answer it again.")
        # A band read again since the score was put together: the grid still
        # shows the old reading, and answers about it would be stamped with a
        # reading they are not about. After a crash no scan is running to finish
        # it, so the sentence says to press Scan rather than to wait.
        unassembled = scan.unassembled_rows(fresh, list(parsed))
        if unassembled:
            raise HTTPException(
                409, "System " + ", ".join(str(i + 1) for i in unassembled)
                + " was read again since the score was put together. Press Scan "
                "on the Scan stage to finish it, then reload the grid.")
        # The stamp is saved before the answers: a crash between the two leaves
        # a stamp with no new answers, which is harmless. The other order could
        # leave answers no stamp ties to a reading, which a re-read never drops.
        # Both stay inside this one lock, so no re-read lands between them.
        scan.stamp_answers(fresh, list(parsed))
        fresh.save()
        try:
            pipeline.save_system_answers(mscx, parsed)
        except (OSError, pipeline.per_system.AnswerFileError) as exc:
            raise HTTPException(500, f"Could not save the answers: {exc}")
    song.adopt(fresh)
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
        # The score this clean replaces, with what was recorded about it, so a clean
        # pressed by mistake can be undone (#391). Taken back if the clean fails,
        # since then nothing was replaced.
        backup = clean_backup.stash(song)
        try:
            cleaned, source_mscx = pipeline.run_clean(
                xml, song.dir, per_system=(song.mode == "per-system"), log=log,
                voicing=song.data.get("voicing") or None, check=opens,
            )
        except Exception:
            clean_backup.drop(backup)
            raise
        if backup:
            log("The previous cleaned score is kept: Undo last clean (Clean panel) puts it back.")
        # A song scanned before the scan stage labelled its bands gets them here,
        # off the same converted input.
        _label_bounds(song, source_mscx)
        rel = os.path.relpath(cleaned, song.dir)
        song.data["cleaned"] = rel
        song.data["cleaned_fingerprint"] = state.file_fingerprint(cleaned)
        clean_backup.mark_made(backup, song.data["cleaned_fingerprint"])
        note_check = verification.compare_notes(source_mscx, cleaned)
        song.data.setdefault("verification", {})["notes"] = {
            **note_check, "checked_against": song.data["cleaned_fingerprint"],
        }
        song.data["verification"]["musescore"] = {
            **opens, "checked_against": song.data["cleaned_fingerprint"],
        }
        # Run the health check.
        found = _health_scan(song, cleaned)
        # The issues as they are now, not as this clean found them minutes ago,
        # so a row dismissed while it ran stays dismissed (#498).
        with state.song_lock(slug):
            now = state.load(slug) or song
            prev = now.data.get("health", {}).get("issues", [])
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
    if is_scanning(song) or not state.start_job(
            song, "clean", WRITERS,
            state.file_fingerprint(song.source_path("xml"))):
        raise HTTPException(409, "Another scan, clean, or publish is already running for this song.")
    asyncio.get_running_loop().run_in_executor(None, _run_clean, slug)
    return {"started": True}


@app.post("/api/songs/{slug}/undo-clean")
def api_undo_clean(slug: str) -> Dict:
    """Put back the cleaned score the last clean replaced, with its records (#391)."""
    song = _require(slug)
    with _fix_job(song, "Something is running for this song; undo once it has finished."), \
            state.song_lock(song.slug):
        fresh = state.load(song.slug) or song
        try:
            clean_backup.restore(fresh)
        except clean_backup.NothingToUndo as exc:
            raise HTTPException(400, str(exc))
        except clean_backup.ChangedSinceClean as exc:
            raise HTTPException(409, str(exc))
        # The restored fingerprint is in the state before the watcher sees the file
        # change, so it is read as our own write and not checked a second time.
        fresh.save()
    song.adopt(fresh)
    hub.emit(slug, {"type": "log", "line": "Undid the last clean: the previous cleaned score is back."})
    hub.emit(slug, {"type": "state"})
    return _derived(song)


def _run_check(slug: str) -> None:
    """Run the clean's checks again on the cleaned score as it is now (#391).

    A clean compares the notes against the scan and asks MuseScore whether it would
    open the result, and those answers hold only for the file they were made on. A
    score fixed in MuseScore, uploaded, or changed by a recorded fix showed both as
    stale, and the only way to refresh them was a re-clean, which throws the score
    away. This asks the same questions about the score that is there.

    It only reports. Unlike a clean it resets no bar MuseScore refuses: the score may
    be a person's own edit, and changing it here would do so where nobody looked.
    What MuseScore refuses becomes a health row instead (`_health_scan`).
    """
    song = _require(slug)
    log = lambda m: _job_emit(slug, "check", m)
    try:
        cleaned = _cleaned_or_400(song)
        fingerprint = state.file_fingerprint(cleaned)
        source_mscx = pipeline.convert_to_mscx(song.source_path("xml"), song.dir, log)
        log("Comparing the notes with the scan…")
        notes = verification.compare_notes(source_mscx, cleaned)
        log(notes.get("detail") or "Notes compared.")
        name = pipeline.checker()
        log(f"Asking {name} whether it opens the score…")
        found = pipeline.musescore_check(cleaned)
        if found is None:
            opens = {"status": "not_checked", "reset": [], "rejected": [], "by": name}
            log("Not checked: no MuseScore answered (MUSESCORE_CLI_PATH).")
        elif found:
            opens = {"status": "rejected", "reset": [], "rejected": found, "by": name}
            log(f"{name} calls this score corrupted ({len(found)} problem(s)); "
                "listed in the Fix panel:")
            for one in found:
                log("  " + one["message"])
        else:
            opens = {"status": "passed", "reset": [], "rejected": [], "by": name}
            log(f"{name} opens it without calling it corrupted.")
        with state.song_lock(song.slug):
            fresh = state.load(song.slug) or song
            if state.file_fingerprint(cleaned) != fingerprint:
                raise RuntimeError("The score changed while it was being checked; check again.")
            fresh.data["cleaned_fingerprint"] = fingerprint
            checks = fresh.data.setdefault("verification", {})
            checks["notes"] = {**notes, "checked_against": fingerprint}
            checks["musescore"] = {**opens, "checked_against": fingerprint}
            found_health = _health_scan(fresh, cleaned)
            fresh.data["health"] = {
                "checked_against": fingerprint,
                "issues": health.merge_issues(
                    found_health, fresh.data.get("health", {}).get("issues", [])),
            }
            fresh.save()
        n = health.finding_count([i for i in fresh.data["health"]["issues"]
                                  if i.get("status") == "open"])
        log(f"Done. {n} issue(s) to review." if n else "Done. No issues found.")
        _job_finish(song, "check")
    except Exception as exc:
        traceback.print_exc()
        message = exc.detail if isinstance(exc, HTTPException) else _error_text(exc)
        _job_emit(slug, "check", message, "error")
        _job_finish(song, "check", message)
    hub.emit(slug, {"type": "state"})


@app.post("/api/songs/{slug}/recheck")
async def api_recheck(slug: str) -> Dict:
    song = _require(slug)
    _cleaned_or_400(song)
    # Not beside a publish either: each saves the whole song state, and the later
    # save would put back the earlier one's copy of the other's result.
    if is_scanning(song) or not state.start_job(
            song, "check", WRITERS):
        raise HTTPException(409, "A scan, clean, check, or publish is already running "
                                 "for this song.")
    asyncio.get_running_loop().run_in_executor(None, _run_check, slug)
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
    song.adopt(fresh)


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
    # A short `fix` job, so a clean, an upload or a lyric import cannot rewrite the
    # score at the same time (#482); the log line goes straight to the page.
    with _fix_job(song, "A scan, clean, lyric import or another fix is running for "
                        "this song — wait for it to finish, then record the slur."):
        try:
            done = pipeline.record_slur_fix(
                song.dir, cleaned, staff, measure, index, span, body.get("why", ""))
        except FixError as exc:
            raise HTTPException(400, str(exc))
        except (OSError, RuntimeError) as exc:
            raise HTTPException(500, str(exc))
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
    # A clean, a publish or a lyric import reads the score this rewrites.
    with _fix_job(song, "A scan, clean, publish or lyric import is running for this "
                        "song — wait for it to finish, then pick again."):
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
    with _fix_job(song, "A scan, clean, publish or lyric import is running for this "
                        "song — wait for it to finish, then pick again."):
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
    # Read and written in one step, so a clean or check finishing in between
    # does not get its health record replaced by the one read here (#498).
    with state.song_lock(slug):
        fresh = state.load(slug) or song
        for i in fresh.data.get("health", {}).get("issues", []):
            if i["id"] == issue_id:
                i["status"] = "dismissed"
        fresh.save()
    song.adopt(fresh)
    return _derived(song)


@app.post("/api/songs/{slug}/rename")
async def api_rename(slug: str, body: Dict) -> Dict:
    """Change the song's display name. The site shows the new one at the next publish."""
    song = _require(slug)
    new_name = (body or {}).get("name", "").strip()
    if not new_name:
        raise HTTPException(400, "Name is required")
    song.data["name"] = new_name
    song.save()
    return _derived(song)


@app.delete("/api/songs/{slug}")
def api_delete_song(slug: str) -> Dict:
    """Delete a song from the app by moving its folder to `songs/.trash/` (#428).

    Refused while a scan, clean, check or publish runs on it: the job would go on
    writing into a folder that is no longer there. The site keeps its own copy, so a
    published song stays on it; the reply names the choirs it is listed in.
    """
    song = _require(slug)
    with state.song_lock(slug), job_state._lock(song.dir):
        song = _require(slug)
        jobs = job_state.load(song.dir)
        running = [k for k, j in jobs.items()
                   if isinstance(j, dict) and j.get("status") == "running"]
        if is_scanning(song) or running:
            raise HTTPException(409, "A scan, clean, check or publish is running for this "
                                     "song — delete it once that has finished.")
        published = song.published()
        dest = state.trash(slug)
    return {"slug": slug, "name": song.name, "published": published,
            "trash": os.path.join("songs", state.TRASH_DIR_NAME, os.path.basename(dest))}


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
    """Record which cleaned score a person approved, then advance to Publish."""
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
    song.set_stage("publish")
    song.save()
    return _derived(song)


# --------------------------------------------------------------------------
# Lyrics stage
# --------------------------------------------------------------------------
@app.get("/api/prompt")
def api_prompt(slug: str = "") -> Dict:
    """The AI lyric prompt; given a song, it lists that song's part names (#482)."""
    path = os.path.join(SCRIPT_DIR, "lyric_json_prompt.txt")
    with open(path, "r", encoding="utf-8") as f:
        prompt = f.read()
    if not slug:
        return {"prompt": prompt}
    return {"prompt": pipeline.lyric_prompt(prompt, _require(slug).cleaned_path() or "")}


@app.get("/api/songs/{slug}/lyric-grid")
def api_lyric_grid(slug: str) -> Dict:
    """Structure for the manual lyric editor: the parts, the printed systems, the text."""
    song = _require(slug)
    cleaned = song.cleaned_path()
    if not cleaned or not os.path.exists(cleaned):
        raise HTTPException(400, "Clean the score first")
    return pipeline.lyric_grid(cleaned, song.dir)


@app.get("/api/songs/{slug}/lyric-staves/{index}")
def api_lyric_staves(slug: str, index: int) -> Dict:
    """Where each staff of one printed system is in its picture, and which it is (#397).

    `index` is the lyric editor's system (0-based). `printed` is None when the staves
    found do not match the score's record, and then the editor lists the boxes instead.
    """
    song = _require(slug)
    cleaned = song.cleaned_path()
    if not cleaned or not os.path.exists(cleaned):
        raise HTTPException(400, "Clean the score first")
    pdf = song.source_path("pdf")
    try:
        return pipeline.lyric_staves(song.dir, pdf if pdf and os.path.exists(pdf) else "",
                                     cleaned, index)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:  # no poppler, an unreadable crop: the list still works
        return {"crop": None, "rows": [], "printed": None, "error": str(exc)}


@app.get("/api/songs/{slug}/landed/{index}")
def api_landed(slug: str, index: int):
    """One printed system's bars of the cleaned score as they are now, words included.

    `index` is the lyric editor's system (0-based). Drawn from those bars alone, so it
    is the right bars even where a render of the whole score lays them out otherwise.
    """
    song = _require(slug)
    cleaned = song.cleaned_path()
    if not cleaned or not os.path.exists(cleaned):
        raise HTTPException(400, "Clean the score first")
    systems = pipeline.lyric_grid(cleaned, song.dir)["systems"]
    if not 0 <= index < len(systems):
        raise HTTPException(404, f"there is no system {index + 1}")
    system = systems[index]
    try:
        path = pipeline.landed_system_svg(song.dir, cleaned, system["start"], system["end"])
    except Exception as exc:
        raise HTTPException(500, f"Could not draw bars {system['start']}–{system['end']}: {exc}")
    return FileResponse(path, media_type="image/svg+xml", headers=dict(REVALIDATE))


def _kept_warnings(previous: List, result, box_import: bool = False) -> List[Dict]:
    """Earlier import warnings this import did not answer (#397, #482).

    An import now changes only what it names, so a warning about a box it did not
    touch still stands. One is answered when the import rewrote its bars on one of
    its parts, for the same voice and verse. A warning with no bars to it, or one
    an older app saved as a sentence, belonged to the import that made it.

    A warning about a source line (`part_rests`, `unknown_part`, `two_lines`) is
    answered only when the match is certain (#482): this import sent a line with
    the same identity (the part as written, in the same bar, verse and voice), or,
    for a `part_rests` or `two_lines` warning, a Type by system box import wrote
    its staff for the same verse and voice over its bars (an emptied box counts).
    Anything else stays until a person dismisses it.
    """
    kept = []
    for w in previous or []:
        if not isinstance(w, dict) or not w.get("measure_start"):
            continue
        start, end = w["measure_start"], w.get("measure_end") or w["measure_start"]
        verse, voice = w.get("verse") or 1, w.get("voice") or 1

        def wrote(sid):
            return any(x.staff == sid and x.verse == verse and x.voice == voice
                       and x.start <= end and x.end >= start for x in result.written)

        if w.get("kind") in lyric_txt.LINE_KINDS:
            if set(w.get("lines") or ()) & set(getattr(result, "lines", ()) or ()):
                continue
            if (box_import and w.get("kind") != lyric_txt.UNKNOWN_PART
                    and any(wrote(sid) for sid in w.get("staff_ids") or [])):
                continue
            kept.append(w)
            continue
        # One warning can name several staves (the same range, kind and counts are
        # said once); only the staves this import rewrote are answered.
        left = [sid for sid in (w.get("staff_ids") or []) if not wrote(sid)]
        if left:
            kept.append({**w, "staff_ids": left})
    return kept


def _with_ids(warnings) -> List:
    """The stored lyric warnings, each with the id Dismiss sends back (#482).

    Worked out on reading (`lyric_txt.warning_id`), so a warning saved before ids
    existed has one too and no `.song.json` needs rewriting. One saved as a plain
    sentence goes out as `{"message", "id"}`, so it can be dismissed like the rest.
    """
    return [{**w, "id": lyric_txt.warning_id(w)} if isinstance(w, dict)
            else {"message": w, "id": lyric_txt.warning_id(w)}
            for w in warnings or [] if isinstance(w, (dict, str))]


def _settle_lyrics_stage(song: state.Song, cleaned: Optional[str]) -> bool:
    """Move a song on Lyrics to Review once nothing is left to answer (#482).

    Only when the warnings were made against the score as it is now: an import
    against an older score proves nothing about this one. Returns whether it moved.
    """
    lyrics = song.data.get("lyrics") or {}
    if (song.stage == "lyrics" and cleaned and os.path.exists(cleaned)
            and lyrics.get("imported_against")
            and lyrics["imported_against"] == state.file_fingerprint(cleaned)
            and not lyrics.get("warnings")):
        song.set_stage("review")
        return True
    return False


# The lyric import places the words here first and moves the file over the score
# only once it knows the score did not change meanwhile (#482). The name does not
# end in `_cleaned.mscx`, so the file watcher never reads it as a score.
LYRICS_TMP = ".lyrics-tmp"
SCORE_MOVED = ("the score changed while the words were being imported "
               "(saved in MuseScore?); import again")


@app.get("/api/songs/{slug}/lyrics-json")
def api_lyrics_json(slug: str):
    song = _require(slug)
    # What was pasted, not the by-system editor's last import (lyrics-editor.json).
    path = song.path("lyrics.json")
    if not os.path.exists(path):
        return PlainTextResponse("")
    with open(path, "r", encoding="utf-8") as f:
        return PlainTextResponse(f.read())


@app.post("/api/songs/{slug}/lyrics")
def api_lyrics(slug: str, body: Dict) -> Dict:
    """Import lyrics: pasted JSON (`json`), or the by-system editor's changed boxes.

    The editor sends `{"verse": n, "boxes": {system index: {box key: text}}}` — only
    the boxes changed, an empty one clearing its words (#397). `cells` is the older
    editor's verse-1 form. Either way only what is named changes; every other verse,
    voice, part and system keeps its words, and so do the warnings about them.

    It runs as a `lyrics` job (#482), so a clean, an upload or a recorded fix cannot
    rewrite the score under it, nor it under them. Nothing that reads the score runs
    before the job starts. The score's fingerprint is taken first; the words are
    placed into a temp file and moved over the score under the song lock only if the
    score on disk still has that fingerprint (a save in MuseScore takes no job).
    """
    song = _require(slug)
    cleaned = song.cleaned_path()
    if not cleaned or not os.path.exists(cleaned):
        raise HTTPException(400, "Clean the score first")
    body = body or {}
    cells = body.get("cells")
    boxes = body.get("boxes")
    verse = 1
    json_text = ""
    if boxes:
        try:
            verse = int(body.get("verse") or 1)
        except (TypeError, ValueError):
            raise HTTPException(400, "Which verse?")
        if verse < 1:
            raise HTTPException(400, "Which verse?")
    elif not cells:
        json_text = body.get("json", "")
        if not isinstance(json_text, str) or not json_text.strip():
            raise HTTPException(400, "Paste the lyric JSON first")
    if is_scanning(song) or not state.start_job(song, "lyrics", WRITERS):
        raise HTTPException(409, "Another job is changing this score; import again once it is done")
    tmp = cleaned + LYRICS_TMP
    error: Optional[str] = "Import stopped"
    finished = False
    try:
        # First, before anything reads the score: the compare below is against this.
        before = state.file_fingerprint(cleaned)
        if boxes:
            blocks = pipeline.lyric_box_blocks(cleaned, verse, boxes, song.dir)
            if not blocks:
                raise HTTPException(400, "Nothing changed")
        elif cells:
            blocks = pipeline.lyric_blocks(cleaned, cells, song.dir)
            if not blocks:
                raise HTTPException(400, "Nothing typed yet")
        if boxes or cells:
            json_text = json.dumps(blocks, ensure_ascii=False, indent=2)
            json_name = "lyrics-editor.json"
        else:
            json_name = "lyrics.json"
        # The editor's import is kept beside the paste rather than over it, so the
        # paste box still opens on what was pasted.
        json_path = song.path(json_name)
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(json_text)
        try:
            result = pipeline.run_lyric_import(json_path, cleaned, song_dir=song.dir, out_path=tmp)
        except Exception as exc:
            raise HTTPException(400, f"Import failed: {exc}")
        with state.song_lock(song.slug):
            song.check_still_here()
            fresh = state.load(song.slug) or song
            if state.file_fingerprint(cleaned) != before:
                raise HTTPException(409, SCORE_MOVED)
            os.replace(tmp, cleaned)
            after = state.file_fingerprint(cleaned)
            # Claimed in the same hold as the write (#336): the file watcher sees the
            # score change at once, and must read it as the app's own write.
            fresh.data["cleaned_fingerprint"] = after
            previous = fresh.data.get("lyrics") or {}
            # Earlier warnings are about the score they were made against; after a
            # re-clean or an edit their staves and bars may mean something else.
            kept = (_kept_warnings(previous.get("warnings"), result, box_import=bool(boxes))
                    if previous.get("imported_against") == before else [])
            fresh.data["lyrics"] = {
                "json": "lyrics.json" if json_name == "lyrics.json"
                        else previous.get("json") or json_name,
                "imported_against": after,
                "warnings": kept + [m.to_dict() for m in result.mismatches],
            }
            if not fresh.data["lyrics"]["warnings"]:
                fresh.set_stage("review")
            elif fresh.stage == "review":
                fresh.set_stage("lyrics")
            # Pitches and note lengths do not change during lyric placement, so the
            # note comparison and MuseScore's check still hold for the new file.
            for name in ("notes", "musescore"):
                data = fresh.data.get("verification", {}).get(name, {})
                if data.get("checked_against") == before:
                    data["checked_against"] = after
            fresh.save()
        song.adopt(fresh)
        # Import may add full-measure rests to otherwise empty measures, so health
        # must be checked again rather than rebound to the new fingerprint.
        found = _health_scan(song, cleaned)
        with state.song_lock(song.slug):
            song.check_still_here()
            late = state.load(song.slug) or song
            # Saved since the hold (MuseScore, an upload): the watcher checks that
            # file, and these findings are about one that is gone.
            if state.file_fingerprint(cleaned) == after:
                late.data["health"] = {
                    "checked_against": after,
                    "issues": health.merge_issues(
                        found, late.data.get("health", {}).get("issues", [])),
                }
            # A dismiss while this ran could not move the stage itself.
            _settle_lyrics_stage(late, cleaned)
            late.save()
            _job_finish(song, "lyrics")
            finished = True
        song.adopt(late)
        error = None
    except BaseException as exc:
        error = str(getattr(exc, "detail", "") or exc) or type(exc).__name__
        raise
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
        if not finished:
            try:
                with state.song_lock(song.slug):
                    _job_finish(song, "lyrics", error)
                    if os.path.isdir(song.dir):
                        left = state.load(song.slug)
                        if left is not None and _settle_lyrics_stage(left, cleaned):
                            left.save()
            except Exception:
                traceback.print_exc()
    # The bars that did not take their words, said in the reply itself rather than
    # left for the caller to dig out of the song's `lyrics.warnings` (#340).
    # `imported` is what this import alone said, so a caller can tell its own, and
    # `written` the bars it rewrote, which can run on past the system it was sent for.
    return {**_derived(song), "mismatches": _with_ids(song.data["lyrics"]["warnings"]),
            "imported": [m.to_dict() for m in result.mismatches],
            "written": [w.to_dict() for w in result.written]}


@app.post("/api/songs/{slug}/lyrics/warnings/dismiss")
def api_dismiss_lyric_warning(slug: str, body: Dict) -> Dict:
    """Take one lyric warning off the list: the person has answered it (#482).

    Any kind, by the id `GET /songs/{slug}` gives it. Writes only `lyrics.warnings`
    and the stage, never the score, so it is no job. The last one moves a song on
    Lyrics to Review only when the warnings were made against the score as it is
    now, and never while an import runs: that import decides the stage itself.
    """
    wid = (body or {}).get("id")
    if not isinstance(wid, str) or not wid:
        raise HTTPException(400, "Which warning?")
    song = _require(slug)
    with state.song_lock(song.slug):
        song.check_still_here()
        fresh = state.load(song.slug) or song
        lyrics = dict(fresh.data.get("lyrics") or {})
        warnings = lyrics.get("warnings") or []
        left = [w for w in warnings
                if not (isinstance(w, (dict, str)) and lyric_txt.warning_id(w) == wid)]
        if len(left) == len(warnings):
            raise HTTPException(404, "That warning is no longer on the list")
        lyrics["warnings"] = left
        fresh.data["lyrics"] = lyrics
        if not job_state.is_running(fresh.dir, "lyrics"):
            _settle_lyrics_stage(fresh, fresh.cleaned_path())
        fresh.save()
    song.adopt(fresh)
    return _derived(song)


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
        "skipped_pages": pipeline.skipped_pages(song.dir),
        "declared": declared,       # how many systems the score says there are
    }


@app.put("/api/songs/{slug}/bounds")
def api_save_bounds(slug: str, body: Dict = None) -> Dict:
    """Persist edited boundaries: {"systems": [{page, top, bottom}, ...]}.

    An optional `"skipped_pages": [n, ...]` says which pages hold no music to scan
    (#480); left out of the body, the pages already left out stay so.
    """
    song = _require(slug)
    pdf = _song_pdf(song)
    bands = (body or {}).get("systems")
    if bands is None:
        raise HTTPException(400, "No systems given")
    skipped = (body or {}).get("skipped_pages")
    try:
        saved = pipeline.save_system_bounds(song.dir, bands, _bounds_score(song), skipped,
                                            pages=_pages_of(pdf) if skipped is not None else None)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, f"Bad bounds: {exc}")
    # Moving a band is changing the input a fragment was read from. Both the
    # bands and the grid answers are keyed by position, so an inserted band
    # silently re-points everything after it — which is why nothing here compares
    # indices; each fragment is checked against the geometry now at its own.
    song = _require(slug)
    discarded = scan.reconcile(song)
    # The Scan panel's button waits for every page to be marked or left out.
    hub.emit(slug, {"type": "state"})
    return {"systems": saved, "skipped_pages": pipeline.skipped_pages(song.dir),
            "discarded": discarded}


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
        return {"systems": _outside_skipped(song, found)}
    try:
        reader = homr_install.begin_read()
    except homr_install.Refused as exc:
        raise HTTPException(409, str(exc)) from None
    with reader:
        answer = await _find_with_homr(slug, pdf, body)
    return {**answer, "systems": _outside_skipped(song, answer.get("systems", []))}


def _pages_of(pdf: str) -> Optional[int]:
    """The PDF's page count, or None when poppler cannot say."""
    try:
        return pipeline.page_count(pdf)
    except (OSError, RuntimeError, ValueError):
        return None


def _outside_skipped(song, systems: List[Dict]) -> List[Dict]:
    """A proposal holds no band on a page left out of the scan (#480)."""
    skipped = set(pipeline.skipped_pages(song.dir))
    return [b for b in systems if int(b.get("page", 0)) not in skipped]


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


@app.get("/api/songs/{slug}/system/{index}/where")
def api_system_where(slug: str, index: int, staff: int, staves: int,
                     bar: Optional[int] = None, bars: Optional[int] = None, dpi: int = 200):
    """Where one staff, and one bar of it, is in a printed system's crop (#368).

    `{"box": {top, bottom, left, right, bar}}` in fractions of the crop at `dpi`, or
    `{"box": null}` when the crop does not show the staves the score says it prints.
    """
    from PIL import Image

    song = _require(slug)
    pdf = _song_pdf(song)
    try:
        path = pipeline.system_crop(song.dir, pdf, index, max(50, min(dpi, 600)))
        with Image.open(path) as image:
            box = system_finder.bar_box(image, staff, staves, bar, bars)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))
    return {"box": box}


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
        rendered = pipeline.render_score_pdf(mscx, breaks, tidy=tidy,
                                             force=doc == "original")
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
    # Which application is a setting (MUSESCORE_APP), not a name written here: this
    # asked macOS for "MuseScore 3" on a machine that has only MuseScore 4, and the
    # button did nothing at all.
    from src import musescore_cli
    musescore_cli.open_score(cleaned)
    return {"ok": True}


@app.get("/api/songs/{slug}/score-file")
def api_score_file(slug: str):
    """The cleaned score, as a download.

    `open-score` above only works for somebody sitting at this host. This is the
    other half of the same idea for everybody else: take the file to whatever
    machine has MuseScore on it. Singing parts get voice instruments in the copy
    sent (#481), so MuseScore 4 puts new dynamics above the staff; the file here is
    not touched.
    """
    song = _require(slug)
    cleaned = _cleaned_or_400(song)
    name = os.path.basename(cleaned)
    copy = pipeline.score_for_download(cleaned)
    if copy is None:
        return FileResponse(cleaned, media_type="application/octet-stream",
                            filename=name, headers=dict(REVALIDATE))
    # Said the way FileResponse says it, so a name with ä in it survives.
    quoted = urllib.parse.quote(name)
    disposition = (f"attachment; filename*=utf-8''{quoted}" if quoted != name
                   else f'attachment; filename="{name}"')
    return Response(copy, media_type="application/octet-stream",
                    headers={**REVALIDATE, "Content-Disposition": disposition})


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
    data = await file.read()
    with _fix_job(song, "A scan, clean, check, publish, lyric import or fix is running "
                        "for this song — replacing the score underneath it would be "
                        "read half-written."):
        try:
            summary = pipeline.accept_uploaded_score(cleaned, file.filename, data)
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


# --------------------------------------------------------------------------
# Publish to the stemmanauhat site (#382, #400)
# --------------------------------------------------------------------------
def _whole_number(value) -> int:
    """`value` as an int when it is a whole number, or ValueError. `int()` alone
    would turn bar 13.5 into 13 and play the change in the wrong place."""
    if isinstance(value, bool):
        raise ValueError(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("+-").isdigit():
        return int(value)
    raise ValueError(value)


def _tempo_changes(cleaned: str, value) -> List[Dict]:
    """Tempo changes (#387) checked against this score, in the one way they are
    stored: `[{"measure": 13, "bpm": 112}, {"measure": 23, "bpm": "start"}]`,
    sorted by bar. Accepts that list or the same as JSON text. A bad bar or BPM
    is a 400 naming it."""
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else []
        except ValueError:
            raise HTTPException(400, "Tempo changes are not readable") from None
    if not isinstance(value, list):
        raise HTTPException(400, "Tempo changes must be a list")
    bars = pipeline.bar_count(cleaned)
    out: Dict[int, Dict] = {}
    for item in value:
        try:
            measure = _whole_number(item.get("measure"))
        except (AttributeError, TypeError, ValueError):
            raise HTTPException(400, "Each tempo change needs a whole bar number") from None
        if not 2 <= measure <= bars:
            raise HTTPException(400, f"Tempo change at bar {measure}: pick a bar from 2 "
                                     f"to {bars} (bar 1 plays at the opening tempo)")
        bpm = item.get("bpm")
        if bpm != START_TEMPO:
            try:
                bpm = _whole_number(bpm)
            except (TypeError, ValueError):
                raise HTTPException(400, f"Tempo change at bar {measure}: BPM must be "
                                         "a whole number or back to start") from None
            if not 20 <= bpm <= 300:
                raise HTTPException(400, f"Tempo change at bar {measure}: BPM must be "
                                         "between 20 and 300")
        if measure in out:
            raise HTTPException(400, f"Bar {measure} has two tempo changes")
        out[measure] = {"measure": measure, "bpm": bpm}
    return [out[m] for m in sorted(out)]


CREDIT_LIMIT = 200


def _credits(value) -> Dict[str, str]:
    """Composer, lyricist and arranger as the Publish panel sends them: each one
    text, trimmed; an empty one is kept empty, so clearing a name the score
    carries stays cleared."""
    if not isinstance(value, dict):
        raise HTTPException(400, "Credits must be composer, lyricist and arranger")
    out = {}
    for name in publish.CREDITS:
        text = value.get(name, "")
        if text is None:
            text = ""
        if not isinstance(text, str):
            raise HTTPException(400, f"The {name} must be text")
        text = " ".join(text.split())
        if len(text) > CREDIT_LIMIT:
            raise HTTPException(400, f"The {name} is longer than {CREDIT_LIMIT} characters")
        out[name] = text
    return out


PART_NAME_LIMIT = 40


def _part_names(value) -> List[str]:
    """Part names as the Publish panel sends them (#402): one per part, top staff
    first, each trimmed. None may be empty or repeat another, because the site's
    player tells the parts apart by name."""
    if not isinstance(value, list):
        raise HTTPException(400, "Part names must be a list")
    out: List[str] = []
    for index, name in enumerate(value, start=1):
        if not isinstance(name, str):
            raise HTTPException(400, f"Part {index}: the name must be text")
        name = " ".join(name.split())
        if not name:
            raise HTTPException(400, f"Part {index} has no name")
        if len(name) > PART_NAME_LIMIT:
            raise HTTPException(400, f"Part {index}: the name is longer than "
                                     f"{PART_NAME_LIMIT} characters")
        if name in out:
            raise HTTPException(400, f"Two parts are called {name}; the singers "
                                     "pick their part by name")
        out.append(name)
    return out


def _publish_parts(song: state.Song, cleaned: Optional[str]) -> Dict:
    """The part names a publish would use, for the Publish panel (#402): the saved
    ones, or the score's own while none are saved. `stale` when the saved ones no
    longer match the score's number of parts, which a publish refuses."""
    current = publish.score_parts(cleaned) if cleaned and os.path.exists(cleaned) else []
    saved = (song.data.get("publish") or {}).get("part_names")
    if saved is None:
        return {"names": current, "saved": False, "stale": False}
    if len(saved) != len(current):
        return {"names": current, "saved": True, "stale": True,
                "message": publish.part_count_message(len(current))
                + " These are the score's own names; correct them and press Save settings."}
    return {"names": list(saved), "saved": True, "stale": False}


def _publish_staff_groups(song: state.Song, cleaned: Optional[str]) -> Dict:
    """The shared staves a publish would draw, for the Publish panel (#420): the
    saved pairing, or the one worked out from the page while none is saved. `shown`
    once there is something to say: more than five parts, or a saved pairing."""
    if not cleaned or not os.path.exists(cleaned):
        return {"shown": False}
    settings = song.data.get("publish") or {}
    proposal = publish.staff_group_proposal(cleaned, settings.get("part_names"))
    saved = settings.get("staff_groups")
    out = {"proposal": proposal["groups"], "source": proposal["source"],
           "skipped": proposal["skipped"], "saved": saved is not None,
           "groups": proposal["groups"] if saved is None else saved,
           "shown": proposal["parts"] > staff_groups.MAX_STAVES or saved is not None,
           "max_staves": staff_groups.MAX_STAVES}
    if saved:
        try:
            publish.check_staff_groups(cleaned, saved, settings.get("part_names"))
        except publish.PublishError as exc:
            out["message"] = str(exc) + " Change them or use the automatic pairing."
    return out


def _staff_groups(value, cleaned: str, part_names: Optional[List[str]]):
    """Shared staves as the Publish panel sends them: text like "S1+S1b, S2+S2b",
    or a list of pairs; null for the automatic pairing and "" for none (#420)."""
    if value is None:
        return None
    try:
        groups = staff_groups.parse_groups(value) if isinstance(value, str) \
            else [tuple(g) for g in value]
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"Shared staves: {exc}") from None
    try:
        publish.check_staff_groups(cleaned, groups, part_names)
    except publish.PublishError as exc:
        raise HTTPException(400, str(exc)) from None
    return [list(g) for g in groups]


def _places(value) -> List[Dict]:
    """Where new part names go in the site's voice tree, as the Publish panel sends
    them (#430): ``[{"name", "id"}]`` (a name of a place, or "-" for none) or
    ``[{"name", "parent"}]`` (a new place under one)."""
    if not isinstance(value, list):
        raise HTTPException(400, "Places must be a list")
    out = []
    for place in value:
        if not isinstance(place, dict) or not str(place.get("name") or "").strip():
            raise HTTPException(400, "Each place needs the part name it is for")
        vid, parent = place.get("id"), place.get("parent")
        if (vid is None) == (parent is None) or not str(vid or parent).strip():
            raise HTTPException(400, f"Part {place['name']}: say a place, a place to put a "
                                     "new one under, or no place")
        out.append({"name": str(place["name"]).strip(),
                    **({"id": str(vid).strip()} if vid is not None
                       else {"parent": str(parent).strip()})})
    return out


def _gradual(cleaned: str, value) -> List[Dict]:
    """The Publish panel's say on the score's printed rit./accel. marks (#483):
    `[{"measure": 30, "bpm": 50, "off": false}]`. `bpm` null eases to the default
    (three quarters for a rit., five quarters for an accel.). A bar with no mark
    in the score is a 400, so a setting cannot quietly point at nothing."""
    if not isinstance(value, list):
        raise HTTPException(400, "Gradual changes must be a list")
    marks = {m["measure"] for m in pipeline.gradual_marks(cleaned)}
    out: Dict[int, Dict] = {}
    for item in value:
        try:
            measure = _whole_number(item.get("measure"))
        except (AttributeError, TypeError, ValueError):
            raise HTTPException(400, "Each rit./accel. setting needs a whole bar number") from None
        if measure not in marks:
            raise HTTPException(400, f"Bar {measure} has no rit. or accel. in the score")
        bpm = item.get("bpm")
        if bpm is not None and bpm != "":
            try:
                bpm = _whole_number(bpm)
            except (TypeError, ValueError):
                raise HTTPException(400, f"rit./accel. at bar {measure}: BPM must be "
                                         "a whole number") from None
            if not 20 <= bpm <= 300:
                raise HTTPException(400, f"rit./accel. at bar {measure}: BPM must be "
                                         "between 20 and 300")
        else:
            bpm = None
        out[measure] = {"measure": measure, "bpm": bpm, "off": bool(item.get("off"))}
    return [out[m] for m in sorted(out)]


def _publish_settings(song: state.Song, opts: Dict) -> Dict:
    """What the site plays and shows a song with, out of a request (#400).

    The tempo the app supplies (only for a score without its own), the tempo
    changes (#387), the credits and the part names (#402). Anything the request
    leaves out falls back
    to what this song saved last; the credits fall back to the score's own
    metaTags.
    """
    saved = song.data.get("publish") or {}
    cleaned = song.cleaned_path()
    if not cleaned or not os.path.exists(cleaned):
        raise HTTPException(400, "Clean the score first")
    out: Dict = {"tempo_changes": _tempo_changes(
        cleaned, opts.get("tempo_changes", saved.get("tempo_changes", [])))}
    if "gradual" in opts:
        out["gradual"] = _gradual(cleaned, opts["gradual"])
    elif "gradual" in saved:
        out["gradual"] = saved["gradual"]
    if not pipeline.has_opening_tempo(cleaned):
        try:
            bpm = _whole_number(opts.get("bpm", saved.get("bpm", publish.DEFAULT_BPM)))
        except (TypeError, ValueError):
            raise HTTPException(400, "BPM must be a whole number") from None
        if not 20 <= bpm <= 300:
            raise HTTPException(400, "BPM must be between 20 and 300")
        out["bpm"] = bpm
    if "credits" in opts:
        out["credits"] = _credits(opts["credits"])
    elif "credits" in saved:
        out["credits"] = saved["credits"]
    names = _part_names(opts["part_names"]) if "part_names" in opts \
        else saved.get("part_names")
    if names is not None:
        count = len(publish.score_parts(cleaned))
        if len(names) != count:
            raise HTTPException(400, publish.part_count_message(count))
        out["part_names"] = names
    if "places" in opts:
        out["places"] = _places(opts["places"])
    elif saved.get("places"):
        out["places"] = saved["places"]
    if "staff_groups" in opts:
        out["staff_groups"] = _staff_groups(opts["staff_groups"], cleaned, names)
    elif saved.get("staff_groups") is not None:
        out["staff_groups"] = saved["staff_groups"]
    return out


def _publish_busy(song: state.Song) -> bool:
    return is_scanning(song) or any(job_state.is_running(song.dir, kind)
                                    for kind in WRITERS if kind != "scan")


def _readings(words: List[str]) -> Optional[Dict]:
    """{word: readings} of the words the dictionary reads more than one way, from
    src/sing_song/readings.py in the singer's venv (g2p_en and its dictionary live there), or
    None on a host with no singer venv: the readings typed by hand need none (#484 review)."""
    from src.sing_song.main import sing_venv
    python = os.path.join(sing_venv(), "bin", "python")
    if not os.path.exists(python):
        return None
    script = os.path.join(os.path.dirname(song_places.__file__), "readings.py")
    r = subprocess.run([python, "-I", script], input=json.dumps(words), capture_output=True,
                       text=True, timeout=300)
    if r.returncode:
        raise HTTPException(500, f"readings.py failed: {(r.stderr or r.stdout).strip()[-500:]}")
    return json.loads(r.stdout)


NO_PACK = "not checked against a voice pack: none found at UFR_DIR"


def _pack_sounds() -> Optional[set]:
    """The voice pack's sounds (UFR_DIR/core/phonemes.json, the file the singer reads), or None
    when this host has no pack."""
    ufr = os.environ.get("UFR_DIR")
    path = os.path.join(ufr, "core", "phonemes.json") if ufr else None
    if not path or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return set(json.load(f))


def _pack_by_language(pack: Optional[set]) -> Optional[Dict[str, List[str]]]:
    """{language g2p sings: the pack's sounds with that language's prefixes}"""
    if pack is None:
        return None
    return {code: sorted(p for p in pack if any(p.startswith(x) for x in languages.get(code, "prefixes", [])))
            for code in sorted(languages.sung_by_g2p())}


def _check_reading(word: str, sounds: str, lang: str, pack: Optional[set]) -> str:
    """The reading with its whitespace normalised, or a 400 naming the word: voice sounds
    ("it/k it/w") in the pack, or with no pack in one of the language's prefixes; ARPAbet only
    for English; never a mix (#484). g2p checks it again against the pack it sings with."""
    if lang not in languages.sung_by_g2p():
        raise HTTPException(400, f"{word!r}: {lang!r} is not a language g2p sings "
                                 f"({', '.join(sorted(languages.sung_by_g2p()))})")
    toks = str(sounds).split()
    if not toks:
        raise HTTPException(400, f"{word!r}: the reading is empty")
    voiced = ["/" in t for t in toks]
    if any(voiced) and not all(voiced):
        raise HTTPException(400, f"{word!r}: give the reading all in voice sounds (it/k) or all in "
                                 f"ARPAbet (K AA1), not both: {' '.join(toks)}")
    if not any(voiced):
        if lang != "en":
            raise HTTPException(400, f"{word!r}: ARPAbet is for English readings; give a "
                                     f"{languages.get(lang, 'name')} reading in voice sounds (it/k ...)")
        return " ".join(toks)   # g2p checks ARPAbet against its own sound list, as before #484
    if pack is not None:
        bad = [t for t in toks if t not in pack]
        if bad:
            raise HTTPException(400, f"{word!r}: the voice pack has no sound {bad[0]!r}")
    else:
        prefixes = languages.get(lang, "prefixes", [])
        bad = [t for t in toks if not re.match(r"^[a-z]+/[a-z0-9]+$", t)
               or not any(t.startswith(x) for x in prefixes)]
        if bad:
            raise HTTPException(400, f"{word!r}: {bad[0]!r} is not a {languages.get(lang, 'name')} "
                                     f"voice sound ({', '.join(prefixes)}...)")
    return " ".join(toks)


@app.get("/api/songs/{slug}/pronunciation")
def api_pronunciation(slug: str) -> Dict:
    """The song page's Pronunciation list (eeron-omat-taskit#144, #484): every place a part sings
    a word the English dictionary reads more than one way, its readings (the one sung by default
    first) and the English reading chosen there; every saved reading in any language ("saved");
    the chosen places the score no longer has; the song's language, the languages g2p sings, and
    the voice pack's sounds for each (null with no pack, and "note" says so)."""
    song = _require(slug)
    cleaned = _cleaned_or_400(song)
    every = song_places.places(cleaned)
    readings = _readings(sorted({p["word"] for p in every}))
    english_note = None
    if readings is None:
        from src.sing_song.main import sing_venv
        english_note = (f"The English dictionary's readings are not listed: no singer at {sing_venv()} "
                        "(scripts/install-singer.sh). Readings typed in voice sounds still work.")
        readings = {}
    choices = song_places.load(song.dir)
    chosen = {song_places.key(c): c["sounds"] for c in choices["places"] if c["lang"] == "en"}
    rows = [dict(p, readings=readings[p["word"]], chosen=chosen.get(song_places.key(p)))
            for p in every if p["word"] in readings]
    saved = [dict(c) for c in choices["places"]]
    saved += [{"bar": None, "beat": None, "word": w, "sounds": v, "lang": lang}
              for lang, m in choices["everywhere"].items() for w, v in m.items()]
    pack = _pack_by_language(_pack_sounds())
    return {"places": rows, "everywhere": choices["everywhere"], "saved": saved,
            "stale": song_places.stale(choices, cleaned), "language": song.language,
            "languages": [{"code": c, "name": n} for c, n in languages.names().items()
                          if c in languages.sung_by_g2p()],
            "pack": pack, "note": None if pack is not None else NO_PACK, "english_note": english_note}


@app.post("/api/songs/{slug}/pronunciation")
def api_pronunciation_save(slug: str, body: Dict = None) -> Dict:
    """Save the readings chosen on the song page (#484): {"places": [{"bar", "beat", "word",
    "sounds", "lang"}], "everywhere": {lang: {word: sounds}}, "langs": [lang, ...]}. Only the
    readings in a language `langs` names are replaced; every other language's are kept. With no
    `langs`, an entry with no `lang`, or a flat everywhere map {word: sounds} (an older page or a
    script), the language is English, which is all a save could mean before #484. A place left out
    sings the dictionary's reading."""
    body = body or {}
    pack = _pack_sounds()
    try:
        langs = [str(x) for x in (body.get("langs") or ["en"])]
        sent = song_places.normalise({"places": list(body.get("places") or []),
                                      "everywhere": dict(body.get("everywhere") or {})})
        chosen = [{"bar": int(c["bar"]), "beat": float(c["beat"]),
                   "word": languages.word_key(str(c["word"]), c["lang"]),
                   "sounds": _check_reading(str(c["word"]), c["sounds"], c["lang"], pack), "lang": c["lang"]}
                  for c in sent["places"]]
        everywhere = {lang: {languages.word_key(str(w), lang): _check_reading(str(w), v, lang, pack)
                             for w, v in m.items()} for lang, m in sent["everywhere"].items()}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise HTTPException(400, "Give places as [{bar, beat, word, sounds, lang}], everywhere as "
                                 "{lang: {word: sounds}} and langs as [lang, ...]")
    outside = sorted(({c["lang"] for c in chosen} | set(everywhere)) - set(langs))
    if outside:
        raise HTTPException(400, f"readings in {', '.join(outside)} are not in langs {langs}")
    with state.song_lock(slug):
        song = _require(slug)
        old = song_places.load(song.dir)
        kept = [c for c in old["places"] if c["lang"] not in langs]
        kept_every = {lang: m for lang, m in old["everywhere"].items() if lang not in langs}
        song_places.save(song.dir, {"places": kept + chosen, "everywhere": {**kept_every, **everywhere}})
    reply = api_pronunciation(slug)
    return reply


@app.post("/api/songs/{slug}/publish-settings")
async def api_publish_settings(slug: str, body: Dict = None) -> Dict:
    """Save the tempo, tempo changes and credits without publishing (#400).

    Refused while a job runs: a publish writes the same record when it finishes,
    and the state file is saved whole.
    """
    song = _require(slug)
    if _publish_busy(song):
        raise HTTPException(409, "A scan, clean, check or publish is running — save the "
                                 "settings after it finishes.")
    settings = _publish_settings(song, body or {})
    with state.song_lock(slug):
        song = _require(slug)
        song.data.setdefault("publish", {}).update(settings)
        song.save()
    return _derived(song)


def _publish_records_now(slug: str) -> Dict[str, Dict]:
    """The song's publish records as saved now, not as the job found them (#483)."""
    with state.song_lock(slug):
        return publish.records(_require(slug).data.get("publish") or {})


def _run_publish(slug: str, choir: str, config: "publish.Config",
                 choirs: Optional[List[Dict]] = None, piano_over_sung: bool = False) -> None:
    song = _require(slug)
    log = lambda m: _job_emit(slug, "publish", m)
    try:
        cleaned = song.cleaned_path()
        settings = song.data.get("publish") or {}
        bpm = None if pipeline.has_opening_tempo(cleaned) \
            else settings.get("bpm", publish.DEFAULT_BPM)
        credits = settings.get("credits")
        if credits is None:
            credits = publish.score_credits(cleaned)
        published = publish.records(settings)
        with tempfile.TemporaryDirectory() as tmp:
            with heavy_slot.heavy_slot(f"song app publish {song.slug}", log=log) as slot:
                bundle = publish.build_bundle(
                    cleaned, tmp, initial_bpm=bpm,
                    tempo_changes=settings.get("tempo_changes"),
                    part_names=settings.get("part_names"),
                    staff_groups=settings.get("staff_groups"),
                    gradual=settings.get("gradual"),
                    log=slot.guard(log))
                slot.check()
            record = publish.publish(
                slug=song.slug, title=song.name, choir=choir, bundle=bundle,
                client=publish.Cloudflare(config), credits=credits,
                published=published, choirs=choirs,
                places=settings.get("places") or [], log=log,
                piano_over_sung=piano_over_sung,
                records_now=lambda: _publish_records_now(slug))
        record["published_against"] = state.file_fingerprint(cleaned)
        with state.song_lock(slug):
            song = _require(slug)
            pub = song.data.setdefault("publish", {})
            sites = publish.records(pub)
            if record.pop("live", True):
                staging = [c["id"] for c in (choirs or []) if c.get("staging")]
                for gone in publish.replaced(choir, sites, staging):
                    del sites[gone]
                sites[choir] = record
            else:
                # Another publish wrote the site's row after this one (#483): its
                # record, not this one, says what the site serves.
                log("Another publish replaced this one before it was recorded; "
                    "its record was not saved.")
            # One record per choir (#384): a song can be in public and one choir at once.
            pub.pop("site", None)
            pub.update({"sites": sites, "choir": choir, "error": None})
            song.save()
        _job_finish(song, "publish")
    except publish.SungOnSite as exc:
        # Kept as a kind, not a sentence, so the panel can offer the confirm (#483).
        error = {"kind": exc.kind, "choir": exc.choir, "message": str(exc)}
        with state.song_lock(slug):
            song = _require(slug)
            song.data.setdefault("publish", {}).update({"error": error, "choir": choir})
            song.save()
        _job_emit(slug, "publish", str(exc), "error")
        _job_finish(song, "publish", str(exc))
    except Exception as exc:
        traceback.print_exc()
        with state.song_lock(slug):
            song = _require(slug)
            song.data.setdefault("publish", {}).update({"error": str(exc), "choir": choir})
            song.save()
        _job_emit(slug, "publish", str(exc), "error")
        _job_finish(song, "publish", str(exc))
    finally:
        hub.emit(slug, {"type": "state"})


@app.post("/api/songs/{slug}/publish")
async def api_publish(slug: str, body: Dict = None) -> Dict:
    """Send the score, its MIDI, the timing and the credits to the site, for one choir.

    Made off the approved cleaned score, with the settings saved on the Publish
    panel; settings sent with the request are saved first, so what is published
    is what the panel shows.
    """
    song = _require(slug)
    body = body or {}
    choir = body.get("choir") or ""
    config = publish.Config.from_env()
    if config is None:
        raise HTTPException(400, publish.MISSING_CONFIG)
    fingerprint = state.file_fingerprint(song.cleaned_path())
    if not fingerprint:
        raise HTTPException(400, "Clean the score before publishing")
    if song.data.get("review", {}).get("approved_against") != fingerprint:
        raise HTTPException(409, "Review and approve the current score before publishing")
    # Names saved for another number of parts are not replaced by whatever a
    # publish sends (#402): the owner checks them and saves first.
    parts = _publish_parts(song, song.cleaned_path())
    if parts["stale"]:
        raise HTTPException(409, publish.part_count_message(len(parts["names"]))
                            + " Save the part names first.")
    settings = _publish_settings(song, body.get("settings") or {})
    # The site's own choirs (#408), read live and only once everything checked
    # here is right; a list saved while Cloudflare is out of reach is not enough
    # to publish against, and the upload would fail anyway.
    if not choir:
        raise HTTPException(400, "Choose a choir first.")
    # The app publishes piano only (#483): a sung version this app recorded for the
    # choir is not replaced unless the panel's confirm says so. The job checks the
    # site itself too, since a sung version may have come from elsewhere.
    piano_over_sung = bool(body.get("piano_only"))
    if not piano_over_sung:
        with state.song_lock(slug):
            recorded = publish.records(_require(slug).data.get("publish") or {}).get(choir) or {}
        if recorded.get("sung"):
            raise HTTPException(409, {"kind": publish.SungOnSite.kind, "choir": choir,
                                      "message": publish.sung_message(choir)})
    if _publish_busy(song):
        raise HTTPException(409, "Another job is running for this song — publish "
                                 "after it finishes.")
    listing = await asyncio.get_running_loop().run_in_executor(
        None, publish.choirs, config, state.SONGS_DIR)
    if listing["error"]:
        raise HTTPException(502, f"Could not read the site's choirs: {listing['error']}")
    known = [c["id"] for c in listing["choirs"]]
    if choir not in known:
        raise HTTPException(400, f"Choose a choir: {', '.join(known) or 'the site has none'}")
    # Every part name in the site's voice tree, or a place said for it (#430),
    # before the job starts: refused here, the panel can say which names.
    names = settings.get("part_names") or publish.score_parts(song.cleaned_path())
    tree = await asyncio.get_running_loop().run_in_executor(
        None, _voice_tree_check, config, names, settings.get("places") or [])
    if tree.get("error"):
        raise HTTPException(tree.get("status", 400), tree["error"])
    if _publish_busy(song) or not state.start_job(
            song, "publish", WRITERS, fingerprint):
        raise HTTPException(409, "Another job is running for this song — publish "
                                 "after it finishes.")
    with state.song_lock(slug):
        song = _require(slug)
        song.data.setdefault("publish", {}).update(settings)
        song.save()
    asyncio.get_running_loop().run_in_executor(None, _run_publish, slug, choir, config,
                                               listing["choirs"], piano_over_sung)
    return {"started": True}


def _voice_tree_check(config: Optional["publish.Config"], names: List[str],
                      places: List[Dict]) -> Dict:
    """The site's voice tree against these part names (#430): which it does not
    know, the places it offers, and whether `places` says where each one goes."""
    if config is None:
        return {"available": False, "unknown": [], "places": [], "error": publish.MISSING_CONFIG}
    try:
        tree = publish.voice_tree(publish.Cloudflare(config))
    except publish.PublishError as exc:
        return {"available": False, "unknown": [], "places": [], "status": 502,
                "error": f"Could not read the site's voice tree: {exc}"}
    if tree is None:  # a site from before the voice tree: nothing to ask
        return {"available": False, "unknown": [], "places": [], "error": None}
    out = {"available": True, "unknown": publish.unknown_names(tree, names),
           "places": publish.tree_places(tree), "error": None}
    try:
        publish.place_statements(tree, names, places)
    except publish.PublishError as exc:
        out["error"] = str(exc)
    return out


@app.post("/api/voice-tree")
async def api_voice_tree(body: Dict = None) -> Dict:
    """Which of these part names the site's voice tree does not know, and its
    places, for the Publish panel to ask where each new one goes (#430)."""
    names = (body or {}).get("names") or []
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise HTTPException(400, "names must be a list of part names")
    reply = await asyncio.get_running_loop().run_in_executor(
        None, _voice_tree_check, publish.Config.from_env(), names, [])
    reply.pop("status", None)
    if reply["available"]:
        reply["error"] = None  # no places were given: the unknown names say it
    return reply


@app.get("/api/choirs")
async def api_choirs() -> Dict:
    """The choirs the Publish panel offers: the site's own, read from its database (#408)."""
    return await asyncio.get_running_loop().run_in_executor(
        None, publish.choirs, publish.Config.from_env(), state.SONGS_DIR)


@app.get("/api/resing")
async def api_resing() -> Dict:
    """Which songs still need a re-sing with the current singer, and how each run is going
    (#453): the song list shows it. The site says which songs are sung (resing.py)."""
    def answer() -> Dict:
        version = progress.singer_version()
        listed = resing.site(publish.Config.from_env(), state.SONGS_DIR)
        return resing.overview(state.list_songs(), listed, version, progress.singer_changes())
    return await asyncio.get_running_loop().run_in_executor(None, answer)


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
            try:
                if _on_cleaned_saved(slug):
                    hub.emit(slug, {"type": "state"})
            except job_state.SongGone:
                pass  # deleted while it was being re-checked (#428)


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


def _is_save_temp(name: str) -> bool:
    """A hidden temp file a score save writes (`lyric_txt._save_mscx`)."""
    return (name.startswith(".") and name.endswith(".tmp")
            and (f"{LYRICS_TMP}." in name or ".mscx." in name))


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
        # A lyric import cut off by the restart leaves its temp score (#482), and a
        # save cut off mid-write leaves the hidden file it was writing
        # (`.<score>.<random>.tmp`, `lyric_txt._save_mscx`).
        try:
            for name in os.listdir(song.dir):
                if name.endswith(LYRICS_TMP) or _is_save_temp(name):
                    os.remove(os.path.join(song.dir, name))
        except OSError:
            traceback.print_exc()
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
