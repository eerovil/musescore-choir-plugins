import os
from pathlib import Path
import pickle
from urllib.request import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import datetime
import logging

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


def upload_video(youtube, file_path, title, description, privacy="unlisted"):
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

    media = MediaFileUpload(file_path, chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = request.execute()
    return response["id"]


def create_playlist(youtube, title):
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


def upload_to_youtube(video_paths, basename):
    logging.info("Authenticating with YouTube...")
    youtube = get_authenticated_service()

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    playlist_title = f"{basename} Stemmanauhat - {timestamp}"
    playlist_id = create_playlist(youtube, playlist_title)
    logging.info(f"Created playlist: {playlist_title}")

    for video_path in video_paths:
        title = Path(video_path).stem
        logging.info(f"Uploading {video_path} as '{title}'...")
        video_id = upload_video(youtube, video_path, title, description="Practice track")
        logging.info(f"Uploaded: https://youtu.be/{video_id}")
        add_video_to_playlist(youtube, playlist_id, video_id)
        logging.info(f"Added to playlist.")

    logging.info(f"All videos uploaded and added to playlist.")
