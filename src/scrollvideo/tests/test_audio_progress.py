"""Progress emitted while MuseScore creates the per-part audio mixes."""

from concurrent.futures import Future

from src.scrollvideo.build import _collect_audio_results


def test_each_completed_audio_mix_advances_visible_progress():
    soprano = Future()
    alto = Future()
    soprano.set_result(("soprano.wav", False))
    alto.set_result(("alto.wav", True))
    lines = []

    results = _collect_audio_results({"S1": soprano, "A1": alto}, lines.append)

    assert results == {
        "S1": ("soprano.wav", False),
        "A1": ("alto.wav", True),
    } or results == {
        "A1": ("alto.wav", True),
        "S1": ("soprano.wav", False),
    }
    assert lines[0] == "Creating audio mixes: 0/2 (0%)"
    assert lines[-1].startswith("Creating audio mixes: 2/2 (100%)")
    assert {line.rsplit(" — ", 1)[-1] for line in lines[1:]} == {"S1", "A1"}
