"""runner.py sing-line against the same fake GPU machine as the band reader's tests."""

import os

import pytest

from src.kube_runner import runner
from src.kube_runner.tests.test_read_band import FakeEeroPc, _executable, eero_pc  # noqa: F401

# the singer in the pod: python -I sing_ufr.py <line.json> <out.wav> 100 <voice>; it writes
# the WAV and says what it was given
POD_SINGER = ('#!/usr/bin/env bash\n'
              'echo "singing $6 lang=$SONG_LANG ufr=$UFR_DIR gpu=$GPU"\n'
              'printf "RIFF-$(cat "$3")" > "$4"\n')


@pytest.fixture
def singer(tmp_path, eero_pc, monkeypatch):  # noqa: F811
    runner.use_kind("sing-line")
    keep = tmp_path / "sing-keep"
    _executable(keep / "venv" / "bin" / "python", POD_SINGER)
    (keep / "ufr").mkdir(parents=True)
    (keep / "ufr" / ".complete").write_text("")
    monkeypatch.setenv("SONG_LANG", "la")
    monkeypatch.setenv("DUMP_TOKENS", "/somewhere")
    return eero_pc


def _line(tmp_path):
    line = tmp_path / "p1-high.json"
    line.write_text('[{"pitch": 60}]')
    return str(line)


def test_a_line_is_sung_in_the_pod_with_this_runs_settings(tmp_path, singer, capsys):
    wav = tmp_path / "p1-high.wav"
    assert runner.main(["sing-line", _line(tmp_path), str(wav), "remy_adult"]) == 0
    assert wav.read_bytes() == b'RIFF-[{"pitch": 60}]'
    said = capsys.readouterr().out
    keep = tmp_path / "sing-keep"
    assert f"singing remy_adult lang=la ufr={keep}/ufr gpu=1" in said
    # what only this host's tests and tools read stays here
    assert "DUMP_TOKENS" not in runner._singer_env()
    assert not os.listdir(tmp_path / "pod-jobs")


def test_starting_the_singer_frees_the_card_from_an_idle_reader(tmp_path, singer):
    singer.pod = "missing"
    assert runner.main(["sing-line", _line(tmp_path), str(tmp_path / "o.wav"), "lyse_core"]) == 0
    assert ["other", "delete", "pod", "reader", "--ignore-not-found", "--wait=true"] in singer.calls


def test_the_voices_are_copied_the_first_time(tmp_path, singer, monkeypatch):
    keep = tmp_path / "sing-keep"
    (keep / "ufr" / ".complete").unlink()
    ufr = tmp_path / "ufr"
    for d in runner.VOICE_DIRS:
        (ufr / d).mkdir(parents=True)
        (ufr / d / "voice.onnx").write_text(d)
    monkeypatch.setenv("UFR_DIR", str(ufr))
    monkeypatch.setattr(runner, "CHUNK", 1000)   # several pieces
    assert runner.main(["sing-line", _line(tmp_path), str(tmp_path / "o.wav"), "lyse_core"]) == 0
    assert (keep / "ufr" / "dsdur" / "voice.onnx").read_text() == "dsdur"
    assert (keep / "ufr" / ".complete").exists() and not (keep / "ufr.tar").exists()


def test_no_voices_to_copy_gives_the_line_back(tmp_path, singer, monkeypatch, capsys):
    (tmp_path / "sing-keep" / "ufr" / ".complete").unlink()
    monkeypatch.delenv("UFR_DIR", raising=False)
    assert runner.main(["sing-line", _line(tmp_path), str(tmp_path / "o.wav"), "lyse_core"]) == 3
    assert "UFR_DIR" in capsys.readouterr().out.splitlines()[-1]
    assert not (tmp_path / "o.wav").exists()


def test_a_singer_that_fails_gives_the_line_back(tmp_path, singer, capsys):
    _executable(tmp_path / "sing-keep" / "venv" / "bin" / "python", "#!/usr/bin/env bash\necho broken\nexit 2\n")
    assert runner.main(["sing-line", _line(tmp_path), str(tmp_path / "o.wav"), "lyse_core"]) == 3
    assert "exited 2" in capsys.readouterr().out.splitlines()[-1]


def test_a_wrong_call_is_refused():
    assert runner.main(["sing-line", "only-one"]) == 64
    assert runner.main(["something-else", "a", "b", "c"]) == 64
