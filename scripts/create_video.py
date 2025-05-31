#!.venv/bin/python3

import os
import time
import subprocess
import obsws_python as obs
import logging


logging.basicConfig(level=logging.DEBUG)



# load .env to find MUSESCORE_EXPORT_PATH
from dotenv import load_dotenv
load_dotenv()
# Ensure the environment variable is set
if 'MUSESCORE_EXPORT_PATH' not in os.environ:
    raise EnvironmentError("MUSESCORE_EXPORT_PATH environment variable is not set.")
if 'OBS_EXPORT_PATH' not in os.environ:
    raise EnvironmentError("OBS_EXPORT_PATH environment variable is not set.")
# Find latest mp3 file in the export directory
import os
from pathlib import Path
export_path = Path(os.environ['MUSESCORE_EXPORT_PATH'])
if not export_path.exists():
    raise FileNotFoundError(f"Export path {export_path} does not exist.")
mp3_files = list(export_path.glob("*.mp3"))
if not mp3_files:
    raise FileNotFoundError("No MP3 files found in the export directory.")
mp3_file = max(mp3_files, key=os.path.getmtime)
if not mp3_file.is_file():
    raise FileNotFoundError(f"Latest MP3 file {mp3_file} is not a valid file.")

# Find latest mov file in the export directory
mov_files = list(Path(os.environ['OBS_EXPORT_PATH']).glob("*.mov"))
if not mov_files:
    raise FileNotFoundError("No MOV files found in the OBS export directory.")
mov_file = max(mov_files, key=os.path.getmtime)
if not mov_file.is_file():
    raise FileNotFoundError(f"Latest MOV file {mov_file} is not a valid file.")

print(f"Using MP3 file: {mp3_file}")

# use ffmpeg to get the duration of the mp3 file
def get_mp3_duration(mp3_path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(mp3_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe error: {result.stderr.decode().strip()}")
    return float(result.stdout.decode().strip())
mp3_duration = get_mp3_duration(mp3_file)

# === CONFIG ===
host, port, password = "localhost", 4455, ""
musescore_show_script = "show_musescore.scpt"
musescore_play_script = "play_musescore.scpt"

def record_video():

    # Launch OBS
    subprocess.run(["open", "-a", "OBS"])

    time.sleep(1)  # Wait for MuseScore to be ready

    ws = obs.ReqClient(host='localhost', port=4455, password='', timeout=3)

    # Initialize MuseScore (show it)
    subprocess.run(["osascript", musescore_show_script])

    time.sleep(1)  # Wait for MuseScore to be ready

    resp = ws.start_record()

    print("OBS recording response:", resp)
    time.sleep(1)

    # === Trigger MuseScore playback ===
    subprocess.run(["osascript", musescore_play_script])
    print("MuseScore playback triggered")

    try:
        time.sleep(mp3_duration + 1)
    except KeyboardInterrupt:
        print("Playback interrupted by user")
    ws.stop_record()
    print("OBS recording stopped")

    ws.disconnect()


def get_mp3_files(mp3_basename):
    # Find each mp3 that starts with the basename
    mp3_files = list(export_path.glob(f"{mp3_basename}*.mp3"))
    if not mp3_files:
        raise FileNotFoundError(f"No MP3 files found with basename {mp3_basename} in the export directory.")

    to_remove = f"{mp3_basename} undefined.mp3"

    # Remove the "undefined" file if it exists
    if to_remove in [f.name for f in mp3_files]:
        mp3_files.remove(export_path / to_remove)
        print(f"Removed {to_remove} from the list of MP3 files.")

    # Sort files by modification time
    mp3_files.sort(key=os.path.getmtime, reverse=True)
    latest_mp3_file = mp3_files[0] if mp3_files else None

    # remove files that were created more than 30 minutes before latest_mp3_file
    
    if latest_mp3_file:
        threshold_time = latest_mp3_file.stat().st_mtime - 30 * 60
        mp3_files = [f for f in mp3_files if f.stat().st_mtime >= threshold_time]
    if not mp3_files:
        raise FileNotFoundError(f"No MP3 files found with basename {mp3_basename} in the export directory after filtering.")

    print("fles:", [f.name for f in mp3_files])
    return mp3_files


def merge_mp3_to_video(mp3_file):
    # Find basename of the mp3 file
    mp3_basename = ' '.join(os.path.basename(mp3_file).split(' ')[:-1])

    print(f"MP3 basename: {mp3_basename}")

    mp3_files = get_mp3_files(mp3_basename)
    if not mp3_files:
        raise FileNotFoundError(f"No MP3 files found with basename {mp3_basename} in the export directory.")

    # Create the output directory if it doesn't exist
    output_dir = os.path.join("output", mp3_basename)
    os.makedirs(output_dir, exist_ok=True)

    for mp3_file in mp3_files:
        print(f"Merging {mp3_file} with video")
        base_filename = mp3_file.stem
        audio_file = mp3_file
        input_video = mov_file
        output_path = os.path.join(output_dir, f"{base_filename}.mov")

        # Construct the ffmpeg command
        cmd = [
            "ffmpeg",
            "-i", f"{input_video}",
            "-i", str(audio_file),
            "-c:v", "copy",
            "-filter_complex", f"[1:a]adelay=1000|1000[a]",
            "-map", "0:v:0",
            "-map", "[a]",
            "-map", "1:a:0",
            "-y",  # Overwrite output file if it exists
            output_path
        ]

        print(f"Running command: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)


record_video()
merge_mp3_to_video(mp3_file)
