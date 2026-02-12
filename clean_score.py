#!/usr/bin/env python3

HELP_TEXT = """
How to use

* Get a musescore file or musicxml file.

* Drag that on this script or run from command line
    ./clean_score.py "path/to/your/file.mscz"

* Also pass the original PDF file if you want to fix the lyrics using Gemini API
    For gemini api, set .env variable GEMINI_API_KEY to your API key

    e.g.
    ./clean_score.py "Sortunut ääni.pdf" "Sortunut-a-a-ni-pdf.xml" 

* Output file will be saved to songs/

"""
import argparse
from shutil import SameFileError
import dotenv

parser = argparse.ArgumentParser(
    description="Convert MuseScore/MusicXML from single-staff, two-voice to two-staff, single-voice-per-staff." + "\n" + HELP_TEXT
)
# Allow passing multiple files
parser.add_argument("input_files", nargs="+", help="Input MuseScore or MusicXML file, and possibly original PDF")
parser.add_argument("--name", help="Optional name for the (new) song directory")
# Add option to force add new staffs, give param e.g. SSAA
parser.add_argument("--add", help="Force add new staffs, give param e.g. SSAA")
args = parser.parse_args()

add_staffs = (args.add or "").upper().strip()

import os
import sys
# Find a possible musescore file from the arguments
musescore_file = None
pdf_file = None
for f in args.input_files:
    if f.lower().endswith((".mscz", ".mscx", ".musicxml", ".xml")):
        musescore_file = f
    elif f.lower().endswith(".pdf"):
        pdf_file = f

song_dir = None

# input may be also a directory, in which case find the first musescore file
if not musescore_file and len(args.input_files) == 1 and os.path.isdir(args.input_files[0]):
    song_dir = args.input_files[0]
    for entry in os.listdir(song_dir):
        if entry.lower().endswith((".mscz", ".mscx", ".musicxml", ".xml")):
            if '_cleaned' in entry.lower():
                continue  # skip already split files
            musescore_file = os.path.join(song_dir, entry)
        elif entry.lower().endswith(".pdf"):
            pdf_file = os.path.join(song_dir, entry)
            # continue searching for musescore file

if not musescore_file:
    print("No MuseScore or MusicXML file provided.")
    sys.exit(1)
if not os.path.exists(musescore_file):
    print(f"MuseScore file {musescore_file} does not exist.")
    sys.exit(1)
if pdf_file and not os.path.exists(pdf_file):
    print(f"PDF file {pdf_file} does not exist.")
    sys.exit(1)

# Create output directory if it doesn't exist
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
songs_dir = os.path.join(SCRIPT_DIR, "songs")
if not os.path.exists(songs_dir):
    os.makedirs(songs_dir)

if not song_dir:
    # Get or create a directory for this song
    # Using the base name of the musescore file
    base_name = os.path.splitext(os.path.basename(musescore_file))[0]
    song_dir = os.path.join(songs_dir, base_name)
    if args.name:
        song_dir = os.path.join(songs_dir, args.name)
    if not os.path.exists(song_dir):
        os.makedirs(song_dir)

# Copy original musescore file and possibly pdf file to the song directory
try:
    import shutil
    shutil.copy2(musescore_file, song_dir)
    if pdf_file:
        shutil.copy2(pdf_file, song_dir)
except SameFileError:
    pass  # ignore if source and destination are the same

input_file = os.path.join(song_dir, os.path.basename(musescore_file))
if pdf_file:
    pdf_file = os.path.join(song_dir, os.path.basename(pdf_file))

output_file = input_file.replace(".mscx", "_cleaned.mscx").replace(".mscz", "_cleaned.mscx").replace(".musicxml", "_cleaned.mscx").replace(".xml", "_cleaned.mscx")

# Load environment variables from .env or .env.default file
dotenv_path = os.path.join(SCRIPT_DIR, ".env")
if not os.path.exists(dotenv_path):
    dotenv_path = os.path.join(SCRIPT_DIR, ".env.default")
dotenv.load_dotenv(dotenv_path)

# Convert any input file to .mscx
if not input_file.lower().endswith(".mscx"):
    print(f"Converting {input_file} to .mscx format")
    if input_file.lower().endswith(".mscz"):
        # unzip the mscz file
        import zipfile
        os.mkdir(song_dir + "/temp_extracted")
        with zipfile.ZipFile(input_file, 'r') as zip_ref:
            zip_ref.extractall(song_dir + "/temp_extracted")
        # Find the .mscx file
        mscx_file = None
        for entry in os.listdir(song_dir + "/temp_extracted"):
            if entry.lower().endswith(".mscx"):
                mscx_file = os.path.join(song_dir + "/temp_extracted", entry)
                break
        else:
            print("Failed to extract .mscx from .mscz file")
            sys.exit(1)
    elif input_file.lower().endswith((".musicxml", ".xml")):
        # Convert musicxml to mscx using musescore command line
        import subprocess
        mscx_file = input_file.rsplit(".", 1)[0] + ".mscx"
        musescore_cmd = os.getenv("MUSESCORE_CLI_PATH", "musescore3")
        result = subprocess.run([musescore_cmd, input_file, "-o", mscx_file], capture_output=True, text=True)
        if result.returncode != 0:
            print("Failed to convert musicxml to mscx")
            print("Command output:", result.stdout)
            print("Command error:", result.stderr)
            sys.exit(1)

    if not mscx_file:
        print("Failed to convert to .mscx format")
        sys.exit(1)
    # Move the converted file to the song directory
    new_input_file = os.path.join(song_dir, os.path.basename(mscx_file))
    shutil.move(mscx_file, new_input_file)
    input_file = new_input_file
    print(f"Converted to {input_file}")

    # If temp_extracted directory exists, remove it
    if os.path.exists(song_dir + "/temp_extracted"):
        shutil.rmtree(song_dir + "/temp_extracted")

# Run the cleaning script
from src.clean_score.main import main
main(input_file, output_file, pdf_file, add_staffs=add_staffs)
