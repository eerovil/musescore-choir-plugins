"""MuseScore 4 finishes its work and then crashes while exiting.

Measured on MuseScore Studio 4.7.5, macOS 26, October 2026: the export is written,
complete and usable, and the process then dies with SIGABRT and

    libc++abi: terminating due to uncaught exception of type
    std::__1::system_error: mutex lock failed: Invalid argument

Every MuseScore call in this project treated a non-zero exit as a failed step, so
the crash failed Record, the previews and the conversions even though the file was
there. `src/musescore_cli.py` is the one place that decides, and these tests are
what it has to decide:

* a clean exit with its file is success (unchanged);
* that crash, after a complete file written during this run, is success -- and is
  said out loud, never silently;
* that crash with no file, a file cut short, or only an old file left over from an
  earlier run is still failure;
* any other failure is still failure, file or no file.

No real MuseScore is needed: a stand-in program writes a file and exits however
the test tells it to, so every case gives the same answer every time.

The stand-in exits 134 rather than really aborting. A process Python starts
directly and which dies of SIGABRT is reported as -6, and 134 is what a shell
shows for the same death; both are accepted, and -6 is covered without a process
at all, by handing the verdict an outcome that says -6.
"""

import io
import logging
import os
import stat
import struct
import sys
import wave
import zlib

import pytest

SIGNATURE = ("libc++abi: terminating due to uncaught exception of type "
             "std::__1::system_error: mutex lock failed: Invalid argument\n")

EXTS = [".musicxml", ".mscx", ".mid", ".wav", ".pdf", ".png", ".mpos"]


def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    header = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    pixels = zlib.compress(b"\x00\xff")
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", pixels) + chunk(b"IEND", b""))


def _wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"\x00\x00" * 800)
    return buffer.getvalue()


def _samples() -> dict:
    """One small, complete file of each kind MuseScore is asked to write."""
    return {
        ".musicxml": (b'<?xml version="1.0" encoding="UTF-8"?>'
                      b'<score-partwise version="4.0"><part-list/></score-partwise>'),
        ".mscx": (b'<?xml version="1.0" encoding="UTF-8"?>'
                  b'<museScore version="4.70"><Score><Staff id="1"><Measure/></Staff>'
                  b'</Score></museScore>'),
        ".mid": (b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480)
                 + b"MTrk" + struct.pack(">I", 4) + b"\x00\xff\x2f\x00"),
        ".wav": _wav(),
        ".pdf": b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n",
        ".png": _png(),
        # Publish's play order (score.mpos): an XML list of where each bar sits.
        ".mpos": (b'<?xml version="1.0" encoding="UTF-8"?>'
                  b'<score><elements><element id="0" x="0" y="0" sx="100" sy="100" '
                  b'page="0"/></elements><events><event elid="0" position="0"/>'
                  b'</events></score>'),
    }


FAKE_SOURCE = '''#!{python}
import os
import sys

mode = os.environ["FAKE_MUSESCORE_MODE"]
samples = os.environ["FAKE_MUSESCORE_SAMPLES"]
argv = sys.argv[1:]
out = argv[argv.index("-o") + 1]
ext = os.path.splitext(out)[1]
with open(os.path.join(samples, "sample" + ext), "rb") as source:
    data = source.read()

if mode == "hang":
    import time
    time.sleep(30)
    sys.exit(0)

if mode in ("ok", "crash", "crash_nosig", "exit1"):
    with open(out, "wb") as target:
        target.write(data)
elif mode == "crash_truncated":
    with open(out, "wb") as target:
        target.write(data[: len(data) // 2])

if mode in ("crash", "crash_nofile", "crash_truncated", "crash_stale"):
    sys.stderr.write({signature!r})
    sys.exit(134)
if mode == "crash_nosig":
    sys.stderr.write("something else went wrong\\n")
    sys.exit(134)
if mode == "exit1":
    sys.exit(1)
sys.exit(0)
'''


@pytest.fixture
def ms():
    """The module under test, imported per test so each one fails on its own."""
    from src import musescore_cli
    return musescore_cli


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A stand-in MuseScore on MUSESCORE_CLI_PATH. Call it with a mode to set one."""
    samples = tmp_path / "samples"
    samples.mkdir()
    for ext, data in _samples().items():
        (samples / ("sample" + ext)).write_bytes(data)
    script = tmp_path / "fake_mscore"
    script.write_text(FAKE_SOURCE.format(python=sys.executable, signature=SIGNATURE))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("FAKE_MUSESCORE_SAMPLES", str(samples))
    monkeypatch.setenv("MUSESCORE_CLI_PATH", str(script))

    def set_mode(mode: str) -> str:
        monkeypatch.setenv("FAKE_MUSESCORE_MODE", mode)
        return str(script)

    return set_mode


def _run(ms, cli: str, tmp_path, ext: str):
    out = str(tmp_path / ("out" + ext))
    return ms.run([cli, str(tmp_path / "in.mscx"), "-o", out], out, timeout=60), out


# --- the verdict -----------------------------------------------------------


@pytest.mark.parametrize("ext", EXTS)
def test_a_clean_exit_with_its_file_is_success(ms, fake, tmp_path, ext):
    outcome, out = _run(ms, fake("ok"), tmp_path, ext)
    assert outcome.returncode == 0
    assert ms.ok(outcome) is True
    assert os.path.exists(out)


@pytest.mark.parametrize("ext", EXTS)
def test_a_crash_on_exit_after_a_complete_file_is_success_and_is_said(
        ms, fake, tmp_path, ext, caplog):
    said = []
    with caplog.at_level(logging.WARNING):
        outcome, out = _run(ms, fake("crash"), tmp_path, ext)
        assert outcome.returncode == 134
        assert ms.ok(outcome, log=said.append) is True
    assert "crashed" in caplog.text
    assert any("crashed" in line for line in said)
    assert os.path.basename(out) in caplog.text


def test_a_crash_with_no_file_is_failure(ms, fake, tmp_path):
    outcome, out = _run(ms, fake("crash_nofile"), tmp_path, ".musicxml")
    assert not os.path.exists(out)
    assert ms.ok(outcome) is False


@pytest.mark.parametrize("ext", EXTS)
def test_a_crash_with_a_file_cut_short_is_failure(ms, fake, tmp_path, ext):
    outcome, out = _run(ms, fake("crash_truncated"), tmp_path, ext)
    assert os.path.exists(out), "premise: half a file was written"
    assert ms.ok(outcome) is False


def test_a_crash_that_left_only_an_old_file_is_failure(ms, fake, tmp_path):
    """The file is complete and it is not this run's: the run wrote nothing."""
    out = tmp_path / "out.musicxml"
    out.write_bytes(_samples()[".musicxml"])
    outcome = ms.run([fake("crash_stale"), str(tmp_path / "in.mscx"), "-o", str(out)],
                     str(out), timeout=60)
    assert ms.ok(outcome) is False


def test_any_other_failure_is_still_failure_even_with_a_file(ms, fake, tmp_path):
    outcome, out = _run(ms, fake("exit1"), tmp_path, ".musicxml")
    assert os.path.exists(out)
    assert outcome.returncode == 1
    assert ms.ok(outcome) is False


def test_a_crash_without_the_known_message_is_failure(ms, fake, tmp_path):
    """The exception is one named crash, not every abort."""
    outcome, out = _run(ms, fake("crash_nosig"), tmp_path, ".musicxml")
    assert os.path.exists(out)
    assert ms.ok(outcome) is False


def test_the_abort_python_itself_reports_is_accepted_too(ms, tmp_path):
    """-6 is what subprocess says for the same death a shell calls 134."""
    out = tmp_path / "out.musicxml"
    out.write_bytes(_samples()[".musicxml"])
    outcome = ms.Outcome(returncode=-6, stdout="", stderr=SIGNATURE,
                         output_path=str(out), before=None)
    assert ms.ok(outcome) is True


def test_the_abort_python_reports_with_no_file_is_failure(ms, tmp_path):
    outcome = ms.Outcome(returncode=-6, stdout="", stderr=SIGNATURE,
                         output_path=str(tmp_path / "never.musicxml"), before=None)
    assert ms.ok(outcome) is False


# --- export: the one-call form ---------------------------------------------


def test_export_returns_the_file_when_musescore_crashed_on_the_way_out(
        ms, fake, tmp_path):
    out = str(tmp_path / "out.mscx")
    assert ms.export(fake("crash"), str(tmp_path / "in.musicxml"), out, timeout=60) == out


def test_export_raises_and_carries_what_musescore_said(ms, fake, tmp_path):
    out = str(tmp_path / "out.mscx")
    with pytest.raises(ms.MuseScoreError, match="mutex lock failed"):
        ms.export(fake("crash_nofile"), str(tmp_path / "in.musicxml"), out, timeout=60)


# --- the four places the app calls MuseScore -------------------------------


def _mscx(tmp_path) -> str:
    path = tmp_path / "score.mscx"
    path.write_bytes(_samples()[".mscx"])
    return str(path)


def _musicxml(tmp_path, name="song.musicxml") -> str:
    path = tmp_path / name
    path.write_bytes(_samples()[".musicxml"])
    return str(path)


def test_run_musescore_gets_past_the_crash(fake, tmp_path):
    """Publish's exports (MusicXML, play order, MIDI) go through run_musescore."""
    from src.score_export import musescore
    fake("crash")
    out = str(tmp_path / "score.musicxml")
    assert musescore.run_musescore(_mscx(tmp_path), out, timeout=60) == out


def test_run_musescore_still_fails_when_nothing_was_written(fake, tmp_path):
    from src.score_export import musescore
    fake("crash_nofile")
    with pytest.raises(RuntimeError, match="MuseScore CLI failed writing"):
        musescore.run_musescore(_mscx(tmp_path), str(tmp_path / "score.musicxml"),
                                timeout=60)


def test_run_musescore_that_never_finishes_says_so(fake, tmp_path):
    """His message for a stuck MuseScore is kept when the helper decides."""
    from src.score_export import musescore
    fake("hang")
    with pytest.raises(RuntimeError, match=r"did not finish within 1s writing score\.mpos"):
        musescore.run_musescore(_mscx(tmp_path), str(tmp_path / "score.mpos"), timeout=1)


def test_convert_to_mscx_gets_past_the_crash(fake, tmp_path):
    from src.song_app import pipeline
    fake("crash")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    target = pipeline.convert_to_mscx(_musicxml(tmp_path), str(out_dir))
    assert target == str(out_dir / "song.mscx")
    assert os.path.exists(target)


def test_convert_to_mscx_still_fails_when_nothing_was_written(fake, tmp_path):
    from src.song_app import pipeline
    fake("crash_nofile")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with pytest.raises(RuntimeError, match="MuseScore CLI conversion failed"):
        pipeline.convert_to_mscx(_musicxml(tmp_path), str(out_dir))


def test_render_score_pdf_gets_past_the_crash(fake, tmp_path):
    from src.song_app import pipeline
    fake("crash")
    rendered = pipeline.render_score_pdf(_mscx(tmp_path))
    assert rendered.endswith(".render.pdf")
    assert os.path.exists(rendered)


def test_render_score_pdf_still_fails_when_nothing_was_written(fake, tmp_path):
    from src.song_app import pipeline
    fake("crash_nofile")
    with pytest.raises(RuntimeError, match="MuseScore CLI render failed"):
        pipeline.render_score_pdf(_mscx(tmp_path))


def test_scan_system_render_gets_past_the_crash(fake, tmp_path):
    from src.song_app import pipeline
    fake("crash")
    song = tmp_path / "song"
    song.mkdir()
    picture = pipeline.scan_system_render(str(song), _musicxml(tmp_path, "system-1.musicxml"))
    assert picture.endswith(".png")
    assert os.path.exists(picture)


def test_scan_system_render_still_fails_when_nothing_was_written(fake, tmp_path):
    from src.song_app import pipeline
    fake("crash_nofile")
    song = tmp_path / "song"
    song.mkdir()
    with pytest.raises(RuntimeError, match="could not engrave"):
        pipeline.scan_system_render(str(song), _musicxml(tmp_path, "system-1.musicxml"))


def test_scan_system_render_does_not_pass_off_the_previous_picture(fake, tmp_path):
    """An old picture is sitting where the new one would go, and the run wrote none."""
    from src.song_app import pipeline
    fake("crash_stale")
    song = tmp_path / "song"
    cache = song / ".pages" / "scan"
    cache.mkdir(parents=True)
    old = cache / "system-1@200.png"
    old.write_bytes(_samples()[".png"])
    os.utime(old, (1_000_000_000, 1_000_000_000))      # long before the parse
    with pytest.raises(RuntimeError, match="could not engrave"):
        pipeline.scan_system_render(str(song), _musicxml(tmp_path, "system-1.musicxml"))


# --- opening the score in MuseScore ----------------------------------------


def test_the_application_to_open_scores_in_defaults_to_musescore_3(ms, monkeypatch):
    monkeypatch.delenv("MUSESCORE_APP", raising=False)
    assert ms.app_name() == "MuseScore 3"


def test_the_application_to_open_scores_in_is_a_setting(ms, monkeypatch):
    monkeypatch.setenv("MUSESCORE_APP", "MuseScore 4")
    assert ms.app_name() == "MuseScore 4"


def test_open_score_asks_macos_for_the_configured_application(ms, monkeypatch):
    monkeypatch.delenv("MUSESCORE_APP", raising=False)
    asked = []
    monkeypatch.setattr(ms.subprocess, "Popen", lambda argv, *a, **k: asked.append(argv))
    ms.open_score("/somewhere/song_cleaned.mscx")
    assert asked == [["open", "-a", "MuseScore 3", "/somewhere/song_cleaned.mscx"]]


def test_the_server_no_longer_names_musescore_3_when_opening_a_score():
    """The button asked macOS for an application this machine does not have."""
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", "server.py"), encoding="utf-8") as source:
        text = source.read()
    assert '"open", "-a", "MuseScore 3"' not in text
    assert "musescore_cli.open_score(" in text
