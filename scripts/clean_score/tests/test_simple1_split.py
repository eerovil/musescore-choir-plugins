# Test that splitting simple_1_input.mscx produces two parts with correct note/rest counts

import os
from scripts.clean_score.clean_score import main

CURRENT_PATH = os.path.dirname(__file__)


def test_simple1_split():
    input_file = os.path.join(CURRENT_PATH, 'test_files/simple_1_input.mscx')
    output_file = os.path.join(CURRENT_PATH, 'test_files/simple_1_test_output.mscx')

    # Run the main function to process the input file
    main(input_file=input_file, output_file=output_file)

    # Compare the output file with the expected output
    expected_output_file = os.path.join(CURRENT_PATH, 'test_files/simple_1_output.mscx')
    with open(expected_output_file, 'r', encoding='utf-8') as f:
        expected_content = f.read()
    with open(output_file, 'r', encoding='utf-8') as f:
        output_content = f.read()
    assert output_content == expected_content, "Output does not match expected content."
