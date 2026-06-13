"""Tests for interactive re-voicing: baseline naming, capture, and routing."""

from lxml import etree

from src.clean_score.utils import revoice
from src.clean_score.utils.revoice import (
    apply_revoice_plan,
    capture_revoice_plan,
    establish_baseline,
)


def _voice(pitch):
    v = etree.Element("voice")
    ch = etree.SubElement(v, "Chord")
    etree.SubElement(ch, "durationType").text = "quarter"
    n = etree.SubElement(ch, "Note")
    etree.SubElement(n, "pitch").text = str(pitch)
    return v


def _measure(pitches):
    m = etree.Element("Measure")
    for p in pitches:
        m.append(_voice(p))
    return m


def _part(score, sid, name):
    part = etree.SubElement(score, "Part")
    etree.SubElement(part, "trackName").text = name
    etree.SubElement(part, "Staff", id=str(sid))
    instr = etree.SubElement(part, "Instrument")
    for tag in ("longName", "shortName", "trackName"):
        etree.SubElement(instr, tag).text = name


def _build():
    """Score: staff 1 = 2 voices, with m2 anomalous (4 voices); staff 2 = bass (1 voice)."""
    root = etree.Element("museScore")
    score = etree.SubElement(root, "Score")
    _part(score, 1, "Track 1")
    _part(score, 2, "B")
    s1 = etree.SubElement(score, "Staff", id="1")
    s1.append(_measure([64, 54]))            # m1: 2 voices
    s1.append(_measure([64, 54, 59, 50]))    # m2: 4 voices (anomaly)
    s2 = etree.SubElement(score, "Staff", id="2")
    s2.append(_measure([47]))
    s2.append(_measure([47]))
    return root


def test_establish_baseline(monkeypatch):
    root = _build()
    monkeypatch.setattr(revoice, "input", lambda *a: "T1,T2,B", raising=False)
    baseline = establish_baseline(root)
    assert baseline["name_to_staff"] == {"T1": 1, "T2": 1, "B": 2}
    assert baseline["staff_to_names"] == {1: ["T1", "T2"], 2: ["B"]}


def test_establish_baseline_reprompts_on_wrong_count(monkeypatch):
    root = _build()
    answers = iter(["T1,T2", "T1,T2,B"])  # first has too few names
    monkeypatch.setattr(revoice, "input", lambda *a: next(answers), raising=False)
    baseline = establish_baseline(root)
    assert baseline["name_to_staff"] == {"T1": 1, "T2": 1, "B": 2}


def test_capture_plan_new_and_move(monkeypatch):
    root = _build()
    baseline = {"name_to_staff": {"T1": 1, "T2": 1, "B": 2},
                "staff_to_names": {1: ["T1", "T2"], 2: ["B"]}}
    # m2 voices are [64,54,59,50]; label them T3 (new), T1, T2 (kept), B (move to bass).
    monkeypatch.setattr(revoice, "input", lambda *a: "T3,T1,T2,B", raising=False)
    plan = capture_revoice_plan(root, baseline)
    kinds = {(e["kind"], e["label"]) for e in plan}
    assert ("new", "T3") in kinds
    assert ("move", "B") in kinds
    # The anomalous measure is reduced to the two kept voices, in baseline order (T1=54, T2=59).
    s1_m2 = root.findall(".//Score/Staff")[0].findall("Measure")[1]
    pitches = [v.find(".//pitch").text for v in s1_m2.findall("voice")]
    assert pitches == ["54", "59"]


def test_capture_plan_blank_drops(monkeypatch):
    root = _build()
    baseline = {"name_to_staff": {"T1": 1, "T2": 1, "B": 2},
                "staff_to_names": {1: ["T1", "T2"], 2: ["B"]}}
    # Keep T1,T2; drop the other two (blank).
    monkeypatch.setattr(revoice, "input", lambda *a: "T1,T2,,", raising=False)
    plan = capture_revoice_plan(root, baseline)
    assert plan == []  # nothing captured; the extras were dropped
    s1_m2 = root.findall(".//Score/Staff")[0].findall("Measure")[1]
    assert len(s1_m2.findall("voice")) == 2


def test_apply_plan_new_staff_and_move():
    root = _build()
    baseline = {"name_to_staff": {"T1": 1, "T2": 1, "B": 2},
                "staff_to_names": {1: ["T1", "T2"], 2: ["B"]}}
    printed_to_output = {1: [1], 2: [2]}  # simple 1:1 output mapping for the test
    plan = [
        {"kind": "new", "label": "T3", "measure_index": 1, "voice": _voice(64)},
        {"kind": "move", "label": "B", "measure_index": 1, "voice": _voice(50)},
    ]
    apply_revoice_plan(root, plan, baseline, printed_to_output)
    score = root.find(".//Score")
    staff_ids = [s.get("id") for s in score.findall("Staff")]
    assert "3" in staff_ids  # a new staff was created
    new_staff = [s for s in score.findall("Staff") if s.get("id") == "3"][0]
    # New staff: rests at m1, the T3 note at m2.
    assert new_staff.findall("Measure")[0].find(".//Chord") is None
    assert new_staff.findall("Measure")[1].find(".//pitch").text == "64"
    # Bass staff (id 2) got the moved voice added at m2.
    bass_m2 = [s for s in score.findall("Staff") if s.get("id") == "2"][0].findall("Measure")[1]
    assert len(bass_m2.findall("voice")) == 2
