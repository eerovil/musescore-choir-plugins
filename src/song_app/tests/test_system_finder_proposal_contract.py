"""The app/homr proposal boundary must not hide failures or approve bounds."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from src.song_app import omr, system_finder
from src.song_app.pdf_systems import SystemBounds


def engine():
    return omr.Engine(key="test", label="test homr", command=["/test/homr"])


def response(monkeypatch, *, stdout="", stderr="", returncode=0):
    monkeypatch.setattr(
        system_finder.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=returncode, stdout=stdout, stderr=stderr
        ),
    )


@pytest.mark.parametrize("diagnostic", [
    "homr: error: unrecognized arguments: --system-page 1",
    "Error: No such option: --system-page",
    "homr: error: unrecognized arguments: --find-system-bounds-extra",
])
def test_supported_option_in_usage_does_not_hide_an_unrelated_error(monkeypatch, diagnostic):
    response(
        monkeypatch,
        returncode=2,
        stderr="usage: homr [--find-system-bounds] input\n" + diagnostic,
    )
    monkeypatch.setattr(system_finder.pdf_systems, "page_count", lambda _pdf: 1)

    def no_fallback(*args, **kwargs):
        pytest.fail("a supported CLI failure must not trigger the compatibility helper")

    monkeypatch.setattr(system_finder.legacy, "find_bands", no_fallback)
    with pytest.raises(omr.HomrError, match="could not propose systems"):
        system_finder.find_bands("score.pdf", engine=engine(), queue=False)


@pytest.mark.parametrize("diagnostic", [
    "homr: error: unrecognized arguments: --find-system-bounds --system-page 1",
    "Error: No such option: --find-system-bounds",
])
def test_explicitly_unsupported_proposal_flag_still_permits_fallback(monkeypatch, diagnostic):
    response(monkeypatch, stderr=diagnostic, returncode=2)
    assert system_finder._page_from_homr(
        "score.pdf", 1, engine=engine(), dpi=200, log=lambda _line: None
    ) is None


@pytest.mark.parametrize("payload", [
    None,
    [],
    {},
    {"systems": {}},
    {"systems": ""},
    {"systems": [None]},
    {"systems": [{"index": 1, "page": 1}]},
])
def test_malformed_json_shape_is_a_proposal_error(monkeypatch, payload):
    response(monkeypatch, stdout=json.dumps(payload))
    with pytest.raises(omr.HomrError, match="Could not read homr's system proposal"):
        system_finder._page_from_homr(
            "score.pdf", 1, engine=engine(), dpi=200, log=lambda _line: None
        )


@pytest.mark.parametrize("change", [
    {"top": float("nan")},
    {"bottom": float("inf")},
    {"top": -0.1},
    {"bottom": 1.1},
    {"top": 0.9},
    {"index": 0},
    {"index": float("inf")},
    {"page": 2},
])
def test_unusable_bounds_are_not_returned_to_the_editor(monkeypatch, change):
    row = {"index": 1, "page": 1, "top": 0.1, "bottom": 0.9, **change}
    response(monkeypatch, stdout=json.dumps({"systems": [row]}))
    with pytest.raises(omr.HomrError):
        system_finder._page_from_homr(
            "score.pdf", 1, engine=engine(), dpi=200, log=lambda _line: None
        )


def test_each_page_has_its_own_lease_and_proposals_do_not_save(monkeypatch, tmp_path):
    events = []
    active = []
    monkeypatch.setattr(system_finder.pdf_systems, "page_count", lambda _pdf: 2)
    saved = tmp_path / ".systems.json"
    saved.write_text("human-approved bounds", encoding="utf-8")

    @contextmanager
    def lease(label, log):
        assert not active
        active.append(label)
        events.append("enter")
        try:
            yield SimpleNamespace(
                guard=lambda logger: logger,
                check=lambda: events.append("check"),
            )
        finally:
            active.pop()
            events.append("exit")

    def propose(_pdf, page, **kwargs):
        assert len(active) == 1
        events.append(f"page {page}")
        return [SystemBounds(index=1, page=page, top=0.1, bottom=0.9)]

    monkeypatch.setattr(system_finder.heavy_slot, "heavy_slot", lease)
    monkeypatch.setattr(system_finder, "_page_from_homr", propose)
    found = system_finder.find_bands(
        "score.pdf", out_dir=str(tmp_path), engine=engine(), queue=True
    )
    assert events == ["enter", "page 1", "check", "exit", "enter", "page 2", "check", "exit"]
    assert [(band.index, band.page) for band in found] == [(1, 1), (2, 2)]
    assert saved.read_text(encoding="utf-8") == "human-approved bounds"
    assert list(tmp_path.iterdir()) == [saved]


def test_public_proposal_reports_a_missing_engine_before_reading_pages(monkeypatch):
    monkeypatch.setattr(omr, "default_engine", lambda: None)
    with pytest.raises(omr.HomrMissing, match="install-homr.sh"):
        system_finder.find_bands("score.pdf", queue=False)
