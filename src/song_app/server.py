"""FastAPI backend for the `song` app — a thin, state-aware door over the toolkit."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import traceback
from typing import Dict, List, Optional, Set

import dotenv
from fastapi import FastAPI, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import health, pipeline, state

SCRIPT_DIR = state.SCRIPT_DIR
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# Load environment (MUSESCORE_CLI_PATH etc.), .env then .env.default.
_env = os.path.join(SCRIPT_DIR, ".env")
dotenv.load_dotenv(_env if os.path.exists(_env) else os.path.join(SCRIPT_DIR, ".env.default"))

app = FastAPI(title="song")


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
        """Thread-safe broadcast (callable from worker threads)."""
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._send(slug, msg), self.loop)


hub = Hub()


def _require(slug: str) -> state.Song:
    song = state.load(slug)
    if not song:
        raise HTTPException(404, f"No song '{slug}'")
    return song


def _lock_path(song: state.Song) -> str:
    return song.path(".recording.lock")


def is_recording(song: state.Song) -> bool:
    """True if a recording is active in *this* server process.

    A lock written by a previous (now-dead) server is treated as stale and cleared,
    so a crash can't leave a song permanently locked.
    """
    path = _lock_path(song)
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


def _media_list(song: state.Song) -> List[Dict]:
    """Merged per-voice videos (and the raw recording) available for review."""
    vdir = song.path("media", "video")
    if not os.path.isdir(vdir):
        return []
    out = []
    for name in sorted(os.listdir(vdir)):
        if not name.lower().endswith((".mov", ".mp4")):
            continue
        prefix = song.slug + " "
        is_merged = name.startswith(prefix)
        out.append({
            "name": name,
            "label": name[len(prefix):].rsplit(".", 1)[0] if is_merged else "raw recording",
            "merged": is_merged,
            "url": f"/api/songs/{song.slug}/media/{name}",
        })
    out.sort(key=lambda m: (not m["merged"], m["label"]))
    return out


def _derived(song: state.Song) -> Dict:
    """State plus computed flags the frontend needs."""
    cleaned = song.cleaned_path()
    pdf = song.source_path("pdf")
    issues = song.data.get("health", {}).get("issues", [])
    return {
        **song.data,
        "slug": song.slug,
        "stages": state.STAGES,
        "stage_index": state.STAGES.index(song.stage) if song.stage in state.STAGES else 0,
        "has_pdf": bool(pdf and os.path.exists(pdf)),
        "has_cleaned": bool(cleaned and os.path.exists(cleaned)),
        "open_issues": [i for i in issues if i.get("status") == "open"],
        "recording": is_recording(song),
        "media": _media_list(song),
    }


# --------------------------------------------------------------------------
# Library + create
# --------------------------------------------------------------------------
@app.get("/api/songs")
def api_songs() -> List[Dict]:
    return [s.to_summary() for s in state.list_songs()]


@app.post("/api/songs")
async def api_create(
    name: str = Form(...),
    per_system: bool = Form(False),
    xml: UploadFile = None,
    pdf: UploadFile = None,
) -> Dict:
    if not name.strip():
        raise HTTPException(400, "Name is required")
    if xml is None:
        raise HTTPException(400, "A MuseScore/MusicXML file is required")

    song = state.create(name.strip(), per_system)
    # Save the score file.
    xml_name = os.path.basename(xml.filename)
    with open(song.path(xml_name), "wb") as f:
        f.write(await xml.read())
    song.data.setdefault("sources", {})["xml"] = xml_name
    # Save the PDF (optional but expected).
    if pdf is not None and pdf.filename:
        pdf_name = os.path.basename(pdf.filename)
        with open(song.path(pdf_name), "wb") as f:
            f.write(await pdf.read())
        song.data["sources"]["pdf"] = pdf_name
    song.set_stage("clean")
    song.save()
    return {"slug": song.slug}


@app.get("/api/songs/{slug}")
def api_song(slug: str) -> Dict:
    return _derived(_require(slug))


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
    return {"ok": True}


def _run_clean(slug: str) -> None:
    song = _require(slug)
    xml = song.source_path("xml")
    log = lambda m: hub.emit(slug, {"type": "log", "line": m})
    try:
        cleaned, _ = pipeline.run_clean(
            xml, song.dir, per_system=(song.mode == "per-system"), log=log,
        )
        rel = os.path.relpath(cleaned, song.dir)
        song.data["cleaned"] = rel
        song.data["cleaned_fingerprint"] = state.file_fingerprint(cleaned)
        # Run the health check.
        found = health.scan(cleaned)
        prev = song.data.get("health", {}).get("issues", [])
        song.data["health"] = {
            "checked_against": song.data["cleaned_fingerprint"],
            "issues": health.merge_issues(found, prev),
        }
        song.set_stage("fix")
        song.save()
        n = len(_derived(song)["open_issues"])
        log(f"Done. {n} issue(s) to review." if n else "Done. No issues found.")
        hub.emit(slug, {"type": "state"})
    except Exception as exc:  # surface to the UI rather than dying silently
        traceback.print_exc()
        hub.emit(slug, {"type": "error", "line": str(exc)})


@app.post("/api/songs/{slug}/clean")
async def api_clean(slug: str) -> Dict:
    _require(slug)
    asyncio.get_running_loop().run_in_executor(None, _run_clean, slug)
    return {"started": True}


# --------------------------------------------------------------------------
# Fix stage — health check
# --------------------------------------------------------------------------
def _rescan(song: state.Song) -> None:
    cleaned = song.cleaned_path()
    if not cleaned or not os.path.exists(cleaned):
        return
    found = health.scan(cleaned)
    prev = song.data.get("health", {}).get("issues", [])
    song.data["cleaned_fingerprint"] = state.file_fingerprint(cleaned)
    song.data["health"] = {
        "checked_against": song.data["cleaned_fingerprint"],
        "issues": health.merge_issues(found, prev),
    }
    song.save()


@app.post("/api/songs/{slug}/rescan")
def api_rescan(slug: str) -> Dict:
    song = _require(slug)
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


@app.post("/api/songs/{slug}/stage/{stage}")
def api_set_stage(slug: str, stage: str) -> Dict:
    """Manual stage navigation (left rail)."""
    song = _require(slug)
    if stage not in state.STAGES:
        raise HTTPException(400, "Unknown stage")
    song.set_stage(stage)
    song.save()
    return _derived(song)


# --------------------------------------------------------------------------
# Lyrics stage
# --------------------------------------------------------------------------
@app.get("/api/playlists")
def api_playlists() -> List[Dict]:
    return state.load_playlists()


@app.get("/api/prompt")
def api_prompt() -> Dict:
    path = os.path.join(SCRIPT_DIR, "lyric_json_prompt.txt")
    with open(path, "r", encoding="utf-8") as f:
        return {"prompt": f.read()}


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
    song = _require(slug)
    cleaned = song.cleaned_path()
    if not cleaned or not os.path.exists(cleaned):
        raise HTTPException(400, "Clean the score first")
    json_text = (body or {}).get("json", "")
    if not json_text.strip():
        raise HTTPException(400, "Paste the lyric JSON first")
    json_path = song.path("lyrics.json")
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(json_text)
    try:
        warnings = pipeline.run_lyric_import(json_path, cleaned, replace=True)
    except Exception as exc:
        raise HTTPException(400, f"Import failed: {exc}")
    song.data["lyrics"] = {
        "json": "lyrics.json",
        "imported_against": state.file_fingerprint(cleaned),
        "warnings": warnings,
    }
    song.data["cleaned_fingerprint"] = state.file_fingerprint(cleaned)
    if not warnings:
        song.set_stage("review")
    song.save()
    return _derived(song)


# --------------------------------------------------------------------------
# Files + local app actions
# --------------------------------------------------------------------------
@app.get("/api/songs/{slug}/pdf")
def api_pdf(slug: str):
    song = _require(slug)
    pdf = song.source_path("pdf")
    if not pdf or not os.path.exists(pdf):
        raise HTTPException(404, "No PDF")
    return FileResponse(pdf, media_type="application/pdf")


@app.get("/api/songs/{slug}/render")
def api_render(slug: str, doc: str = "cleaned"):
    """Render a score variant to PDF via MuseScore, for the viewer tabs.

    doc = original          -> the OCR'd input score (converted to .mscx)
          cleaned_nolyrics  -> the cleaned score with lyrics stripped
          cleaned           -> the cleaned score as-is (with lyrics)
    """
    song = _require(slug)
    try:
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
        rendered = pipeline.render_score_pdf(mscx)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc))
    return FileResponse(rendered, media_type="application/pdf")


@app.post("/api/songs/{slug}/open-score")
def api_open_score(slug: str) -> Dict:
    song = _require(slug)
    cleaned = song.cleaned_path()
    if not cleaned or not os.path.exists(cleaned):
        raise HTTPException(400, "Nothing to open")
    subprocess.Popen(["open", "-a", "MuseScore 3", cleaned])
    return {"ok": True}


@app.post("/api/songs/{slug}/reveal-pdf")
def api_reveal_pdf(slug: str) -> Dict:
    song = _require(slug)
    pdf = song.source_path("pdf")
    if not pdf or not os.path.exists(pdf):
        raise HTTPException(404, "No PDF")
    subprocess.Popen(["open", "-R", pdf])
    return {"ok": True}


# --------------------------------------------------------------------------
# Record stage
# --------------------------------------------------------------------------
def _run_record(slug: str, opts: Dict) -> None:
    song = _require(slug)
    log = lambda m: hub.emit(slug, {"type": "log", "line": m})
    progress = lambda m: hub.emit(slug, {"type": "progress", "line": m})

    def on_uploaded(info: Dict) -> None:
        rec = song.data.setdefault("record", {})
        rec.setdefault("uploads", []).append(info)
        rec["playlist_id"] = info.get("playlist_id")
        if info.get("playlist_id"):
            state.save_playlist(info["playlist_id"], info.get("playlist_title"))
        song.save()
        hub.emit(slug, {"type": "state"})

    try:
        from src.stemmanauha.create_video import run
        merge_only = bool(opts.get("merge_only"))
        upload_only = bool(opts.get("upload_only"))
        youtube = bool(opts.get("youtube")) or upload_only
        if youtube:  # fresh upload run — clear any stale record of prior uploads
            song.data.setdefault("record", {})["uploads"] = []
            song.save()
            if opts.get("playlist"):  # remember the chosen target playlist
                state.save_playlist(opts["playlist"], opts.get("playlist_title"))
        log("Uploading to YouTube…" if upload_only
            else "Re-merging with new offset…" if merge_only
            else "Starting recording pipeline…")
        results = run(
            song_dir=song.dir,
            youtube=youtube,
            extra_playlist_id=opts.get("playlist") or None,
            audio_delay_ms=int(opts.get("audio_delay_ms", 1300)),
            redo_mp3=bool(opts.get("redo_mp3")),
            redo_video=bool(opts.get("redo_video")),
            merge_only=merge_only,
            upload_only=upload_only,
            log=log, progress=progress,
            display_name=song.name, on_uploaded=on_uploaded,
        )
        rec = song.data.setdefault("record", {})
        if not upload_only:
            rec["exported"] = True
            rec["audio_delay_ms"] = int(opts.get("audio_delay_ms", 1300))
            rec["outputs"] = [os.path.basename(str(r)) for r in (results or [])]
        rec["error"] = None
        # After recording, move on to the Upload stage; uploading stays there.
        if not merge_only:
            song.set_stage("upload")
        song.save()
        log("Upload complete." if upload_only else f"Done. {len(results or [])} video(s) ready.")
    except Exception as exc:
        traceback.print_exc()
        song.data.setdefault("record", {})["error"] = str(exc)
        song.save()
        hub.emit(slug, {"type": "error", "line": str(exc)})
    finally:
        lock = _lock_path(song)
        if os.path.exists(lock):
            os.remove(lock)
        hub.emit(slug, {"type": "state"})


@app.post("/api/songs/{slug}/record")
async def api_record(slug: str, body: Dict = None) -> Dict:
    song = _require(slug)
    if is_recording(song):
        raise HTTPException(409, "A recording is already running for this song.")
    # Take the lock before launching so a second request (e.g. after a page
    # refresh) is rejected rather than starting a clashing recording.
    with open(_lock_path(song), "w") as f:
        f.write(str(os.getpid()))
    asyncio.get_running_loop().run_in_executor(None, _run_record, slug, body or {})
    return {"started": True}


@app.get("/api/songs/{slug}/media/{name}")
def api_media(slug: str, name: str):
    song = _require(slug)
    safe = os.path.basename(name)
    path = song.path("media", "video", safe)
    if not os.path.exists(path):
        raise HTTPException(404, "No such media")
    return FileResponse(path, media_type="video/quicktime")


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
    return _derived(song)


@app.post("/api/songs/{slug}/reveal-media")
def api_reveal_media(slug: str) -> Dict:
    song = _require(slug)
    vdir = song.path("media", "video")
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
            song = state.load(slug)
            if not song:
                continue
            # Only react if the file actually changed since our last scan.
            fp = state.file_fingerprint(song.cleaned_path())
            if fp and fp != song.data.get("cleaned_fingerprint"):
                _rescan(song)
                hub.emit(slug, {"type": "state"})


@app.on_event("startup")
async def _startup() -> None:
    hub.loop = asyncio.get_running_loop()
    if os.path.isdir(state.SONGS_DIR):
        asyncio.create_task(_watch_cleaned())


# --------------------------------------------------------------------------
# Static frontend (mounted last so /api/* wins)
# --------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
