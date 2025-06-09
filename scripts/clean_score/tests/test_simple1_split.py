# Test that splitting simple_1_input.mscx produces two parts with correct note/rest counts
import os
import shutil
import tempfile
from lxml import etree
from scripts.clean_score.clean_score import main
import pytest

class BaseScoreTest:
    folder = None  # e.g. 'simple_1'
    input_file = 'INPUT.mscx'  # will be replaced in setup
    output_file = 'OUTPUT.mscx'  # will be replaced in setup

    @pytest.fixture(autouse=True)
    def setup_and_teardown(self, request):
        # Setup temp dir for output
        self.test_dir = tempfile.mkdtemp()
        self.input_path = os.path.join(os.path.dirname(__file__), self.folder, f'{self.folder}_input.mscx')
        self.output_path = os.path.join(self.test_dir, f'{self.folder}_output.mscx')
        yield
        shutil.rmtree(self.test_dir)

    def run_and_save(self):
        tree = etree.parse(self.input_path)
        root = tree.getroot()
        main(root)
        tree.write(self.output_path, pretty_print=True, xml_declaration=True, encoding="UTF-8")
        return self.output_path

    def get_output_tree(self):
        return etree.parse(self.output_path)

class TestSimple1(BaseScoreTest):
    folder = 'simple_1'

    def test_simple1_split(self):
        self.run_and_save()
        tree = self.get_output_tree()
        root = tree.getroot()
        score = root.find('Score')
        parts = score.findall('Part')
        assert len(parts) == 2, f"Expected 2 parts, got {len(parts)}"
        for part in parts:
            notes = [n for n in part.findall('.//Note')]
            rests = [r for r in part.findall('.//Rest')]
            assert len(notes) == 6, f"Part {part.attrib.get('id')} should have 6 notes, got {len(notes)}"
            assert len(rests) == 2, f"Part {part.attrib.get('id')} should have 2 rests, got {len(rests)}"
            measures = part.findall('.//Measure')
            assert len(measures) == 2, f"Part {part.attrib.get('id')} should have 2 measures, got {len(measures)}"
            m1 = measures[0]
            m2 = measures[1]
            m1_types = ['Rest' if el.tag == 'Rest' else 'Note' for el in m1.iterchildren() if el.tag in ('Note', 'Rest')]
            m2_types = ['Rest' if el.tag == 'Rest' else 'Note' for el in m2.iterchildren() if el.tag in ('Note', 'Rest')]
            print(f"DEBUG {part.attrib.get('id')} M1: {m1_types}")
            print(f"DEBUG {part.attrib.get('id')} M2: {m2_types}")
            assert m1_types == ['Note', 'Rest', 'Note', 'Note'], f"Part {part.attrib.get('id')} M1 order: {m1_types}"
            assert m2_types == ['Rest', 'Note', 'Note', 'Note'], f"Part {part.attrib.get('id')} M2 order: {m2_types}"
