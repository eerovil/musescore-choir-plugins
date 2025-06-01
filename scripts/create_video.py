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

from upload_to_youtube import get_authenticated_service, upload_to_youtube

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
    files = glob_unicode(path, pattern)
    if not files:
        raise FileNotFoundError(f"No files matching {pattern} in {path}")
    # if path is mov, ignore files less than 5MB
    if pattern.endswith(".mov"):
        logging.info(f"Filtering MOV files larger than 5MB in {path}")
        files = [f for f in files if f.stat().st_size >= 5 * 1024 * 1024]  # 5MB
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
    results = []

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
            str(output_path),
        ]
        logging.info(f"Merging {mp3.name} → {output_path.name}")
        subprocess.run(cmd, check=True, capture_output=True)
        results.append(output_path)
    
    logging.info(f"All videos merged: {', '.join(str(r) for r in results)}")
    return results


def main():
    parser = argparse.ArgumentParser(description="Generate MuseScore practice videos.")
    parser.add_argument("--basename", default='', help="Filter MP3 files by base name (e.g. song name)")
    parser.add_argument("--no-record", action="store_true", help="Skip recording, only merge audio to video")
    parser.add_argument("--no-merge", action="store_true", help="Skip merge audio to video")
    parser.add_argument("--youtube", action="store_true", help="Upload to YouTube after merging")
    args = parser.parse_args()
    basename = args.basename.strip()
    mp3 = get_latest_file(export_path, f"{basename}*.mp3")
    basename = ' '.join(mp3.stem.split(' ')[:-1])
    print("Basename:", basename)

    if args.youtube:
        get_authenticated_service()

    if not args.no_record:
        record_video(mp3)

    if not args.no_merge:
        results = merge_mp3_to_video(basename)
        logging.info(f"Videos created: {', '.join(str(r) for r in results)}")
    else:
        # basename must be provided if merging is skipped
        if not basename:
            raise ValueError("Base name must be provided when skipping recording.")
        results = []
        # Find output/basename/*.mov files
        output_dir = Path("output") / basename
        if output_dir.exists():
            results = list(output_dir.glob("*.mov"))
            if not results:
                raise FileNotFoundError(f"No video files found in {output_dir}")
        else:
            raise FileNotFoundError(f"Output directory {output_dir} does not exist.")

    if args.youtube:
        upload_to_youtube(results, basename=basename)
        logging.info("Videos uploaded to YouTube.")


if __name__ == "__main__":
    main()
