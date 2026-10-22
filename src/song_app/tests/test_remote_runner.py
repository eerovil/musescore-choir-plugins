"""``REMOTE_RUNNER``: another machine reads a band first (#437, #439).

The runner is a stub here. The one shipped (``src/kube_runner/runner.py``) is tested
in its own package; what is pinned here is the contract: the arguments it gets, a
written file being used as the reading, and every other outcome reading the band on
this host under the heavy slot.
"""

import stat

import pytest

from src.song_app import heavy_slot, omr


def _executable(path, text):
    path.write_text("#!/usr/bin/env bash\n" + text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@pytest.fixture
def here(tmp_path, monkeypatch):
    """This host's homr, which writes a parse saying it read here."""
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    homr = _executable(venv / "bin" / "homr",
                       'img="${!#}"; echo "<score-partwise><!--here--></score-partwise>" '
                       '> "${img%.*}.musicxml"\n')
    monkeypatch.delenv("AGENTDECK_API_URL", raising=False)
    monkeypatch.delenv("AGENTDECK_URL", raising=False)
    asked = []
    original = heavy_slot.heavy_slot

    def slot(label, **kw):
        asked.append(label)
        return original(label, **kw)
    monkeypatch.setattr(omr.heavy_slot, "heavy_slot", slot)
    engine = omr.Engine(key="default", label="installed", command=[homr], default=True,
                        gpu="force")
    return engine, asked, str(venv)


def _read(tmp_path, engine):
    image = tmp_path / "system-01.png"
    image.write_bytes(b"png")
    lines = []
    out = omr.read_page(str(image), out_dir=str(tmp_path / "out"), engine=engine,
                        log=lines.append)
    with open(out) as f:
        return f.read(), lines


def _reader(tmp_path, monkeypatch, body):
    reader = _executable(tmp_path / "reader", body)
    monkeypatch.setenv("REMOTE_RUNNER", reader)
    return reader


def test_a_band_the_reader_read_is_used_without_this_hosts_slot(tmp_path, monkeypatch, here):
    engine, asked, venv = here
    _reader(tmp_path, monkeypatch,
            'echo "$@" > "$(dirname "$0")/args"; echo "reading over there"\n'
            'b=$(basename "${2%.*}")\n'
            'echo "<score-partwise><!--there--></score-partwise>" > "$3/$b.musicxml"\n')
    text, lines = _read(tmp_path, engine)
    assert "<!--there-->" in text
    assert "homr-engine" in text          # stamped here like any other parse
    assert "reading over there" in lines
    assert asked == []
    args = (tmp_path / "args").read_text().split()
    assert args[0] == "read-band"
    assert args[1].endswith("system-01.png")
    assert args[3] == venv
    assert args[4:7] == ["--gpu", "force", "--no-title"]


def test_a_reader_that_cannot_says_why_and_the_band_is_read_here(tmp_path, monkeypatch, here):
    engine, asked, _ = here
    _reader(tmp_path, monkeypatch, 'echo "trying"; echo "the card is not free"; exit 3\n')
    text, lines = _read(tmp_path, engine)
    assert "<!--here-->" in text
    assert asked                           # read here under the slot, as before
    assert any("did not read it (the card is not free)" in line for line in lines)


def test_a_reader_that_wrote_nothing_is_not_believed(tmp_path, monkeypatch, here):
    engine, asked, _ = here
    _reader(tmp_path, monkeypatch, "exit 0\n")
    text, lines = _read(tmp_path, engine)
    assert "<!--here-->" in text
    assert any("wrote no MusicXML" in line for line in lines)


def test_a_missing_reader_reads_here(tmp_path, monkeypatch, here):
    engine, asked, _ = here
    monkeypatch.setenv("REMOTE_RUNNER", str(tmp_path / "no-such-reader"))
    text, _ = _read(tmp_path, engine)
    assert "<!--here-->" in text


def test_a_branch_engine_is_never_sent(tmp_path, monkeypatch, here):
    engine, _, _ = here
    _reader(tmp_path, monkeypatch, 'touch "$(dirname "$0")/ran"; exit 1\n')
    branch = omr.Engine(key="branch", label="a branch", command=engine.command, gpu="force")
    text, _ = _read(tmp_path, branch)
    assert "<!--here-->" in text
    assert not (tmp_path / "ran").exists()


def test_no_reader_no_change(tmp_path, monkeypatch, here):
    engine, asked, _ = here
    monkeypatch.setenv("REMOTE_RUNNER", "")
    text, _ = _read(tmp_path, engine)
    assert "<!--here-->" in text and asked


def test_a_reader_past_its_deadline_is_asked_to_stop_first(tmp_path, monkeypatch, here):
    """#443: SIGTERM, so the runner ends its pod job; the band is then read here."""
    engine, asked, _ = here
    monkeypatch.setattr(omr, "REMOTE_TIMEOUT", 1)
    monkeypatch.setattr(omr, "REMOTE_STOP_S", 10)
    _reader(tmp_path, monkeypatch,
            'trap \'touch "$(dirname "$0")/ended"; exit 143\' TERM\n'
            'echo "reading over there"; sleep 60 & wait\n')
    text, lines = _read(tmp_path, engine)
    assert (tmp_path / "ended").exists()
    assert "<!--here-->" in text and asked
    assert any("did not read it" in line for line in lines)


def test_a_reader_that_will_not_stop_is_killed(tmp_path, monkeypatch, here):
    engine, _, _ = here
    monkeypatch.setattr(omr, "REMOTE_TIMEOUT", 1)
    monkeypatch.setattr(omr, "REMOTE_STOP_S", 1)
    _reader(tmp_path, monkeypatch, "trap '' TERM\nsleep 60 & wait\n")
    text, _ = _read(tmp_path, engine)
    assert "<!--here-->" in text


def test_a_local_homr_is_killed_at_once(monkeypatch):
    """Only the remote runner gets the grace: a local homr can keep its SIGKILL."""
    sent = []
    monkeypatch.setattr(omr.os, "killpg", lambda pid, sig: sent.append(sig))

    class Process:
        pid = 1

        def wait(self, timeout=None):
            return 0
    omr._kill(Process())
    assert sent == [omr.signal.SIGKILL]
