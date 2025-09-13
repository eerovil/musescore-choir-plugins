#!/usr/bin/env python3

HELP_TEXT = """
IF ONLY RECORDING AUDIO: just run export.qml plugin in musescore
you don't need this script

Install QuickRecorder.
Set up QuickRecorder: Add keyboard shortcuts to start record and stop
	-- Send Shift+Control+Cmd + R to start
    -- Send Shift+Control+Cmd + S to stop

Setup musescore 3 to export with keyboard shortcut.
i.e. install plugins and run plugin export.qml with keyboard shortcut
	-- Send Command-Option-E

Open wanted sheet music in musescore
Test quickrecorder 
"""


import argparse
import dotenv
import os

parser = argparse.ArgumentParser(
    description="Record practice video from MuseScore using QuickRecorder." + "\n" + HELP_TEXT
)
# Pass song name
parser.add_argument("basename", help="Name of the song to record: MUST MATCH the MuseScore file name")
args = parser.parse_args()

# Create output directory if it doesn't exist
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
songs_dir = os.path.join(SCRIPT_DIR, "songs")
if not os.path.exists(songs_dir):
    os.makedirs(songs_dir)

# Load environment variables from .env or .env.default file
dotenv_path = os.path.join(SCRIPT_DIR, ".env")
if not os.path.exists(dotenv_path):
    dotenv_path = os.path.join(SCRIPT_DIR, ".env.default")
dotenv.load_dotenv(dotenv_path)

song_dir = os.path.join(songs_dir, args.basename)

# Check that song directory exists
if not os.path.exists(song_dir):
    print(f"Song directory {song_dir} does not exist.")
    print("Make sure you have a directory in songs/ with the same name as the song")
    print("and that it contains the musescore file.")
    exit(1)

YOUTUBE_CLIENT_SECRETS_PATH = os.path.join(SCRIPT_DIR, os.getenv("YOUTUBE_CLIENT_SECRETS_PATH", ""))
youtube = False
if YOUTUBE_CLIENT_SECRETS_PATH and os.path.exists(YOUTUBE_CLIENT_SECRETS_PATH):
    youtube = True
    print("YouTube upload enabled.")

from src.stemmanauha.create_video import run
run(
    song_dir=song_dir,
    youtube=youtube,
)
