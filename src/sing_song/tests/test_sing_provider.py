"""SING_PROVIDER picks what sing_ufr.py sings on (#483): cpu, cuda, or coreml when the
installed onnxruntime has it. sing_ufr.py is a script that sings as it is read, so the
function is lifted out of its source rather than imported."""

import ast
import os
import sys

import pytest

SINGER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sing_ufr.py")


def _providers():
    tree = ast.parse(open(SINGER, encoding="utf-8").read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "providers")
    space = {"os": os, "sys": sys}
    exec(compile(ast.Module([fn], []), SINGER, "exec"), space)
    return space["providers"]


class FakeOrt:
    def __init__(self, *have):
        self.have = list(have) + ["CPUExecutionProvider"]

    def get_available_providers(self):
        return self.have


CPU = ["CPUExecutionProvider"]


@pytest.mark.parametrize("env, want", [
    ({}, CPU),
    ({"GPU": "1"}, ["CUDAExecutionProvider", "CPUExecutionProvider"]),
    ({"SING_PROVIDER": "cuda"}, ["CUDAExecutionProvider", "CPUExecutionProvider"]),
    ({"SING_PROVIDER": "cpu", "GPU": "1"}, CPU),
    ({"SING_PROVIDER": "CoreML"},
     [("CoreMLExecutionProvider", {"MLComputeUnits": "ALL"}), "CPUExecutionProvider"]),
])
def test_the_setting_picks_the_providers(env, want):
    said = []
    assert _providers()(FakeOrt("CoreMLExecutionProvider"), env, said.append) == want
    assert said == []


def test_coreml_without_it_in_onnxruntime_sings_on_the_processor_and_says_so():
    said = []
    assert _providers()(FakeOrt(), {"SING_PROVIDER": "coreml"}, said.append) == CPU
    assert "no CoreML provider" in said[0]


def test_an_unknown_setting_sings_on_the_processor_and_says_so():
    said = []
    assert _providers()(FakeOrt(), {"SING_PROVIDER": "metal"}, said.append) == CPU
    assert "metal" in said[0]


def test_every_session_goes_through_the_one_that_says_what_it_got():
    text = open(SINGER, encoding="utf-8").read()
    assert text.count("ort.InferenceSession(") == 1
    assert "s.get_providers()" in text
