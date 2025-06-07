


# Test cases for the clean_score.py script


from glob import glob
import io
from pathlib import Path
import pytest  # noqa
from lxml import etree

from ..clean_score import main

CURRENT_DIR = Path(__file__).resolve().parent

def test_fixtures():
    # Get all folder names in the current directory
    current_dir = CURRENT_DIR
    folder_names = [f.name for f in current_dir.iterdir() if f.is_dir()]
    for folder in folder_names:
        # Skip folders that start with a dot or __
        if folder.startswith((".", "__")):
            continue
        print(f"Testing folder: {folder}")
        # Find *input.musicxml in each folder
        input_file = glob(f"{current_dir}/{folder}/*input.musicxml")[0]
        output_file = glob(f"{current_dir}/{folder}/*output.musicxml")[0]
        # Read the file
        # Load MusicXML file
        tree = etree.parse(input_file)
        root = tree.getroot()

        # Side effect
        main(root)

        # Write to in-memory file
        test_output_file = io.BytesIO()
        tree.write(test_output_file, pretty_print=True, xml_declaration=True, encoding="UTF-8")
        test_output_file.seek(0)

        # Read the expected output file
        with open(output_file, "rb") as f:
            expected_output = f.read()
        # Compare the output
        assert test_output_file.getvalue() == expected_output, f"Output does not match expected for {folder}"
