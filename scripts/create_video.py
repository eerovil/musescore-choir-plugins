#!/usr/bin/env python3

import argparse
import os
import time
import subprocess
from pathlib import Path
from dotenv import load_dotenv
import obsws_python as obs  # This is how you originally used it — and it works
import logging

# === CONFIG ===
logging.basicConfig(level=logging.INFO)
load_dotenv()

MUSESCORE_EXPORT_PATH = os.getenv("MUSESCORE_EXPORT_PATH")
OBS_EXPORT_PATH = os.getenv("OBS_EXPORT_PATH")

if not MUSESCORE_EXPORT_PATH or not OBS_EXPORT_PATH:
    raise EnvironmentError("Both MUSESCORE_EXPORT_PATH and OBS_EXPORT_PATH must be set in the environment.")

export_path = Path(MUSESCORE_EXPORT_PATH)
obs_path = Path(OBS_EXPORT_PATH)

musescore_show_script = "show_musescore.scpt"
musescore_play_script = "play_musescore.scpt"


def get_mp3_duration(mp3_path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(mp3_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe error: {result.stderr.decode().strip()}")
    return float(result.stdout.decode().strip())


def get_latest_file(path: Path, pattern: str):
    files = list(path.glob(pattern))
    if not files:
        raise FileNotFoundError(f"No files matching {pattern} in {path}")
    return max(files, key=os.path.getmtime)


def get_filtered_mp3_files(mp3_basename):
    mp3_files = list(export_path.glob(f"{mp3_basename}*.mp3"))
    to_remove = f"{mp3_basename} undefined.mp3"
    mp3_files = [f for f in mp3_files if f.name != to_remove]

    if not mp3_files:
        raise FileNotFoundError(f"No MP3s with base name '{mp3_basename}' found.")

    latest_time = max(f.stat().st_mtime for f in mp3_files)
    threshold = latest_time - 30 * 60
    filtered = [f for f in mp3_files if f.stat().st_mtime >= threshold]

    logging.info(f"Filtered MP3s: {[f.name for f in filtered]}")
    return filtered


def record_video(mp3_file):
    duration = get_mp3_duration(mp3_file)
    subprocess.run(["open", "-a", "OBS"])
    time.sleep(1)

    ws = obs.ReqClient(host='localhost', port=4455, password='', timeout=3)
    subprocess.run(["osascript", musescore_show_script])
    time.sleep(1)

    ws.start_record()
    logging.info("Recording started.")
    time.sleep(1)
    subprocess.run(["osascript", musescore_play_script])
    logging.info("Playback started.")
    try:
        time.sleep(duration + 1)
    except KeyboardInterrupt:
        logging.info("Recording interrupted by user.")
        ws.stop_record()
        logging.info("Recording stopped.")
        ws.disconnect()
        raise

    ws.stop_record()
    logging.info("Recording stopped.")
    ws.disconnect()


def merge_mp3_to_video(mp3_basename):
    mp3_files = get_filtered_mp3_files(mp3_basename)
    mov_file = get_latest_file(obs_path, "*.mov")
    output_dir = Path("output") / mp3_basename
    output_dir.mkdir(parents=True, exist_ok=True)

    for mp3 in mp3_files:
        output_path = output_dir / f"{mp3.stem}.mov"
        cmd = [
            "ffmpeg",
            "-i", str(mov_file),
            "-i", str(mp3),
            "-c:v", "copy",
            "-filter_complex", "[1:a]adelay=1000|1000[a]",
            "-map", "0:v:0",
            "-map", "[a]",
            "-map", "1:a:0",
            "-y",
            str(output_path)
        ]
        logging.info(f"Merging {mp3.name} → {output_path.name}")
        subprocess.run(cmd, check=True)


def main():
    parser = argparse.ArgumentParser(description="Generate MuseScore practice videos.")
    parser.add_argument("--basename", default='', help="Filter MP3 files by base name (e.g. song name)")
    parser.add_argument("--no-record", action="store_true", help="Skip recording, only merge audio to video")
    args = parser.parse_args()

    if not args.no_record:
        mp3 = get_latest_file(export_path, f"{args.basename}*.mp3")
        record_video(mp3)

    merge_mp3_to_video(args.basename)


if __name__ == "__main__":
    main()
