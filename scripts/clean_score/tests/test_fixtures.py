# Test cases for the clean_score.py script


from glob import glob
import io
from pathlib import Path
import pytest  # noqa
import tempfile
import xml.etree.ElementTree as ET

from ..clean_score import main

CURRENT_DIR = Path(__file__).resolve().parent

def test_fixtures():
    return
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
        score = converter.parse(input_file)

        # Side effect
        split_score = main(score)

        # Write to in-memory file
        with tempfile.NamedTemporaryFile(suffix='.musicxml', delete=False) as tmpfile:
            tmpfile_path = tmpfile.name
        split_score.write('musicxml', fp=tmpfile_path)
        # Read the generated output from the temp file
        with open(tmpfile_path, "rb") as f:
            actual_output = f.read()
        # Read the expected output file
        with open(output_file, "rb") as f:
            expected_output = f.read()
        # Compare the output semantically (ignore quote/case differences)
        actual_xml = ET.fromstring(actual_output)
        expected_xml = ET.fromstring(expected_output)
        def elements_equal(e1, e2):
            if e1.tag != e2.tag or (e1.text or '').strip() != (e2.text or '').strip() or (e1.tail or '').strip() != (e2.tail or '').strip() or e1.attrib != e2.attrib:
                return False
            if len(e1) != len(e2):
                return False
            return all(elements_equal(c1, c2) for c1, c2 in zip(e1, e2))
        assert elements_equal(actual_xml, expected_xml), f"Output XML does not match expected for {folder}"
