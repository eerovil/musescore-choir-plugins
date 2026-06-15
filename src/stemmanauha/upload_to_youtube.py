import os
from pathlib import Path
import pickle
import json
import random
import time
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
import datetime
import logging
from google.auth.transport.requests import Request

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube"]

# Reasons worth retrying with backoff (transient rate-limit / server errors). A 403
# "quotaExceeded" is the *daily* quota — not retryable in the short term, so we don't.
_RETRY_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "backendError", "internalError"}
_MAX_RETRIES = 6


def _error_reason(exc):
    try:
        return json.loads(exc.content.decode("utf-8"))["error"]["errors"][0].get("reason", "")
    except Exception:
        return ""


def _should_retry(exc):
    status = getattr(exc.resp, "status", None)
    if status in (429, 500, 502, 503):
        return True
    return status == 403 and _error_reason(exc) in _RETRY_REASONS


class QuotaExceeded(RuntimeError):
    pass


def _with_retry(fn, log=None):
    """Call fn(), backing off on 429/5xx/rate-limit. Raises QuotaExceeded on a
    daily-quota 403 with a clear message; re-raises other errors."""
    log = log or logging.info
    delay = 1.0
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return fn()
        except HttpError as exc:
            status = getattr(exc.resp, "status", None)
            reason = _error_reason(exc)
            if status == 403 and reason == "quotaExceeded":
                raise QuotaExceeded(
                    "YouTube daily quota exceeded — try again after it resets "
                    "(midnight Pacific) or request more quota."
                ) from exc
            if _should_retry(exc) and attempt < _MAX_RETRIES:
                wait = delay + random.uniform(0, delay * 0.5)
                log(f"YouTube rate-limited ({status} {reason or ''}); retrying in {wait:.1f}s "
                    f"(attempt {attempt + 1}/{_MAX_RETRIES})…")
                time.sleep(wait)
                delay = min(delay * 2, 64)
                continue
            raise


def _execute(request, log=None):
    return _with_retry(request.execute, log)

def get_authenticated_service():
    creds = None
    token_file = "token.pickle"

    # Try to load saved credentials
    if os.path.exists(token_file):
        with open(token_file, "rb") as token:
            creds = pickle.load(token)

    # If there are no valid credentials, do the OAuth flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "client_secrets.json", SCOPES
            )
            creds = flow.run_local_server(port=0)

        # Save the credentials
        with open(token_file, "wb") as token:
            pickle.dump(creds, token)

    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload_video(youtube, file_path, title, description, privacy="unlisted", progress=None, log=None):
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": ["choir", "practice", "musescore"],
            "categoryId": "10",  # Music
        },
        "status": {
            "privacyStatus": privacy
        }
    }

    # Resumable, chunked upload so we can report progress per chunk.
    media = MediaFileUpload(file_path, chunksize=4 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = _with_retry(request.next_chunk, log)  # resumes on rate-limit
        if status and progress:
            progress(f"Uploading {title}: {int(status.progress() * 100)}%")
    if progress:
        progress(f"Uploading {title}: 100%")
    return response["id"]


def create_playlist(youtube, title):
    try:
        request = youtube.playlists().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": title,
                    "description": "Practice tracks playlist",
                },
                "status": {
                    "privacyStatus": "unlisted"
                }
            }
        )
        response = _execute(request)
        return response["id"]
    except QuotaExceeded:
        raise
    except Exception as e:
        logging.error(f"Error creating playlist: {e}")


def add_video_to_playlist(youtube, playlist_id, video_id):
    request = youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {
                    "kind": "youtube#video",
                    "videoId": video_id
                }
            }
        }
    )
    _execute(request)


def upload_to_youtube(song_dir, video_paths, extra_playlist_id=None, log=None,
                      progress=None, display_name=None, on_uploaded=None):
    """Upload merged videos.

    display_name : human song name used for the playlist + video titles (instead
                   of the folder slug).
    log(msg)     : milestones; progress(msg): live per-chunk upload percentage.
    on_uploaded(info) : called after each successful upload with
                   {title, video_id, url, playlist_id} so the caller can persist it.
    """
    log = log or logging.info
    basename = os.path.basename(song_dir)
    nice = display_name or basename

    log("Authenticating with YouTube…")
    youtube = get_authenticated_service()

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    playlist_title = f"{nice} Stemmanauhat - {timestamp}"
    playlist_id = create_playlist(youtube, playlist_title)
    log(f"Created playlist: {playlist_title}")

    total = len(video_paths)
    for i, video_path in enumerate(video_paths, start=1):
        stem = Path(video_path).stem
        # File stems are "<slug> <part>"; show the nice name in the title.
        part = stem[len(basename) + 1:] if stem.startswith(basename + " ") else stem
        title = f"{nice} {part}".strip()
        log(f"Uploading {i}/{total}: {title}…")
        video_id = upload_video(youtube, video_path, title, description="Practice track", progress=progress, log=log)
        url = f"https://youtu.be/{video_id}"
        log(f"Uploaded {title}: {url}")
        if playlist_id:
            add_video_to_playlist(youtube, playlist_id, video_id)
        if extra_playlist_id:
            add_video_to_playlist(youtube, extra_playlist_id, video_id)
        if on_uploaded:
            on_uploaded({"title": title, "part": part, "video_id": video_id, "url": url,
                         "playlist_id": playlist_id, "playlist_title": playlist_title})

    log("All videos uploaded.")


def _update_video_title(youtube, video_id, title, log=None):
    """Set a video's title (the snippet update needs the existing categoryId)."""
    resp = _execute(youtube.videos().list(part="snippet", id=video_id), log=log)
    items = resp.get("items", [])
    if not items:
        return False
    snippet = items[0]["snippet"]
    snippet["title"] = title
    _execute(youtube.videos().update(part="snippet", body={"id": video_id, "snippet": snippet}), log=log)
    return True


def _update_playlist_title(youtube, playlist_id, title, log=None):
    resp = _execute(youtube.playlists().list(part="snippet", id=playlist_id), log=log)
    items = resp.get("items", [])
    if not items:
        return
    snippet = items[0]["snippet"]
    snippet["title"] = title
    _execute(youtube.playlists().update(part="snippet", body={"id": playlist_id, "snippet": snippet}), log=log)


def rename_uploads(uploads, old_name, new_name, log=None):
    """Retitle already-uploaded videos (and their playlist) from old_name to new_name.

    Each video title is "<name> <part>"; the playlist is "<name> Stemmanauhat - …".
    Returns the updated uploads list (titles/playlist_title rewritten).
    """
    log = log or logging.info
    if not uploads:
        return uploads
    youtube = get_authenticated_service()
    renamed_playlists = {}  # pid -> new title (applied once via API, mirrored to all entries)
    updated = []
    for u in uploads:
        part = u.get("part")
        if part is None:  # legacy entry without a stored part
            t = u.get("title", "")
            part = t[len(old_name) + 1:] if t.startswith(old_name + " ") else t
        new_title = f"{new_name} {part}".strip()
        nu = dict(u)
        try:
            _update_video_title(youtube, u["video_id"], new_title, log=log)
            nu["title"] = new_title
            log(f"Renamed video → {new_title}")
        except QuotaExceeded:
            raise
        except Exception as e:
            log(f"Could not rename {u.get('video_id')}: {e}")
        pid = u.get("playlist_id")
        if pid:
            if pid not in renamed_playlists:
                pt = u.get("playlist_title", "")
                npt = (new_name + pt[len(old_name):]) if pt.startswith(old_name + " ") else pt
                try:
                    if npt and npt != pt:
                        _update_playlist_title(youtube, pid, npt, log=log)
                        log(f"Renamed playlist → {npt}")
                except QuotaExceeded:
                    raise
                except Exception as e:
                    log(f"Could not rename playlist {pid}: {e}")
                renamed_playlists[pid] = npt or pt
            nu["playlist_title"] = renamed_playlists[pid]
        updated.append(nu)
    return updated


def delete_videos(video_ids, log=None):
    """Delete uploaded videos from YouTube (for re-upload). Best-effort per id."""
    log = log or logging.info
    if not video_ids:
        return
    youtube = get_authenticated_service()
    for vid in video_ids:
        try:
            _execute(youtube.videos().delete(id=vid), log=log)
            log(f"Deleted {vid} from YouTube.")
        except QuotaExceeded:
            raise
        except Exception as e:
            log(f"Could not delete {vid}: {e}")
