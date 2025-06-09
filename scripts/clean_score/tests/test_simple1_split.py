# Test that splitting simple_1_input.mscx produces two parts with correct note/rest counts

import os
from scripts.clean_score.clean_score import main

CURRENT_PATH = os.path.dirname(__file__)


def test_simple1_split():
    import xml.etree.ElementTree as ET
    input_file = os.path.join(CURRENT_PATH, 'test_files/simple_1_input.mscx')
    output_file = os.path.join(CURRENT_PATH, 'test_files/simple_1_test_output.mscx')

    # Run the main function to process the input file
    main(input_file=input_file, output_file=output_file)

    # Compare the output file with the expected output as XML trees
    expected_output_file = os.path.join(CURRENT_PATH, 'test_files/simple_1_output.mscx')
    with open(expected_output_file, 'r', encoding='utf-8') as f:
        expected_content = f.read()
    with open(output_file, 'r', encoding='utf-8') as f:
        output_content = f.read()

    tree_expected = ET.ElementTree(ET.fromstring(expected_content))
    tree_output = ET.ElementTree(ET.fromstring(output_content))

    # Print child tags of <museScore> in both expected and output files for debugging
    expected_root = tree_expected.getroot()
    output_root = tree_output.getroot()
    print('Expected museScore children:')
    for child in expected_root:
        print(child.tag)
    print('Output museScore children:')
    for child in output_root:
        print(child.tag)

    # Print child tags of <Score> in both expected and output files for debugging
    expected_score = expected_root.find('Score')
    output_score = output_root.find('Score')
    print('Expected Score children:')
    for child in expected_score:
        print(child.tag)
    print('Output Score children:')
    for child in output_score:
        print(child.tag)

    def xml_equal(a, b, debug=False):
        def elements_equal(e1, e2, path="/"):
            if e1.tag != e2.tag:
                if debug:
                    print(f"Tag mismatch at {path}: {e1.tag} != {e2.tag}")
                return False
            if (e1.text or '').strip() != (e2.text or '').strip():
                if debug:
                    print(f"Text mismatch at {path}: '{e1.text}' != '{e2.text}'")
                return False
            if (e1.tail or '').strip() != (e2.tail or '').strip():
                if debug:
                    print(f"Tail mismatch at {path}: '{e1.tail}' != '{e2.tail}'")
                return False
            if e1.attrib != e2.attrib:
                if debug:
                    print(f"Attrib mismatch at {path}: {e1.attrib} != {e2.attrib}")
                return False
            if len(e1) != len(e2):
                if debug:
                    print(f"Children count mismatch at {path}: {len(e1)} != {len(e2)}")
                return False
            for i, (c1, c2) in enumerate(zip(e1, e2)):
                if not elements_equal(c1, c2, path + f"{e1.tag}[{i}]/"):
                    return False
            return True
        return elements_equal(a.getroot(), b.getroot())

    if not xml_equal(tree_expected, tree_output, debug=True):
        assert False, "Output XML structure does not match expected content. See printed differences above."
