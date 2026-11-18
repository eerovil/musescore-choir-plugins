"""One graphics card, a singer or a homr read at a time (#510).

The card has 6 GB; a singer's models and homr's both want most of it. A singer waits for
the card, says so and rests it 60 s after the last singer; a homr read never waits inside
its heavy slot and reads on the processor instead, except a re-read, which lets go of the
slot and waits. conftest points GPU_CARD_LOCK at a temporary file.
"""
import fcntl
import os
import sys
import time

import pytest

from src import gpu_card
from src.sing_song import main as sing_main
from src.sing_song import progress
from src.song_app import omr, system_finder
from src.song_app.tests.test_omr import a_page, venv_homr


@pytest.fixture
def card(tmp_path, monkeypatch):
    monkeypatch.setenv("GPU_CARD_LOCK", str(tmp_path / "card.lock"))
    return tmp_path / "card.lock"


class Other:
    """Somebody else holding the card: a lock on a file description of its own."""

    def __init__(self):
        self.fd = None

    def take(self):
        self.fd = os.open(gpu_card.lock_path(), os.O_RDWR | os.O_CREAT)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def let_go(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


@pytest.fixture
def other(card):
    o = Other()
    yield o
    o.let_go()


# --- the lock itself ---------------------------------------------------------


def test_a_free_card_is_held_and_the_time_written_on_release(card):
    said = []
    with gpu_card.hold(said.append) as got:
        assert got and gpu_card.busy()
    assert not gpu_card.busy() and said == []
    assert abs(float(card.read_text()) - time.time()) < 5


def test_a_singer_rests_the_card_after_the_last_one(card):
    card.write_text(f"{time.time() - 20:.3f}\n")
    slept, said = [], []
    with gpu_card.hold(said.append, sleep=slept.append):
        pass
    assert len(slept) == 1 and 35 < slept[0] <= 40
    assert said and said[0].startswith("letting the graphics card rest")


def test_a_rest_long_past_is_no_rest(card):
    card.write_text(f"{time.time() - 600:.3f}\n")
    slept = []
    with gpu_card.hold(lambda m: None, sleep=slept.append):
        pass
    assert slept == []


def test_a_singer_waits_for_the_card_and_says_so(other):
    other.take()
    said, waited = [], []

    def on_wait():
        waited.append(True)
        other.let_go()   # the other singer finishes while this one waits

    with gpu_card.hold(said.append, on_wait=on_wait, pause=False) as got:
        assert got
    assert waited == [True]
    assert said == ["waiting for the graphics card", "got the graphics card"]


def test_try_hold_does_not_wait(other):
    other.take()
    with gpu_card.try_hold() as got:
        assert got is False
    assert gpu_card.busy()


def test_wait_free_does_not_keep_the_card(card):
    gpu_card.wait_free(lambda m: pytest.fail(m))
    assert not gpu_card.busy()


# --- homr's reads ------------------------------------------------------------


def gpu_homr(tmp_path, monkeypatch):
    monkeypatch.delenv("OMR_GPU", raising=False)
    monkeypatch.setenv("HOMR_BIN", venv_homr(tmp_path, "gpu=force\n"))


def test_a_read_on_a_free_card_reads_on_it_and_holds_it(tmp_path, monkeypatch, card):
    gpu_homr(tmp_path, monkeypatch)
    seen = []
    real = omr._run

    def run(*a, **k):
        seen.append(gpu_card.busy())
        return real(*a, **k)

    monkeypatch.setattr(omr, "_run", run)
    omr.read_page(a_page(tmp_path))
    assert "--gpu force" in open(tmp_path / "args").read()
    assert seen == [True] and not gpu_card.busy()


def test_a_read_while_a_singer_sings_reads_on_the_processor_and_says_so(tmp_path, monkeypatch, other):
    gpu_homr(tmp_path, monkeypatch)
    other.take()
    said = []
    omr.read_page(a_page(tmp_path), log=said.append)
    assert "--gpu no" in open(tmp_path / "args").read()
    assert "the graphics card is singing; reading this band on the processor" in said


def test_a_re_read_waits_for_the_card_outside_its_slot(tmp_path, monkeypatch, other):
    gpu_homr(tmp_path, monkeypatch)
    other.take()
    waits = []

    def wait_free(log):
        waits.append(True)
        if len(waits) == 2:   # the singer lets go after the first try found the card taken
            other.let_go()

    monkeypatch.setattr(omr.gpu_card, "wait_free", wait_free)
    omr.read_page(a_page(tmp_path), wait_for_card=True)
    assert len(waits) == 2
    assert "--gpu force" in open(tmp_path / "args").read()


def test_a_processor_engine_takes_no_lock(tmp_path, monkeypatch, other):
    monkeypatch.setenv("OMR_GPU", "no")
    monkeypatch.setenv("HOMR_BIN", venv_homr(tmp_path, "gpu=force\n"))
    other.take()
    said = []
    omr.read_page(a_page(tmp_path), log=said.append, wait_for_card=True)
    assert "--gpu no" in open(tmp_path / "args").read()
    assert not any("graphics card" in s for s in said)


def test_ask_homr_reads_on_the_processor_while_a_singer_sings(monkeypatch, other):
    engine = omr.Engine(key="installed", label="x", command=["homr"], gpu="force")
    asked = []
    monkeypatch.setattr(system_finder, "_ask_homr",
                        lambda pdf, page, engine, dpi, log, timeout, gpu: asked.append(gpu) or [])
    other.take()
    said = []
    system_finder._page_from_homr("x.pdf", 1, engine=engine, dpi=200, log=said.append, timeout=60)
    assert asked == ["no"]
    assert "the graphics card is singing; reading this page on the processor" in said


# --- the singer's run --------------------------------------------------------


def test_one_run_of_a_song_at_a_time(tmp_path, monkeypatch):
    monkeypatch.setenv("SING_STATE_DIR", str(tmp_path / "sing"))
    first = progress.run_lock("laulu")
    assert first is not None and progress.run_locked("laulu")
    assert progress.run_lock("laulu") is None
    assert progress.run_lock("toinen") is not None
    os.close(first)
    assert not progress.run_locked("laulu")


def test_a_run_started_by_the_app_records_its_id(tmp_path, monkeypatch):
    monkeypatch.setenv("SING_RUN_ID", "laulu-1-abc")
    run = progress.Run(str(tmp_path), ["jm"], 3)
    run.step("bundle")
    assert progress.read(str(tmp_path))["run_id"] == "laulu-1-abc"


def test_a_dead_run_waiting_for_the_card_reads_as_failed(tmp_path):
    progress.write(str(tmp_path), {"state": "waiting", "for": "graphics card", "pid": 2 ** 22 + 7,
                                   "updated": 1.0})
    assert progress.read(str(tmp_path))["state"] == "failed"
    progress.write(str(tmp_path), {"state": "waiting", "updated": 1.0})   # --waiting's own
    assert progress.read(str(tmp_path))["state"] == "waiting"


def test_a_second_run_of_a_song_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SING_STATE_DIR", str(tmp_path / "sing"))
    (tmp_path / "songs" / "laulu").mkdir(parents=True)
    monkeypatch.setattr(sing_main, "ROOT", str(tmp_path))
    held = progress.run_lock("laulu")
    try:
        for extra in ([], ["--waiting"]):
            monkeypatch.setattr(sys, "argv", ["sing_song.py", "laulu", "--no-sing", *extra])
            monkeypatch.setattr(sing_main, "build_bundle", lambda *a: pytest.fail("ran"))
            with pytest.raises(SystemExit) as stop:
                sing_main.main()
            assert stop.value.code == sing_main.EXIT_ANOTHER_RUN
            assert "another sing_song run is singing laulu" in capsys.readouterr().err
    finally:
        os.close(held)
