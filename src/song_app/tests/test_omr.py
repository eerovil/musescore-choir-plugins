"""Calling homr from the app's own environment.

Most of this drives a stub standing in for homr, because what the module is
responsible for is the boundary — where the binary is, that the answer ends up
where the caller asked for it and the litter does not, that progress reaches a
log, and that every way of failing says something. Whether homr can read music
is homr's business.

The last test is the one the card asks for and the only one that runs the real
thing: a page of the scanned fixture goes in, MusicXML comes out. It needs
homr installed (scripts/install-homr.sh) and poppler, and skips without them.
"""
import contextlib
import os
import shutil
import stat
import time

import pytest
from lxml import etree

from src.song_app import heavy_slot, omr


@pytest.fixture(autouse=True)
def _no_real_deck(monkeypatch):
    """A scan asks AgentDeck for a heavy slot; tests must not take a real one
    off the host they run on. Each test that cares fakes its own deck."""
    monkeypatch.delenv("AGENTDECK_API_URL", raising=False)
    monkeypatch.delenv("AGENTDECK_URL", raising=False)


FIXTURE_PDF = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "fixtures", "virta-venhetta-vie", "00-registered", "Virta venhettä vie.pdf",
)


def stub_homr(tmp_path, script):
    """A fake homr on disk, with $1.. the arguments the module passed it."""
    path = tmp_path / "fake-homr"
    path.write_text("#!/usr/bin/env bash\n" + script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def a_page(tmp_path, name="page-1.png"):
    image = tmp_path / name
    image.write_bytes(b"not really a png, the stub does not look")
    return str(image)


# --- where homr is -------------------------------------------------------


def test_homr_bin_wins_over_everything(monkeypatch, tmp_path):
    monkeypatch.setenv("HOMR_BIN", "/somewhere/else/homr")
    assert omr.homr_binary() == "/somewhere/else/homr"


def test_falls_back_to_the_installed_venv(monkeypatch, tmp_path):
    monkeypatch.delenv("HOMR_BIN", raising=False)
    venv_bin = tmp_path / "bin"
    venv_bin.mkdir()
    (venv_bin / "homr").write_text("")
    monkeypatch.setattr(omr, "DEFAULT_VENV", str(tmp_path))
    assert omr.homr_binary() == str(venv_bin / "homr")


def test_falls_back_to_the_path_when_no_venv(monkeypatch, tmp_path):
    monkeypatch.delenv("HOMR_BIN", raising=False)
    monkeypatch.setattr(omr, "DEFAULT_VENV", str(tmp_path / "no-such-venv"))
    assert omr.homr_binary() == "homr"


def test_not_installed_is_its_own_error(monkeypatch, tmp_path):
    monkeypatch.setenv("HOMR_BIN", str(tmp_path / "no-such-homr"))
    with pytest.raises(omr.HomrMissing) as caught:
        omr.read_page(a_page(tmp_path))
    assert "install-homr.sh" in str(caught.value)


# --- what it hands back --------------------------------------------------


def test_the_musicxml_lands_where_the_caller_asked(monkeypatch, tmp_path):
    # The stub writes beside its input, the way homr does.
    monkeypatch.setenv("HOMR_BIN", stub_homr(tmp_path, 'echo "<score/>" > "${!#%.*}.musicxml"\n'))
    out = tmp_path / "scores"
    produced = omr.read_page(a_page(tmp_path), out_dir=str(out))

    assert produced == str(out / "page-1.musicxml")
    assert open(produced).read().strip() == "<score/>"


def test_without_an_out_dir_it_lands_beside_the_image(monkeypatch, tmp_path):
    monkeypatch.setenv("HOMR_BIN", stub_homr(tmp_path, 'echo "<score/>" > "${!#%.*}.musicxml"\n'))
    pages = tmp_path / "pages"
    pages.mkdir()
    produced = omr.read_page(a_page(pages))
    assert produced == str(pages / "page-1.musicxml")


def test_the_teaser_litter_does_not_follow_the_answer(monkeypatch, tmp_path):
    # homr drops a _teaser.png (and in debug mode more) next to its input.
    monkeypatch.setenv("HOMR_BIN", stub_homr(
        tmp_path,
        'echo "<score/>" > "${!#%.*}.musicxml"\n'
        'echo teaser > "${!#%.*}_teaser.png"\n',
    ))
    pages = tmp_path / "pages"
    pages.mkdir()
    omr.read_page(a_page(pages))
    assert sorted(os.listdir(pages)) == ["page-1.musicxml", "page-1.png"]


def test_reading_a_page_again_replaces_its_answer(monkeypatch, tmp_path):
    image = a_page(tmp_path)
    monkeypatch.setenv("HOMR_BIN", stub_homr(tmp_path, 'echo "<first/>" > "${!#%.*}.musicxml"\n'))
    first = omr.read_page(image)
    monkeypatch.setenv("HOMR_BIN", stub_homr(tmp_path, 'echo "<second/>" > "${!#%.*}.musicxml"\n'))
    second = omr.read_page(image)
    assert first == second
    assert open(second).read().strip() == "<second/>"


# --- how it is called ----------------------------------------------------


def test_the_gpu_is_switched_off_explicitly(monkeypatch, tmp_path):
    # --gpu auto asks whether CUDA is registered, not whether it works, and
    # this host's card is below onnxruntime's floor (#93).
    monkeypatch.setenv("HOMR_BIN", stub_homr(
        tmp_path,
        'echo "$@" > ' + str(tmp_path / "args") + '\n'
        'echo "<score/>" > "${!#%.*}.musicxml"\n',
    ))
    omr.read_page(a_page(tmp_path))
    assert "--gpu no" in open(tmp_path / "args").read()


def test_progress_reaches_the_log(monkeypatch, tmp_path):
    monkeypatch.setenv("HOMR_BIN", stub_homr(
        tmp_path,
        'echo "Found 12 staff line fragments"\n'
        'echo "Finished parsing 12 staves" >&2\n'
        'echo "<score/>" > "${!#%.*}.musicxml"\n',
    ))
    lines = []
    omr.read_page(a_page(tmp_path), log=lines.append)
    # Both streams are a progress channel; homr talks on stderr.
    assert "Found 12 staff line fragments" in lines
    assert "Finished parsing 12 staves" in lines


# --- the heavy slot ------------------------------------------------------


def test_a_page_is_read_under_a_heavy_slot(monkeypatch, tmp_path):
    """A page is ~30s of every core, so it waits its turn — and the slot is
    held across the run, not merely asked for and dropped."""
    order = []
    monkeypatch.setenv("HOMR_BIN", stub_homr(
        tmp_path,
        'echo running\n'
        'echo "<score/>" > "${!#%.*}.musicxml"\n',
    ))

    @contextlib.contextmanager
    def fake_slot(label, **kwargs):
        order.append(f"take {label}")
        yield heavy_slot.Slot("lease-1")
        order.append("release")

    monkeypatch.setattr(omr.heavy_slot, "heavy_slot", fake_slot)
    omr.read_page(
        a_page(tmp_path),
        log=lambda line: order.append("homr") if line == "running" else None,
        label="song app homr MySong page 2",
    )

    assert order == ["take song app homr MySong page 2", "homr", "release"]


def test_the_slot_is_labelled_after_the_page_by_default(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setenv("HOMR_BIN", stub_homr(tmp_path, 'echo "<score/>" > "${!#%.*}.musicxml"\n'))

    @contextlib.contextmanager
    def fake_slot(label, **kwargs):
        seen.append(label)
        yield heavy_slot.Slot()

    monkeypatch.setattr(omr.heavy_slot, "heavy_slot", fake_slot)
    omr.read_page(a_page(tmp_path, "MySong-page-3.png"))
    assert seen == ["song app homr MySong-page-3"]


def test_a_caller_holding_a_slot_does_not_take_a_second(monkeypatch, tmp_path):
    """One lease per song is the other way to do this, so queue=False has to
    genuinely not ask — a nested lease would deadlock a one-slot pool."""
    monkeypatch.setenv("HOMR_BIN", stub_homr(tmp_path, 'echo "<score/>" > "${!#%.*}.musicxml"\n'))

    @contextlib.contextmanager
    def never(label, **kwargs):
        raise AssertionError("asked for a slot when the caller already held one")
        yield

    monkeypatch.setattr(omr.heavy_slot, "heavy_slot", never)
    assert omr.read_page(a_page(tmp_path), queue=False).endswith(".musicxml")


def test_losing_the_slot_stops_the_page(monkeypatch, tmp_path):
    """A heartbeat answering 404 means the cores may already be somebody
    else's. homr's own output is where a page can be stopped."""
    monkeypatch.setenv("HOMR_BIN", stub_homr(
        tmp_path,
        'echo "Found 12 staff line fragments"\n'
        'sleep 20\n'
        'echo "<score/>" > "${!#%.*}.musicxml"\n',
    ))
    slot = heavy_slot.Slot("lease-1")

    @contextlib.contextmanager
    def fake_slot(label, **kwargs):
        yield slot

    monkeypatch.setattr(omr.heavy_slot, "heavy_slot", fake_slot)

    def lose_it(_line):
        slot._lose()          # the heartbeat thread's job, done inline

    with pytest.raises(heavy_slot.SlotLost):
        omr.read_page(a_page(tmp_path), log=lose_it)


def test_a_stopped_page_takes_homr_with_it(monkeypatch, tmp_path):
    """Abandoning the read loop must not leave homr running on cores that have
    been handed to somebody else."""
    marker = tmp_path / "still-running"
    monkeypatch.setenv("HOMR_BIN", stub_homr(
        tmp_path,
        'echo starting\n'
        'sleep 20\n'
        'touch ' + str(marker) + '\n',
    ))
    slot = heavy_slot.Slot("lease-1")

    @contextlib.contextmanager
    def fake_slot(label, **kwargs):
        yield slot

    monkeypatch.setattr(omr.heavy_slot, "heavy_slot", fake_slot)
    with pytest.raises(heavy_slot.SlotLost):
        omr.read_page(a_page(tmp_path), log=lambda _line: slot._lose())

    time.sleep(1.5)
    assert not marker.exists(), "homr outlived the page that was stopped"


# --- how it fails --------------------------------------------------------


def test_a_missing_image_is_refused_before_homr_runs(tmp_path):
    with pytest.raises(omr.HomrError) as caught:
        omr.read_page(str(tmp_path / "nothing.png"))
    assert "No such image" in str(caught.value)


def test_a_pdf_is_refused_rather_than_handed_over(monkeypatch, tmp_path):
    # homr reads one image; feeding it a PDF fails deep inside opencv.
    pdf = tmp_path / "song.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    with pytest.raises(omr.HomrError) as caught:
        omr.read_page(str(pdf))
    assert ".pdf" in str(caught.value)


def test_a_failing_run_carries_what_homr_said(monkeypatch, tmp_path):
    monkeypatch.setenv("HOMR_BIN", stub_homr(
        tmp_path, 'echo "No noteheads found" >&2\nexit 1\n'))
    with pytest.raises(omr.HomrError) as caught:
        omr.read_page(a_page(tmp_path))
    assert "No noteheads found" in str(caught.value)


def test_a_clean_exit_with_no_file_is_still_a_failure(monkeypatch, tmp_path):
    # homr deletes its own output when parsing raises, so the exit code is not
    # the whole story.
    monkeypatch.setenv("HOMR_BIN", stub_homr(tmp_path, 'echo "gave up quietly" >&2\nexit 0\n'))
    with pytest.raises(omr.HomrError) as caught:
        omr.read_page(a_page(tmp_path))
    assert "no MusicXML" in str(caught.value)
    assert "gave up quietly" in str(caught.value)


def test_a_wedged_run_is_killed(monkeypatch, tmp_path):
    monkeypatch.setenv("HOMR_BIN", stub_homr(tmp_path, 'sleep 30\n'))
    with pytest.raises(omr.HomrError) as caught:
        omr.read_page(a_page(tmp_path), timeout=1)
    assert "did not finish" in str(caught.value)


# --- the real thing ------------------------------------------------------


@pytest.mark.skipif(not omr.homr_available(), reason="homr not installed (scripts/install-homr.sh)")
@pytest.mark.skipif(not shutil.which("pdftoppm"), reason="poppler (pdftoppm) is not installed")
def test_a_scanned_page_comes_back_as_musicxml(tmp_path):
    """The card's own acceptance: an image path in, a MusicXML path out."""
    from src.song_app import pdf_systems

    page = pdf_systems.render_page(FIXTURE_PDF, 1, 300, str(tmp_path))

    lines = []
    produced = omr.read_page(page, out_dir=str(tmp_path / "out"), log=lines.append)

    assert os.path.exists(produced)
    root = etree.parse(produced).getroot()
    assert root.tag == "score-partwise"
    assert root.findall(".//part"), "no parts in the MusicXML"
    assert root.findall(".//measure"), "no measures in the MusicXML"
    assert root.findall(".//note"), "no notes in the MusicXML"
    # It took minutes; it had better have said something while it did.
    assert lines
