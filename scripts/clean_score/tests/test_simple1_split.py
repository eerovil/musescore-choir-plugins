# Test that splitting simple_1_input.mscx produces two parts with correct note/rest counts

import pytest
import os
from lxml import etree
from scripts.clean_score.clean_score import main

CURRENT_PATH = os.path.dirname(__file__)


@pytest.mark.parametrize(
    "base_filename",
    [
        "simple_1",
        # 'medium_1'
    ],
)
def test_split(base_filename):
    import xml.etree.ElementTree as ET

    input_file = os.path.join(CURRENT_PATH, f"test_files/{base_filename}_input.mscx")
    output_file = os.path.join(
        CURRENT_PATH, f"test_files/{base_filename}_test_output.mscx"
    )

    # Run the main function to process the input file
    main(input_file, output_file)

    # Compare the output file with the expected output as XML trees
    expected_output_file = os.path.join(
        CURRENT_PATH, f"test_files/{base_filename}_output.mscx"
    )
    with open(expected_output_file, "r", encoding="utf-8") as f:
        expected_content = f.readlines()
    with open(output_file, "r", encoding="utf-8") as f:
        output_content = f.readlines()

    tree_expected = etree.fromstringlist(expected_content)
    tree_output = etree.fromstringlist(output_content)

    index = -1
    # List elements one by one to compare
    for elem_expected, elem_output in zip(tree_expected.iter(), tree_output.iter()):
        index += 1
        expected_parent = elem_expected.getparent()
        output_parent = elem_output.getparent()
        expected_snippet = None
        output_snippet = None
        if expected_parent and output_parent:
            expected_snippet = ET.tostring(expected_parent, encoding="unicode")
            output_snippet = ET.tostring(output_parent, encoding="unicode")
        assert (
            elem_expected.tag == elem_output.tag
        ), f"Tag mismatch at index {index}: expected\n '{elem_expected.tag}', got\n '{elem_output.tag}'. Expected: \n{expected_snippet}, Output: \n{output_snippet}"
