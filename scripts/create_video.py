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
    raise EnvironmentError(
        "Both MUSESCORE_EXPORT_PATH and OBS_EXPORT_PATH must be set in the environment."
    )

export_path = Path(MUSESCORE_EXPORT_PATH)
obs_path = Path(OBS_EXPORT_PATH)

musescore_show_script = "show_musescore.scpt"
musescore_play_script = "play_musescore.scpt"
musescore_export_script = "export_musescore.scpt"
start_recording_script = "start_recording.scpt"
stop_recording_script = "stop_recording.scpt"


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


def merge_mp3_to_video(mp3_basename):
    mp3_files = get_filtered_mp3_files(mp3_basename)
    input_video_file = get_latest_file(obs_path, "*.mov")
    output_dir = Path("output") / mp3_basename
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for mp3 in mp3_files:
        output_path = output_dir / f"{mp3.stem}.mov"
        cmd = [
            "ffmpeg",
            "-i",
            str(input_video_file),
            "-i",
            str(mp3),
            "-c:v",
            "copy",
            "-filter_complex",
            "[1:a]adelay=1000|1000[a]",
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


def export_mp3_from_musescore():
    """
    Export MP3 files from MuseScore using AppleScript.
    """
    script_path = Path(musescore_export_script)
    if not script_path.exists():
        raise FileNotFoundError(f"Script {script_path} does not exist.")

    subprocess.run(["osascript", str(script_path)], check=True)
    time.sleep(5)
    wait_for_all_mp3(export_dir=MUSESCORE_EXPORT_PATH, timeout=120, check_interval=1)
    logging.info("MP3 export from MuseScore completed.")


def main():
    """
    Install QuickRecorder.
    Set up QuickRecorder: Add keyboard shortcuts to start record and stop
    Setup musescore 3 to export with keyboard shortcut.
    Open wanted sheet music in musescore, and match --basename with the basename of it

    """
    parser = argparse.ArgumentParser(description="Generate MuseScore practice videos.")
    parser.add_argument(
        "--basename", default="", help="Filter MP3 files by base name (e.g. song name)"
    )
    parser.add_argument(
        "--export-mp3", action="store_true", help="Export MP3 files from MuseScore"
    )
    parser.add_argument("--record", action="store_true", help="Record video using OBS")
    parser.add_argument(
        "--merge", action="store_true", help="Merge MP3 files with video"
    )
    parser.add_argument(
        "--youtube", action="store_true", help="Upload to YouTube after merging"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Export, Record and merge and upload to YouTube",
    )
    args = parser.parse_args()

    if args.full:
        args.export_mp3 = True
        args.record = True
        args.merge = True
        args.youtube = True

    if args.youtube:
        get_authenticated_service()

    if args.export_mp3:
        export_mp3_from_musescore()
        logging.info("MP3 files exported from MuseScore.")

    basename = args.basename.strip()
    mp3 = get_latest_file(export_path, f"{basename}*.mp3")
    basename = " ".join(mp3.stem.split(" ")[:-1])
    print("Basename:", basename)

    if args.record:
        record_video(mp3)

    if args.merge:
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
