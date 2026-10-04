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


def test_an_old_homr_is_told_to_update_rather_than_answered_another_way(monkeypatch):
    """The app's own copy of the grouping rule is gone (#144): no second answer."""
    monkeypatch.setattr(
        system_finder.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=2,
            stdout="",
            stderr="homer: error: unrecognized arguments: --find-system-bounds --system-page 1",
        ),
    )

    try:
        system_finder._page_from_homr(
            "/tmp/score.pdf", 1, engine=_engine(), dpi=200, log=lambda _line: None)
    except omr.HomrError as error:
        assert "too old" in str(error)
        assert "test homr" in str(error)
    else:
        raise AssertionError("an old homr was not refused")


def test_a_supported_homr_failure_is_not_called_too_old(monkeypatch):
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
