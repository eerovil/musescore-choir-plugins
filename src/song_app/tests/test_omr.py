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
import json
import os
import shutil
import stat
import subprocess
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


# --- which homr ----------------------------------------------------------
#
# A homr change is tried out on a branch, and the branch is never installed: the
# app runs a local working copy from source against the installed venv's
# dependencies. So what these pin is the discovery, the labels, and that the
# working copy is what actually runs.


def a_venv(path, branch=None, source="homr[cpu] @ git+.../homr.git@main"):
    """A venv shaped the way scripts/install-homr.sh leaves one."""
    (path / "bin").mkdir(parents=True)
    for name in ("homr", "python"):
        exe = path / "bin" / name
        exe.write_text("#!/usr/bin/env bash\n")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    lines = [f"source={source}"] + ([f"branch={branch}"] if branch else [])
    (path / "homr-engine.txt").write_text("\n".join(lines) + "\n")
    return str(path / "bin" / "homr")


def a_checkout(path, branch="main", worktrees=()):
    """A homr working copy, with git worktrees beside it if asked for."""
    import subprocess as sp
    path.mkdir(parents=True)
    (path / "homr").mkdir()
    (path / "homr" / "__init__.py").write_text("")
    run = lambda *a: sp.run(a, cwd=str(path), check=True, capture_output=True)
    run("git", "init", "-q", "-b", branch)
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "T")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "homr")
    for name, tree_branch in worktrees:
        run("git", "worktree", "add", "-q", "-b", tree_branch, str(path.parent / name))
    return str(path)


def test_one_install_and_no_checkout_is_one_engine(monkeypatch, tmp_path):
    monkeypatch.delenv("HOMR_BIN", raising=False)
    binary = a_venv(tmp_path / "homr-venv", branch="main")
    monkeypatch.setattr(omr, "DEFAULT_VENV", str(tmp_path / "homr-venv"))
    monkeypatch.setattr(omr, "CHECKOUT", str(tmp_path / "no-such-checkout"))

    engines = omr.engines()
    assert [(e.key, e.label, e.command, e.default) for e in engines] == [
        ("default", "installed: main", [binary], True)]


def test_a_working_copy_and_its_worktrees_are_engines(monkeypatch, tmp_path):
    """No install: the code comes from the checkout, the dependencies from the venv."""
    monkeypatch.delenv("HOMR_BIN", raising=False)
    a_venv(tmp_path / "homr-venv", branch="main")
    monkeypatch.setattr(omr, "DEFAULT_VENV", str(tmp_path / "homr-venv"))
    checkout = a_checkout(tmp_path / "homr", branch="main",
                          worktrees=[("system-4", "prototype/system-4")])
    monkeypatch.setattr(omr, "CHECKOUT", checkout)

    engines = omr.engines()

    assert [e.key for e in engines] == ["default", "homr", "system-4"]
    # The label is the branch each working copy has out *now* -- switching a
    # branch there changes the engine with nothing to reinstall -- and it names
    # the directory too, since a worktree keeps its name when its branch moves.
    assert [e.label for e in engines] == [
        "installed: main", "main — homr", "prototype/system-4 — system-4"]
    tree = engines[2]
    assert tree.command[1:] == ["-c", omr.RUN_HOMR]
    assert tree.command[0].endswith("/bin/python"), "the venv's interpreter"
    assert tree.env == {"PYTHONPATH": str(tmp_path / "system-4")}


def test_a_directory_that_is_not_a_homr_checkout_is_not_an_engine(monkeypatch, tmp_path):
    monkeypatch.delenv("HOMR_BIN", raising=False)
    a_venv(tmp_path / "homr-venv", branch="main")
    monkeypatch.setattr(omr, "DEFAULT_VENV", str(tmp_path / "homr-venv"))
    checkout = a_checkout(tmp_path / "homr")
    import shutil as sh
    sh.rmtree(os.path.join(checkout, "homr"))
    monkeypatch.setattr(omr, "CHECKOUT", checkout)

    assert [e.key for e in omr.engines()] == ["default"]


def test_without_an_install_there_are_no_checkout_engines(monkeypatch, tmp_path):
    """The working copies borrow the venv's dependencies; there is nothing to borrow."""
    monkeypatch.setenv("HOMR_BIN", str(tmp_path / "no-such-homr"))
    monkeypatch.setattr(omr, "CHECKOUT", a_checkout(tmp_path / "homr"))

    assert omr.engines() == []


def test_no_key_means_the_installed_engine(monkeypatch, tmp_path):
    binary = a_venv(tmp_path / "homr-venv", branch="main")
    monkeypatch.setenv("HOMR_BIN", binary)
    assert omr.engine_for(None).command == [binary]
    assert omr.engine_for("default").command == [binary]


def test_an_engine_that_is_not_there_is_refused(monkeypatch, tmp_path):
    """Not silently the default: a parse nobody can account for is worse.

    The whole point of picking an engine is to know which homr read the page.
    """
    monkeypatch.delenv("HOMR_BIN", raising=False)
    a_venv(tmp_path / "homr-venv", branch="main")
    monkeypatch.setattr(omr, "DEFAULT_VENV", str(tmp_path / "homr-venv"))
    monkeypatch.setattr(omr, "CHECKOUT", str(tmp_path / "nothing-here"))

    with pytest.raises(omr.HomrMissing) as caught:
        omr.engine_for("system-4")
    assert "system-4" in str(caught.value)


def test_the_weights_are_linked_rather_than_downloaded_again(monkeypatch, tmp_path):
    """homr keeps its weights beside its own source, so a working copy would
    fetch its own 150 MB -- per worktree. The names carry a content hash, so a
    link cannot be the wrong weights."""
    monkeypatch.delenv("HOMR_BIN", raising=False)
    venv = tmp_path / "homr-venv"
    a_venv(venv, branch="main")
    package = venv / "lib" / "python3.12" / "site-packages" / "homr" / "segmentation"
    package.mkdir(parents=True)
    (package / "segnet_308-abc.onnx").write_bytes(b"weights")
    monkeypatch.setattr(omr, "DEFAULT_VENV", str(venv))
    checkout = a_checkout(tmp_path / "homr")
    monkeypatch.setattr(omr, "CHECKOUT", checkout)

    engine = omr.engine_for("homr")

    linked = os.path.join(checkout, "homr", "segmentation", "segnet_308-abc.onnx")
    assert os.path.islink(linked) and open(linked, "rb").read() == b"weights"
    assert engine.env["PYTHONPATH"] == checkout
    # Idempotent, and a real file is never replaced by a link to another one.
    os.unlink(linked)
    open(linked, "wb").write(b"its own")
    omr.engine_for("homr")
    assert not os.path.islink(linked)


def test_the_chosen_engine_is_what_reads_the_page(monkeypatch, tmp_path):
    """The picked engine runs, with its environment, and the default is not consulted."""
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("HOMR_BIN", stub_homr(tmp_path, "exit 1\n"))
    chosen = stub_homr(other, 'echo "<picked>$MARK</picked>" > "${!#%.*}.musicxml"\n')
    engine = omr.Engine(key="system-4", label="prototype/system-4",
                        command=[chosen], env={"MARK": "here"})

    out = omr.read_page(a_page(tmp_path), out_dir=str(tmp_path / "out"),
                        engine=engine, queue=False)

    assert "<picked>here</picked>" in open(out).read()


# --- what it hands back --------------------------------------------------


def test_the_musicxml_lands_where_the_caller_asked(monkeypatch, tmp_path):
    # The stub writes beside its input, the way homr does.
    monkeypatch.setenv("HOMR_BIN", stub_homr(tmp_path, 'echo "<score/>" > "${!#%.*}.musicxml"\n'))
    out = tmp_path / "scores"
    produced = omr.read_page(a_page(tmp_path), out_dir=str(out))

    assert produced == str(out / "page-1.musicxml")
    # Apart from the one line saying which homr read it, which every parse
    # carries and which comes straight back off (#154).
    assert omr.strip_provenance(open(produced, "rb").read()).strip() == b"<score/>"


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
    assert omr.strip_provenance(open(second, "rb").read()).strip() == b"<second/>"


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


def test_the_title_is_not_read(monkeypatch, tmp_path):
    # The app never uses the title homr reads, and since upstream 9ec3a78 reading
    # it means fetching OCR weights first (#220).
    monkeypatch.setenv("HOMR_BIN", stub_homr(
        tmp_path,
        'echo "$@" > ' + str(tmp_path / "args") + '\n'
        'echo "<score/>" > "${!#%.*}.musicxml"\n',
    ))
    omr.read_page(a_page(tmp_path))
    assert "--no-title" in open(tmp_path / "args").read().split()


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


# --- slurs are homr's now --------------------------------------------------
#
# Pairing homr's slur tokens and dropping runaways moved into the fork
# (eerovil/homr#62, #144). The app must not do it a second time: a slur that
# reaches the app is homr's claim about the page and is kept as written.


def test_the_app_leaves_homrs_slurs_alone(monkeypatch, tmp_path):
    """A slur across three barlines is homr's to not emit, not the app's to drop."""
    notes = "".join(
        f'<measure number="{bar}"><note><pitch><step>C</step><octave>4</octave></pitch>'
        f'<duration>4</duration><voice>1</voice><type>whole</type>{slur}</note></measure>'
        for bar, slur in ((1, '<notations><slur type="start" number="1"/></notations>'),
                          (2, ""), (3, ""),
                          (4, '<notations><slur type="stop" number="1"/></notations>')))
    written = tmp_path / "homr-said.musicxml"
    written.write_text(
        '<?xml version="1.0" encoding="UTF-8"?><score-partwise version="4.0">'
        '<part-list><score-part id="P1"><part-name>V</part-name></score-part>'
        f'</part-list><part id="P1">{notes}</part></score-partwise>')
    monkeypatch.setenv("HOMR_BIN", stub_homr(
        tmp_path, f'cp "{written}" "${{!#%.*}}.musicxml"\n'))

    produced = omr.read_page(a_page(tmp_path), out_dir=str(tmp_path / "out"))

    assert omr.strip_provenance(open(produced, "rb").read()) == written.read_bytes()
    assert not hasattr(omr, "resolve_slurs")


def test_a_page_homr_got_right_is_not_rewritten(monkeypatch, tmp_path):
    monkeypatch.setenv("HOMR_BIN", stub_homr(
        tmp_path, 'echo "<score-partwise/>" > "${!#%.*}.musicxml"\n'))
    produced = omr.read_page(a_page(tmp_path), out_dir=str(tmp_path / "out"))
    # The provenance line is the only thing between what homr wrote and what is
    # on disk, and taking it off gives homr's bytes back exactly.
    assert omr.strip_provenance(open(produced, "rb").read()) == b"<score-partwise/>\n"


def test_the_installed_engine_says_which_commit_it_is(monkeypatch, tmp_path):
    """"main" alone was a guess: the label read `main` whatever was installed.

    pip's own record of the install is the one account of it that cannot go
    stale, and being frozen is what makes this engine worth comparing against.
    """
    monkeypatch.delenv("HOMR_BIN", raising=False)
    venv = tmp_path / "homr-venv"
    a_venv(venv, branch="main")
    info = venv / "lib" / "python3.12" / "site-packages" / "homr-0.7.0.post37.dist-info"
    info.mkdir(parents=True)
    (info / "direct_url.json").write_text(json.dumps({
        "url": "https://github.com/eerovil/homr.git",
        "vcs_info": {"vcs": "git", "requested_revision": "main",
                     "commit_id": "3fe86a3e84db43af19eae452e29830c1f46c3b34"}}))
    monkeypatch.setattr(omr, "DEFAULT_VENV", str(venv))
    monkeypatch.setattr(omr, "CHECKOUT", str(tmp_path / "none"))

    assert omr.engines()[0].label == "installed: main @ 3fe86a3"


# --- provenance: which homr read this, written into the parse itself ---------
#
# The record has to reach a reader who never opens the app -- an agent opening
# a fragment straight off disk is exactly what #129 was -- so it is in the file,
# and it has to be removable byte for byte, or `scan.content_stamp` could not
# step over it and an upgrade would start discarding work (#154).


def test_the_installed_engine_carries_its_commit(monkeypatch, tmp_path):
    monkeypatch.delenv("HOMR_BIN", raising=False)
    venv = tmp_path / "homr-venv"
    a_venv(venv, branch="main")
    info = venv / "lib" / "python3.12" / "site-packages" / "homr-0.7.0.post37.dist-info"
    info.mkdir(parents=True)
    (info / "direct_url.json").write_text(json.dumps({
        "url": "https://github.com/eerovil/homr.git",
        "vcs_info": {"vcs": "git", "requested_revision": "main",
                     "commit_id": "3fe86a3e84db43af19eae452e29830c1f46c3b34"}}))
    monkeypatch.setattr(omr, "DEFAULT_VENV", str(venv))
    monkeypatch.setattr(omr, "CHECKOUT", str(tmp_path / "none"))

    engine = omr.engines()[0]
    assert engine.commit == "3fe86a3e84db43af19eae452e29830c1f46c3b34"
    assert engine.dirty is False


def test_a_checkout_engine_carries_the_commit_it_has_out(monkeypatch, tmp_path):
    """The label moves when a branch is switched; the commit is what lasts."""
    monkeypatch.delenv("HOMR_BIN", raising=False)
    a_venv(tmp_path / "homr-venv", branch="main")
    monkeypatch.setattr(omr, "DEFAULT_VENV", str(tmp_path / "homr-venv"))
    checkout = a_checkout(tmp_path / "homr", branch="main")
    monkeypatch.setattr(omr, "CHECKOUT", checkout)

    head = subprocess.run(["git", "-C", checkout, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    tree = omr.engines()[1]
    assert tree.commit == head
    assert tree.dirty is False


def test_an_edited_checkout_says_so(monkeypatch, tmp_path):
    """A commit is a claim about what ran, and an edited working copy breaks it."""
    monkeypatch.delenv("HOMR_BIN", raising=False)
    a_venv(tmp_path / "homr-venv", branch="main")
    monkeypatch.setattr(omr, "DEFAULT_VENV", str(tmp_path / "homr-venv"))
    checkout = a_checkout(tmp_path / "homr", branch="main")
    monkeypatch.setattr(omr, "CHECKOUT", checkout)
    with open(os.path.join(checkout, "homr", "__init__.py"), "w") as f:
        f.write("# an uncommitted edit\n")

    assert omr.engines()[1].dirty is True


def _xml(tmp_path, name="page.musicxml"):
    path = tmp_path / name
    path.write_bytes(b'<?xml version="1.0" encoding="UTF-8"?>\n'
                     b"<score-partwise><part-list/></score-partwise>\n")
    return str(path)


def test_a_parse_says_which_homr_read_it(tmp_path):
    engine = omr.Engine(key="system-4", label="prototype/system-4 — system-4",
                        command=["/x"], commit="abc1234def", dirty=True)
    path = _xml(tmp_path)
    omr.stamp_provenance(path, engine)

    said = open(path, encoding="utf-8").read()
    assert "homr-engine" in said and "prototype/system-4" in said
    assert said.index("homr-engine") > said.index("<?xml"), "after the declaration"
    assert omr.read_provenance(path) == {
        "engine": "system-4", "label": "prototype/system-4 — system-4",
        "commit": "abc1234def", "dirty": True}


def test_a_fragment_nobody_stamped_reads_as_unknown(tmp_path):
    """Every fragment on the host today, and there is no way to recover better."""
    assert omr.read_provenance(_xml(tmp_path)) is None
    assert omr.read_provenance(str(tmp_path / "no-such-file")) is None


def test_the_line_comes_back_off_byte_for_byte(tmp_path):
    """What makes this provenance and not a stamp: the parse itself is unchanged."""
    path = _xml(tmp_path)
    original = open(path, "rb").read()
    omr.stamp_provenance(path, omr.Engine(key="default", label="installed: main @ 3fe86a3",
                                          command=["/x"], commit="3fe86a3"))
    stamped = open(path, "rb").read()

    assert stamped != original
    assert omr.strip_provenance(stamped) == original
    assert omr.strip_provenance(original) == original, "nothing to strip is untouched"


def test_stamping_twice_leaves_one_line(tmp_path):
    """Re-reading replaces the record rather than piling records up."""
    path = _xml(tmp_path)
    first = omr.Engine(key="default", label="installed: main", command=["/x"], commit="aaa")
    second = omr.Engine(key="wt", label="branch — wt", command=["/x"], commit="bbb")
    omr.stamp_provenance(path, first)
    omr.stamp_provenance(path, second)

    assert open(path, encoding="utf-8").read().count("homr-engine") == 1
    assert omr.read_provenance(path)["commit"] == "bbb"


def test_a_label_cannot_break_the_comment(tmp_path):
    """Branch names are people's, and `--` ends an XML comment."""
    path = _xml(tmp_path)
    omr.stamp_provenance(path, omr.Engine(
        key="wt", label='we--ird "name"', command=["/x"], commit="c0ffee"))

    etree.parse(path)              # still parseable, which is the whole worry
    assert omr.read_provenance(path)["commit"] == "c0ffee"


# --- whole-measure rests (#164) ------------------------------------------
#
# homr has no way to say "second voice on this staff", so a printed whole-bar
# rest and the notes engraved beside it come out sharing one <voice> and the
# bar overfills by a whole note. The rule moves the rest out. The bar it was
# diagnosed against is committed beside these tests rather than read out of
# `songs/`, which is live and changed under #130 once already.

FRAGMENT = os.path.join(os.path.dirname(__file__), "test_files",
                        "shared_whole_rest.musicxml")


def a_measure(body, number="1"):
    return etree.fromstring(
        f'<part id="P1"><measure number="{number}">'
        "<attributes><divisions>4</divisions></attributes>"
        f"{body}</measure></part>")


def note(duration, voice, kind="quarter", rest=False, staff="1", chord=False):
    return ("<note>" + ("<chord/>" if chord else "")
            + ("<rest/>" if rest else
               "<pitch><step>C</step><octave>4</octave></pitch>")
            + f"<duration>{duration}</duration><type>{kind}</type>"
            + f"<voice>{voice}</voice><staff>{staff}</staff></note>")


def voice_lengths(part, number="1"):
    """How long each voice of a measure is, by the cursor rules the file uses.

    Written out here rather than imported, so the test measures the XML the way
    a reader would rather than the way the code under test does.
    """
    measure = [m for m in part.findall("measure") if m.get("number") == number][0]
    cursor = previous = 0
    ends = {}
    for el in measure:
        if el.tag == "backup":
            cursor -= int(el.findtext("duration"))
        elif el.tag == "forward":
            cursor += int(el.findtext("duration"))
        elif el.tag == "note":
            length = int(el.findtext("duration") or 0)
            voice = el.findtext("voice") or "1"
            if el.find("chord") is not None:
                ends[voice] = max(ends.get(voice, 0), previous + length)
            else:
                assert cursor >= 0, "the cursor went behind the start of the bar"
                ends[voice] = max(ends.get(voice, 0), cursor + length)
                previous, cursor = cursor, cursor + length
    return ends


def test_a_whole_rest_sharing_a_voice_is_moved_out():
    """The rule, on the shape it was written for."""
    part = a_measure(note(16, 5, "whole", rest=True) + note(16, 5, "whole"))
    moved = omr.split_measure_rests(part)

    assert [(m.was, m.now) for m in moved] == [("5", "6")]
    assert voice_lengths(part) == {"5": 16, "6": 16}


def test_the_notes_behind_it_move_back_into_the_room_it_was_taking():
    """The bar has to get shorter, which relabelling the voice alone would not do.

    This is the diagnosed bar's shape: a whole rest in front of a half, a dotted
    eighth and a 16th, 28 divisions of a 16-division bar.
    """
    part = a_measure(note(16, 5, "whole", rest=True) + note(8, 5, "half")
                     + note(3, 5, "eighth") + note(1, 5, "16th"))
    omr.split_measure_rests(part)

    # 12, not 16: the quarter rest homr lost is *not* invented back. A short bar
    # is a better failure than a bar seven quarters long, and it is the reason
    # `scan` writes the move down for somebody to read against the page.
    assert voice_lengths(part) == {"5": 12, "6": 16}


def test_a_whole_rest_alone_in_its_voice_is_left_alone():
    """39 of the benchmark's 41 whole rests are this, and they are not the defect.

    A voice resting through the bar makes no musical claim, and
    `fix_overfull_measures` already re-lengths it to the real bar.
    """
    part = a_measure(note(16, 5, "whole", rest=True)
                     + "<backup><duration>16</duration></backup>"
                     + note(16, 1, "whole", rest=True))
    assert omr.split_measure_rests(part) == []
    assert voice_lengths(part) == {"5": 16, "1": 16}


def test_a_rest_that_is_not_a_whole_rest_is_left_alone():
    """The rule is "a whole-measure rest cannot share a voice", not "a rest"."""
    part = a_measure(note(4, 5, "quarter", rest=True) + note(12, 5, "half"))
    assert omr.split_measure_rests(part) == []


def test_a_second_shared_rest_gets_a_voice_of_its_own_too():
    part = a_measure(note(16, 5, "whole", rest=True) + note(16, 5, "whole")
                     + "<backup><duration>32</duration></backup>"
                     + note(16, 6, "whole", rest=True) + note(16, 6, "whole"))
    moved = omr.split_measure_rests(part)

    assert [(m.was, m.now) for m in moved] == [("5", "7"), ("6", "8")]
    assert voice_lengths(part) == {"5": 16, "6": 16, "7": 16, "8": 16}


def test_the_spare_voice_is_reused_from_one_bar_to_the_next():
    """MuseScore holds four voices to a staff, so a voice per bar is unusable."""
    body = note(16, 1, "whole", rest=True) + note(16, 1, "whole")
    part = etree.fromstring(
        '<part id="P1">'
        f'<measure number="1">{body}</measure>'
        f'<measure number="2">{body}</measure></part>')
    moved = omr.split_measure_rests(part)

    assert [m.now for m in moved] == ["2", "2"]


def test_a_chord_moves_with_the_note_it_is_stacked_on():
    part = a_measure(note(16, 5, "whole", rest=True) + note(8, 5, "half")
                     + note(8, 5, "half", chord=True))
    omr.split_measure_rests(part)

    assert voice_lengths(part) == {"5": 8, "6": 16}


def test_a_measure_with_something_unreadable_among_its_notes_is_left_alone():
    """A direction's place in the stream carries meaning we would be guessing at."""
    part = a_measure(note(16, 5, "whole", rest=True)
                     + "<direction><direction-type><words>rit.</words>"
                     "</direction-type></direction>" + note(16, 5, "whole"))
    assert omr.split_measure_rests(part) == []


def test_the_committed_bar_stops_overfilling():
    """The acceptance, on the real parse — #130's bar, committed beside the test."""
    part = etree.parse(FRAGMENT).getroot().find("part")
    moved = omr.split_measure_rests(part)

    assert [(m.measure, m.staff, m.was, m.now) for m in moved] == [("2", "2", "5", "7")]
    # 4/4 at divisions=4 is 16. Before the move voice 5 held 28.
    assert voice_lengths(part, "2") == {"5": 12, "1": 16, "7": 16}
    # And the bars either side of it are untouched.
    assert voice_lengths(part, "1") == {"1": 16, "5": 16, "6": 12}
    assert voice_lengths(part, "3") == {"1": 16, "2": 16, "5": 16, "6": 16}


def test_a_parse_with_nothing_to_move_comes_back_byte_for_byte(tmp_path):
    path = tmp_path / "page.musicxml"
    original = ('<?xml version="1.0" encoding="UTF-8"?><score-partwise version="4.0">'
                '<part id="P1"><measure number="1"><note><rest/><duration>16</duration>'
                "<type>whole</type><voice>1</voice></note></measure></part>"
                "</score-partwise>")
    path.write_text(original)

    assert omr.split_measure_rests_in(str(path)) == []
    assert path.read_text() == original


def test_moving_one_twice_finds_nothing_the_second_time(tmp_path):
    path = tmp_path / "page.musicxml"
    shutil.copy(FRAGMENT, path)

    assert len(omr.split_measure_rests_in(str(path))) == 1
    assert omr.split_measure_rests_in(str(path)) == []


# --- homr's note positions (#220) ----------------------------------------
#
# Upstream homr writes where each note sits on the image as a comment inside
# every <note> (dda4d2f). The repair at this boundary reads the notes, so it
# must do the same with the comments there as without them.


def with_image_positions(data):
    """``data`` with an ``imgpos`` comment in every note, as homr writes it."""
    out, n = b"", 0
    for piece in data.split(b"</note>")[:-1]:
        out += piece + f"<!-- imgpos: {10 + n}, {200 + n} -->".encode() + b"</note>"
        n += 1
    return out + data.split(b"</note>")[-1]


def test_note_positions_are_not_content():
    data = open(FRAGMENT, "rb").read()
    marked = with_image_positions(data)
    assert marked != data and marked.count(b"imgpos") == data.count(b"</note>")
    assert omr.strip_image_positions(marked) == data
    assert omr.strip_image_positions(data) == data, "nothing to strip is untouched"


def test_shared_rests_move_the_same_with_note_positions(tmp_path):
    plain, marked = tmp_path / "plain.musicxml", tmp_path / "marked.musicxml"
    shutil.copy(FRAGMENT, plain)
    marked.write_bytes(with_image_positions(open(FRAGMENT, "rb").read()))

    moves = [(m.measure, m.staff, m.was, m.now)
             for m in omr.split_measure_rests_in(str(plain))]
    assert moves == [("2", "2", "5", "7")]
    assert [(m.measure, m.staff, m.was, m.now)
            for m in omr.split_measure_rests_in(str(marked))] == moves
    assert (omr.strip_image_positions(marked.read_bytes())
            == plain.read_bytes())


def test_a_page_comes_back_with_its_shared_rests_moved(monkeypatch, tmp_path):
    """The seam: what ``read_page`` hands back has been through the rule.

    And it hands the moves to a caller that asks, because the log is not a
    record — `scan` has to write them into the song's fixes.json.
    """
    monkeypatch.setenv("HOMR_BIN", stub_homr(
        tmp_path, f'cp "{FRAGMENT}" "${{!#%.*}}.musicxml"\n'))

    lines, repairs = [], []
    produced = omr.read_page(a_page(tmp_path), out_dir=str(tmp_path / "out"),
                             log=lines.append, repairs=repairs)

    assert [(r.measure, r.was, r.now) for r in repairs] == [("2", "5", "7")]
    assert voice_lengths(etree.parse(produced).getroot().find("part"), "2") == {
        "5": 12, "1": 16, "7": 16}
    assert any("whole-measure rest" in line for line in lines), lines
