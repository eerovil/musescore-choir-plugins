#!/usr/bin/env python3

import argparse
import os
import time
import subprocess
from pathlib import Path
import unicodedata
from dotenv import load_dotenv
import obsws_python as obs  # This is how you originally used it — and it works
import logging

from .upload_to_youtube import get_authenticated_service, upload_to_youtube

# === CONFIG ===
logging.basicConfig(level=logging.INFO)
load_dotenv()

MUSESCORE_EXPORT_PATH = os.getenv("MUSESCORE_EXPORT_PATH")
VIDEO_EXPORT_PATH = os.getenv("VIDEO_EXPORT_PATH")

HOME_DIR = str(Path.home())
MUSESCORE_EXPORT_PATH = MUSESCORE_EXPORT_PATH.replace("~", HOME_DIR) if MUSESCORE_EXPORT_PATH else None
VIDEO_EXPORT_PATH = VIDEO_EXPORT_PATH.replace("~", HOME_DIR) if VIDEO_EXPORT_PATH else None

if not MUSESCORE_EXPORT_PATH or not VIDEO_EXPORT_PATH:
    raise EnvironmentError(
        "Both MUSESCORE_EXPORT_PATH and VIDEO_EXPORT_PATH must be set in the environment."
    )

export_path = Path(MUSESCORE_EXPORT_PATH)
obs_path = Path(VIDEO_EXPORT_PATH)

SCRIPT_PATH = os.path.dirname(os.path.abspath(__file__))

musescore_show_script = Path(SCRIPT_PATH) / "show_musescore.scpt"
musescore_play_script = Path(SCRIPT_PATH) / "play_musescore.scpt"
musescore_export_script = Path(SCRIPT_PATH) / "export_musescore.scpt"
start_recording_script = Path(SCRIPT_PATH) / "start_recording.scpt"
stop_recording_script = Path(SCRIPT_PATH) / "stop_recording.scpt"


def get_mp3_duration(mp3_path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(mp3_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe error: {result.stderr.decode().strip()}")
    return float(result.stdout.decode().strip())


def get_latest_file(path: Path, pattern: str):
    files = glob_unicode(path, pattern)
    if not files:
        raise FileNotFoundError(f"No files matching {pattern} in {path}")
    # if path is mov, ignore files less than 1MB
    if pattern.endswith(".mov"):
        logging.info(f"Filtering MOV files larger than 1MB in {path}")
        files = [f for f in files if f.stat().st_size >= 1 * 1024 * 1024]  # 5MB
        if not files:
            raise FileNotFoundError(f"No MOV files larger than 5MB found in {path}")
    return max(files, key=os.path.getmtime)


def glob_unicode(path: Path, pattern: str):
    import fnmatch

    pattern_nfc = unicodedata.normalize("NFC", pattern)
    print(f"Searching for files in {path} matching pattern: {pattern_nfc}")
    ret = []
    for name in os.listdir(path):
        name_nfc = unicodedata.normalize("NFC", name)
        if fnmatch.fnmatch(name_nfc, pattern_nfc):
            ret.append(path / name_nfc)

    return ret


def get_filtered_mp3_files(mp3_basename):
    mp3_files = glob_unicode(export_path, f"{mp3_basename}*.mp3")
    to_remove = f"undefined.mp3"
    logging.info(f"Removing {to_remove} from the list of MP3s.")
    mp3_files = [f for f in mp3_files if to_remove not in f.name]

    if not mp3_files:
        raise FileNotFoundError(f"No MP3s with base name '{mp3_basename}' found.")

    latest_time = max(f.stat().st_mtime for f in mp3_files)
    threshold = latest_time - 30 * 60
    filtered = [f for f in mp3_files if f.stat().st_mtime >= threshold]

    logging.info(f"Filtered MP3s: {[f.name for f in filtered]}")
    return filtered


def record_video(song_dir, mp3_file):
    if not mp3_file or not mp3_file.exists():
        raise ValueError("A valid mp3_file must be provided to record video.")
    
    if song_dir:
        # If video already exists in song_dir/media, skip recording
        video_dir = Path(song_dir) / "media" / "video"
        if video_dir.exists() and any(video_dir.glob("*.mov")):
            logging.info(f"Video files already exist in {video_dir}, skipping recording.")
            video_file = next(video_dir.glob("*.mov"))
            return video_file

    duration = get_mp3_duration(mp3_file)
    subprocess.run(["open", "-a", "QuickRecorder"])
    time.sleep(1)

    subprocess.run(["osascript", musescore_show_script])
    time.sleep(1)

    subprocess.run(["osascript", start_recording_script])
    logging.info("Recording started.")
    time.sleep(1)
    subprocess.run(["osascript", musescore_play_script])
    logging.info("Playback started.")
    try:
        time.sleep(duration + 1)
    except KeyboardInterrupt:
        logging.info("Recording interrupted by user.")
        subprocess.run(["osascript", stop_recording_script])
        logging.info("Recording stopped.")
        raise

    subprocess.run(["osascript", stop_recording_script])
    logging.info("Recording stopped.")

    # Wait a moment for OBS to finalize the file
    time.sleep(1)

    if song_dir:
        # Find the latest .mov file in VIDEO_EXPORT_PATH
        latest_video = get_latest_file(Path(VIDEO_EXPORT_PATH), "*.mov")
        # Move it to song_dir/media
        target_dir = Path(song_dir) / "media" / "video"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / latest_video.name
        logging.info(f"Moving {latest_video} to {target_path}")
        latest_video.rename(target_path)
        logging.info(f"Video file moved to {target_dir}")
        return target_path


def merge_mp3_to_video(song_dir):
    if not song_dir:
        raise ValueError("song_dir must be set to merge MP3 to video.")

    # Find all mp3 files in song_dir/media
    media_dir = Path(song_dir) / "media"
    song_name = os.path.basename(song_dir)
    if not media_dir.exists():
        raise FileNotFoundError(f"Media directory {media_dir} does not exist.")

    mp3_files = list(media_dir.glob("*.mp3"))
    video_dir = media_dir / "video"

    if not video_dir.exists():
        raise FileNotFoundError(f"Video directory {video_dir} does not exist.")
    input_video_file = next(video_dir.glob("*.mov"), None)
    if not input_video_file:
        raise FileNotFoundError(f"No .mov file found in {video_dir}")

    results = []

    for mp3 in mp3_files:
        mp3_stem = mp3.stem
        part_name = mp3_stem.split(" ")[-1]  # Get the part after the last space

        output_path = video_dir / f"{song_name} {part_name}.mov"
        if output_path.exists():
            logging.info(f"Output video {output_path} already exists, skipping merge.")
            results.append(output_path)
            continue
        cmd = [
            "ffmpeg",
            "-i",
            str(input_video_file),
            "-i",
            str(mp3),
            "-c:v",
            "copy",
            "-filter_complex",
            "[1:a]adelay=1100|1100[a]",
            "-map",
            "0:v:0",
            "-map",
            "[a]",
            "-map",
            "1:a:0",
            "-y",
            str(output_path),
        ]
        logging.info(f"Merging {mp3.name} → {output_path.name}")
        subprocess.run(cmd, check=True, capture_output=True)
        results.append(output_path)

    logging.info(f"All videos merged: {', '.join(str(r) for r in results)}")
    return results


def wait_for_all_mp3(export_dir, timeout=120, check_interval=1):
    """
    Wait for a file ending in ALL.mp3 to be created or updated and fully written in export_dir.
    Logs any new or replaced files.
    """
    export_dir = Path(export_dir)
    seen_files = {}

    # Initialize seen_files with existing .mp3 files and their mtimes
    for f in export_dir.glob("*.mp3"):
        try:
            seen_files[f] = f.stat().st_mtime
        except FileNotFoundError:
            pass  # In case file is deleted right after

    target_file = None
    elapsed = 0

    print(f"Watching for '*ALL.mp3' in: {export_dir.resolve()}")

    while elapsed < timeout:
        for f in export_dir.glob("*.mp3"):
            try:
                mtime = f.stat().st_mtime
            except FileNotFoundError:
                continue

            if f not in seen_files or seen_files[f] != mtime:
                print(f"New or updated file detected: {f.name}")
                seen_files[f] = mtime
                elapsed = 0  # reset timeout on new activity

                if f.name.endswith("ALL.mp3"):
                    target_file = f

        if target_file and target_file.exists():
            # Wait until file size is stable for 3 seconds
            last_size = -1
            stable_seconds = 0

            while stable_seconds < 3:
                try:
                    current_size = os.path.getsize(target_file)
                    if current_size == last_size and current_size > 0:
                        stable_seconds += 1
                    else:
                        stable_seconds = 0
                    last_size = current_size
                except FileNotFoundError:
                    stable_seconds = 0
                    last_size = -1

                time.sleep(1)

            print(f"{target_file.name} has finished writing.")
            return target_file

        time.sleep(check_interval)
        elapsed += check_interval

    raise TimeoutError("Timed out waiting for '*ALL.mp3' to appear or finish writing.")


def export_mp3_from_musescore(song_dir):
    """
    Export MP3 files from MuseScore using AppleScript.
    """
    # If mp3 already exists in song_dir/mp3, skip export
    if song_dir:
        media_dir = Path(song_dir) / "media"
        if media_dir.exists() and any(media_dir.glob("*.mp3")):
            logging.info(f"MP3 files already exist in {media_dir}, skipping export.")
            one_mp3 = next(media_dir.glob("*.mp3"))
            return one_mp3

    script_path = Path(musescore_export_script)
    if not script_path.exists():
        raise FileNotFoundError(f"Script {script_path} does not exist.")

    subprocess.run(["osascript", str(script_path)], check=True)
    time.sleep(5)
    all_mp3 = wait_for_all_mp3(export_dir=MUSESCORE_EXPORT_PATH, timeout=120, check_interval=1)
    logging.info("MP3 export from MuseScore completed.")

    if song_dir:
        # Move exported MP3 files to song folder/mp3
        mp3_basename = all_mp3.stem.replace(" ALL", "")
        mp3_files = get_filtered_mp3_files(mp3_basename)
        target_dir = Path(song_dir) / "media"
        target_dir.mkdir(parents=True, exist_ok=True)
        for mp3 in mp3_files:
            target_path = target_dir / mp3.name
            logging.info(f"Moving {mp3} to {target_path}")
            mp3.rename(target_path)
            all_mp3 = target_path
        logging.info(f"All MP3 files moved to {target_dir}")
    
    return all_mp3


def run(song_dir=None, youtube=False):
    if not song_dir or not os.path.exists(song_dir):
        raise ValueError("A valid song_dir must be provided.")

    if youtube:
        get_authenticated_service()

    mp3 = export_mp3_from_musescore(song_dir)
    logging.info("MP3 files exported from MuseScore.")

    record_video(song_dir, mp3)

    results = merge_mp3_to_video(song_dir)
    logging.info("MP3 files merged to video: " + ", ".join(str(r) for r in results))

    if youtube:
        upload_to_youtube(song_dir, results)
        logging.info("Videos uploaded to YouTube.")
