"""The reviewed OMR manifest is the authority for reference inputs."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from lxml import etree

from scripts import implode_report, reference_manifest, scan_vs_reference


def digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def reviewed_song(tmp_path, monkeypatch, cleaned: bytes = b"score"):
    songs = tmp_path / "songs"
    song = songs / "fixture"
    song.mkdir(parents=True)
    pdf = song / "reviewed.pdf"
    score = song / "reviewed_cleaned.mscx"
    pdf.write_bytes(b"page")
    score.write_bytes(cleaned)
    manifest = tmp_path / "omr-songs.json"
    manifest.write_text(json.dumps({"songs": {"fixture": {
        "pdf": pdf.name,
        "cleaned": score.name,
        "pdf_sha256": digest(pdf),
        "cleaned_sha256": digest(score),
        "video": {"videos": 1, "uploads": 0, "stage": "record"},
        "review": {"status": "ok", "notes": "checked"},
    }}}))
    monkeypatch.setattr(reference_manifest, "MANIFEST", manifest)
    monkeypatch.setattr(reference_manifest, "SONGS", songs)
    return pdf, score


def test_the_manifest_chooses_the_reviewed_files_not_an_alphabetical_match(
    tmp_path, monkeypatch
) -> None:
    pdf, score = reviewed_song(tmp_path, monkeypatch)
    (score.parent / "000_cleaned.mscx").write_bytes(b"different score")

    found = reference_manifest.reference_files("fixture")

    assert found.pdf == pdf
    assert found.cleaned == score


def test_the_reference_report_uses_the_manifest_instead_of_rediscovering_a_score(
    tmp_path, monkeypatch
) -> None:
    pdf, score = reviewed_song(tmp_path, monkeypatch)
    (score.parent / "000_cleaned.mscx").write_bytes(b"different score")

    assert implode_report.songs() == [(
        "fixture",
        pdf,
        score,
        {"videos": 1, "uploads": 0, "stage": "record"},
    )]


@pytest.mark.parametrize("kind", ["pdf", "cleaned"])
def test_a_reviewed_source_that_changed_is_refused(tmp_path, monkeypatch, kind) -> None:
    pdf, score = reviewed_song(tmp_path, monkeypatch)
    {"pdf": pdf, "cleaned": score}[kind].write_bytes(b"changed after review")

    with pytest.raises(ValueError, match=rf"reviewed {kind} changed.*review it again"):
        reference_manifest.reference_files("fixture")


def test_a_hidden_resting_staff_does_not_add_a_reference_voice(
    tmp_path, monkeypatch
) -> None:
    root = etree.Element("museScore")
    score = etree.SubElement(root, "Score")
    for staff_id, name, event in ((1, "S1", "Chord"), (2, "B1", "Rest")):
        part = etree.SubElement(score, "Part")
        etree.SubElement(part, "Staff", id=str(staff_id))
        etree.SubElement(part, "trackName").text = name
        staff = etree.SubElement(score, "Staff", id=str(staff_id))
        voice = etree.SubElement(etree.SubElement(staff, "Measure"), "voice")
        event_element = etree.SubElement(voice, event)
        if event == "Chord":
            etree.SubElement(event_element, "Note")
    xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
    reviewed_song(tmp_path, monkeypatch, cleaned=xml)
    band = SimpleNamespace(measure_start=1, measure_end=1)

    assert scan_vs_reference.reference_systems("fixture", [band]) == [{
        "staves": 1,
        "bars": 1,
        "notes": 1,
        "voices": 1,
    }]
