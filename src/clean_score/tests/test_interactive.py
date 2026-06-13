"""Tests for interactive voice-anomaly resolution (measures with >2 voices)."""

import sys
import types

from lxml import etree

from src.clean_score.utils import interactive
from src.clean_score.utils.interactive import (
    _find_anomalies,
    resolve_voice_anomalies,
)


def _make_score(voice_counts):
    """Build a minimal Score with one staff whose measures have the given voice counts."""
    score = etree.Element("Score")
    part = etree.SubElement(score, "Part")
    etree.SubElement(part, "Staff", id="1")
    etree.SubElement(part, "trackName").text = "Track 1"
    staff = etree.SubElement(score, "Staff", id="1")
    for n in voice_counts:
        measure = etree.SubElement(staff, "Measure")
        for _ in range(n):
            voice = etree.SubElement(measure, "voice")
            chord = etree.SubElement(voice, "Chord")
            etree.SubElement(chord, "Note")
    root = etree.Element("museScore")
    root.append(score)
    return root


def _voice_counts(root):
    staff = root.find(".//Score/Staff")
    return [len(m.findall("voice")) for m in staff.findall("Measure")]


def test_find_anomalies_flags_more_than_two_voices():
    root = _make_score([2, 2, 4, 1, 2])
    staff = root.find(".//Score/Staff")
    assert _find_anomalies(staff) == [2]  # 0-based index of the 4-voice measure


def test_two_voice_divisi_is_not_an_anomaly():
    root = _make_score([1, 1, 2, 2])  # legitimate divisi; splitter handles it
    staff = root.find(".//Score/Staff")
    assert _find_anomalies(staff) == []


def test_non_interactive_reduces_to_modal():
    root = _make_score([2, 2, 4, 2])
    resolve_voice_anomalies(root, interactive=False)
    assert _voice_counts(root) == [2, 2, 2, 2]


def test_interactive_keep_first(monkeypatch):
    root = _make_score([2, 2, 4, 2])
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(interactive, "input", lambda *a: "1", raising=False)
    resolve_voice_anomalies(root, interactive=True)
    assert _voice_counts(root) == [2, 2, 2, 2]


def test_interactive_keep_all(monkeypatch):
    root = _make_score([2, 2, 4, 2])
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(interactive, "input", lambda *a: "2", raising=False)
    resolve_voice_anomalies(root, interactive=True)
    assert _voice_counts(root) == [2, 2, 4, 2]  # unchanged


def test_interactive_custom_pick(monkeypatch):
    root = _make_score([2, 2, 4, 2])
    # First prompt returns "3" (pick), second returns the voice numbers to keep.
    answers = iter(["3", "1 3 4"])
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(interactive, "input", lambda *a: next(answers), raising=False)
    resolve_voice_anomalies(root, interactive=True)
    assert _voice_counts(root) == [2, 2, 3, 2]  # kept 3 of the 4 voices


def test_interactive_falls_back_when_not_a_tty(monkeypatch):
    """interactive=True but stdin is not a TTY -> behave like non-interactive (no prompt)."""
    root = _make_score([2, 4])
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: False))

    def _boom(*a):
        raise AssertionError("should not prompt when stdin is not a TTY")

    monkeypatch.setattr(interactive, "input", _boom, raising=False)
    resolve_voice_anomalies(root, interactive=True)
    assert _voice_counts(root) == [2, 2]
