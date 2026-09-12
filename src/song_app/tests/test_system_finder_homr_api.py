from __future__ import annotations

import json
from types import SimpleNamespace

from src.song_app import omr, system_finder
from src.song_app.pdf_systems import SystemBounds


def _engine(command=None):
    return omr.Engine(
        key="test",
        label="test homr",
        command=command or ["/opt/homr/bin/homr"],
        env={},
    )


def test_supported_homr_cli_is_the_primary_proposal_path(monkeypatch):
    calls = []
    monkeypatch.setattr(system_finder.pdf_systems, "page_count", lambda _pdf: 2)

    def propose(pdf_path, page, *, engine, dpi, log, timeout=300):
        calls.append((pdf_path, page, engine.key, dpi))
        return [SystemBounds(index=1, page=page, top=0.1, bottom=0.9)]

    monkeypatch.setattr(system_finder, "_page_from_homr", propose)
    monkeypatch.setattr(
        system_finder.legacy,
        "find_bands",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy fallback used")),
    )

    found = system_finder.find_bands(
        "/tmp/score.pdf", engine=_engine(), queue=False, dpi=200
    )

    assert calls == [
        ("/tmp/score.pdf", 1, "test", 200),
        ("/tmp/score.pdf", 2, "test", 200),
    ]
    assert [(item.index, item.page) for item in found] == [(1, 1), (2, 2)]


def test_checkout_engine_uses_public_homr_module_cli(monkeypatch):
    called = {}

    def run(command, **kwargs):
        called["command"] = command
        called["env"] = kwargs["env"]
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "systems": [
                        {
                            "index": 1,
                            "page": 3,
                            "top": 0.2,
                            "bottom": 0.7,
                            "measure_start": 0,
                            "measure_end": 0,
                        }
                    ]
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(system_finder.subprocess, "run", run)
    engine = _engine(["/venv/bin/python", "-c", "from homr.main import main; main()"])
    object.__setattr__(engine, "env", {"PYTHONPATH": "/work/homr"})

    found = system_finder._page_from_homr(
        "/tmp/score.pdf", 3, engine=engine, dpi=200, log=lambda _line: None
    )

    assert called["command"] == [
        "/venv/bin/python",
        "-m",
        "homr.main",
        "/tmp/score.pdf",
        "--gpu",
        "no",
        "--find-system-bounds",
        "--system-page",
        "3",
        "--system-dpi",
        "200",
    ]
    assert called["env"]["PYTHONPATH"] == "/work/homr"
    assert found == [SystemBounds(index=1, page=3, top=0.2, bottom=0.7)]


def test_old_homr_cli_selects_compatibility_fallback(monkeypatch):
    monkeypatch.setattr(
        system_finder.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=2,
            stdout="",
            stderr="homer: error: unrecognized arguments: --find-system-bounds --system-page 1",
        ),
    )

    assert (
        system_finder._page_from_homr(
            "/tmp/score.pdf",
            1,
            engine=_engine(),
            dpi=200,
            log=lambda _line: None,
        )
        is None
    )


def test_fallback_restarts_with_the_legacy_whole_pdf_path(monkeypatch):
    monkeypatch.setattr(system_finder.pdf_systems, "page_count", lambda _pdf: 4)
    calls = []

    def unsupported(*args, **kwargs):
        calls.append("supported")
        return None

    expected = [SystemBounds(index=1, page=1, top=0.1, bottom=0.9)]

    def legacy(*args, **kwargs):
        calls.append("legacy")
        return expected

    monkeypatch.setattr(system_finder, "_page_from_homr", unsupported)
    monkeypatch.setattr(system_finder.legacy, "find_bands", legacy)

    found = system_finder.find_bands(
        "/tmp/score.pdf", engine=_engine(), queue=False, dpi=200
    )

    assert found == expected
    assert calls == ["supported", "legacy"]


def test_supported_homr_failure_does_not_hide_behind_legacy(monkeypatch):
    monkeypatch.setattr(
        system_finder.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="segmentation failed",
        ),
    )

    try:
        system_finder._page_from_homr(
            "/tmp/score.pdf",
            1,
            engine=_engine(),
            dpi=200,
            log=lambda _line: None,
        )
    except omr.HomrError as error:
        assert "segmentation failed" in str(error)
    else:
        raise AssertionError("supported homr failure was hidden")
