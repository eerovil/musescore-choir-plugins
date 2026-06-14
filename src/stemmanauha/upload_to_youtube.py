import os
from pathlib import Path
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import datetime
import logging
from google.auth.transport.requests import Request

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube"]

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


def upload_video(youtube, file_path, title, description, privacy="unlisted", progress=None):
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
        status, response = request.next_chunk()
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
        response = request.execute()
        return response["id"]
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
    request.execute()


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
        video_id = upload_video(youtube, video_path, title, description="Practice track", progress=progress)
        url = f"https://youtu.be/{video_id}"
        log(f"Uploaded {title}: {url}")
        if playlist_id:
            add_video_to_playlist(youtube, playlist_id, video_id)
        if extra_playlist_id:
            add_video_to_playlist(youtube, extra_playlist_id, video_id)
        if on_uploaded:
            on_uploaded({"title": title, "video_id": video_id, "url": url,
                         "playlist_id": playlist_id, "playlist_title": playlist_title})

    log("All videos uploaded.")


def delete_videos(video_ids, log=None):
    """Delete uploaded videos from YouTube (for re-upload). Best-effort per id."""
    log = log or logging.info
    if not video_ids:
        return
    youtube = get_authenticated_service()
    for vid in video_ids:
        try:
            youtube.videos().delete(id=vid).execute()
            log(f"Deleted {vid} from YouTube.")
        except Exception as e:
            log(f"Could not delete {vid}: {e}")
