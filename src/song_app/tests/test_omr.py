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
# A homr branch is installed beside the default one rather than over it, so the
# app has to be able to list what is installed and run a named one.


def a_venv(path, branch=None, source="homr[cpu] @ git+.../homr.git@main"):
    """A venv shaped the way scripts/install-homr.sh leaves one."""
    (path / "bin").mkdir(parents=True)
    homr = path / "bin" / "homr"
    homr.write_text("#!/usr/bin/env bash\n")
    homr.chmod(homr.stat().st_mode | stat.S_IEXEC)
    lines = [f"source={source}"] + ([f"branch={branch}"] if branch else [])
    (path / "homr-engine.txt").write_text("\n".join(lines) + "\n")
    return str(homr)


def test_one_install_is_one_engine(monkeypatch, tmp_path):
    monkeypatch.delenv("HOMR_BIN", raising=False)
    binary = a_venv(tmp_path / "homr-venv", branch="main")
    monkeypatch.setattr(omr, "DEFAULT_VENV", str(tmp_path / "homr-venv"))

    engines = omr.engines()
    assert [(e.key, e.label, e.binary, e.default) for e in engines] == [
        ("default", "main", binary, True)]


def test_a_branch_venv_is_offered_beside_the_default(monkeypatch, tmp_path):
    """The label is the branch, not the mangled directory name."""
    monkeypatch.delenv("HOMR_BIN", raising=False)
    a_venv(tmp_path / "homr-venv", branch="main")
    branch = a_venv(tmp_path / "homr-venv-prototype-system-4", branch="prototype/system-4")
    monkeypatch.setattr(omr, "DEFAULT_VENV", str(tmp_path / "homr-venv"))

    engines = omr.engines()
    assert [e.label for e in engines] == ["main", "prototype/system-4"]
    assert [e.default for e in engines] == [True, False]
    assert engines[1].key == "prototype-system-4"
    assert omr.engine_binary("prototype-system-4") == branch


def test_a_venv_with_no_homr_in_it_is_not_an_engine(monkeypatch, tmp_path):
    """A half-built venv must not be offered as something to scan with."""
    monkeypatch.delenv("HOMR_BIN", raising=False)
    a_venv(tmp_path / "homr-venv", branch="main")
    (tmp_path / "homr-venv-broken" / "bin").mkdir(parents=True)
    monkeypatch.setattr(omr, "DEFAULT_VENV", str(tmp_path / "homr-venv"))

    assert [e.key for e in omr.engines()] == ["default"]


def test_an_unlabelled_engine_falls_back_to_its_source(monkeypatch, tmp_path):
    monkeypatch.delenv("HOMR_BIN", raising=False)
    a_venv(tmp_path / "homr-venv", branch="main")
    a_venv(tmp_path / "homr-venv-pinned", source="homr==9.9.9")
    monkeypatch.setattr(omr, "DEFAULT_VENV", str(tmp_path / "homr-venv"))

    assert [e.label for e in omr.engines()][1] == "homr==9.9.9"


def test_no_key_means_the_default_engine(monkeypatch, tmp_path):
    binary = a_venv(tmp_path / "homr-venv", branch="main")
    monkeypatch.setenv("HOMR_BIN", binary)
    assert omr.engine_binary(None) == binary
    assert omr.engine_binary("default") == binary


def test_an_engine_that_is_not_installed_is_refused(monkeypatch, tmp_path):
    """Not silently the default: a parse nobody can account for is worse.

    The whole point of picking an engine is to know which homr read the page.
    """
    monkeypatch.delenv("HOMR_BIN", raising=False)
    a_venv(tmp_path / "homr-venv", branch="main")
    monkeypatch.setattr(omr, "DEFAULT_VENV", str(tmp_path / "homr-venv"))

    with pytest.raises(omr.HomrMissing) as caught:
        omr.engine_binary("prototype-system-4")
    assert "prototype-system-4" in str(caught.value)


def test_a_named_binary_is_what_reads_the_page(monkeypatch, tmp_path):
    """The picked engine runs, and the default one is not consulted."""
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("HOMR_BIN", stub_homr(tmp_path, "exit 1\n"))
    chosen = stub_homr(other, 'echo "<picked/>" > "${!#%.*}.musicxml"\n')

    out = omr.read_page(a_page(tmp_path), out_dir=str(tmp_path / "out"),
                        binary=chosen, queue=False)
    assert "<picked/>" in open(out).read()


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


# --- slurs ---------------------------------------------------------------
#
# homr writes ``slurStart`` / ``slurStop`` one note at a time and never pairs
# them, and the ``number`` they would pair by is the staff number, the same for
# every slur on the staff. These read as little token streams for that reason:
# ``"1( 1) 3( 5)"`` is a start and a stop in bar 1 and a slur from bar 3 to bar
# 5, which is the level the defect lives at.


def a_slurred_part(stream, bars=8, per_bar=4):
    """A one-staff part whose slur tokens are ``"1( 2) ..."`` -- bar and end."""
    wanted = {}
    for token in stream.split():
        wanted.setdefault(int(token[:-1]), []).append(
            "start" if token[-1] == "(" else "stop")

    measures = []
    for bar in range(1, bars + 1):
        attributes = ("<attributes><divisions>1</divisions>"
                      "<key><fifths>0</fifths></key>"
                      "<time><beats>4</beats><beat-type>4</beat-type></time>"
                      "<clef><sign>G</sign><line>2</line></clef></attributes>"
                      if bar == 1 else "")
        notes = ""
        for n in range(per_bar):
            slurs = ""
            here = wanted.get(bar, [])
            # One token per note, in the order they were written.
            if n < len(here):
                slurs = f'<notations><slur type="{here[n]}" number="1"/></notations>'
            notes += ("<note><pitch><step>C</step><octave>4</octave></pitch>"
                      f"<duration>1</duration><type>quarter</type>{slurs}</note>")
        measures.append(f'<measure number="{bar}">{attributes}{notes}</measure>')
    return etree.fromstring(f'<part id="P1">{"".join(measures)}</part>')


def slur_pairs(part):
    """The pairs left in a part, as ``(start bar, stop bar)``, in order."""
    seen, open_bars = [], []
    for bar, measure in enumerate(part.findall("measure"), 1):
        for slur in measure.findall("note/notations/slur"):
            if slur.get("type") == "start":
                open_bars.append(bar)
            else:
                seen.append((open_bars.pop(), bar))
    assert not open_bars, "a start was left open"
    return seen


def test_a_slur_inside_one_bar_is_kept():
    part = a_slurred_part("1( 1)")
    assert omr.resolve_slurs(part) == 0
    assert slur_pairs(part) == [(1, 1)]


def test_a_melisma_across_one_barline_is_kept():
    """``il-man il-ki-rii-vi-`` is the worked example in the lyric tests: a
    word whose syllables span a barline is real music, and the rule must not
    eat it. One barline is the most any slur in the benchmark crosses."""
    part = a_slurred_part("1( 2)")
    assert omr.resolve_slurs(part) == 0
    assert slur_pairs(part) == [(1, 2)]


def test_a_slur_across_two_barlines_is_dropped():
    part = a_slurred_part("1( 3)")
    assert omr.resolve_slurs(part) == 1
    assert slur_pairs(part) == []


def test_a_runaway_does_not_take_the_slurs_around_it_with_it():
    part = a_slurred_part("1( 1) 2( 6) 7( 7)")
    assert omr.resolve_slurs(part) == 1
    assert slur_pairs(part) == [(1, 1), (7, 7)]


def test_a_start_made_while_one_is_open_goes():
    """MuseScore keeps the first and discards the second, so this changes
    nothing about how the file reads -- and it is what stops a removed runaway
    promoting the leftover start onto a stop further away still."""
    part = a_slurred_part("1( 1( 2)")
    assert omr.resolve_slurs(part) == 0
    assert slur_pairs(part) == [(1, 2)]


def test_a_stop_that_closes_nothing_goes():
    """#112 measured a lone dangler as cosmetic, and in isolation it is. In a
    stream it is not: four bars through the MuseScore CLI with one unmatched
    stop lose every later slur of that number too."""
    part = a_slurred_part("1) 2( 2)")
    assert omr.resolve_slurs(part) == 0
    assert slur_pairs(part) == [(2, 2)]


def test_a_start_that_never_stops_goes():
    part = a_slurred_part("1( 1) 3(")
    assert omr.resolve_slurs(part) == 0
    assert slur_pairs(part) == [(1, 1)]


def test_the_b5_shape_leaves_one_alternating_stream():
    """B5's own m46 region: five starts and one stop, then music that is fine.

    Resolving it once has to settle it -- running again must find nothing, or
    the fix is a cascade rather than a repair. Removing the runaway pairs alone
    left the real B5 with a fresh 2-bar runaway at m51.
    """
    part = a_slurred_part("1( 1( 2( 3( 3) 4( 4) 5( 6) 7( 7)", bars=8)
    assert omr.resolve_slurs(part) == 1
    assert slur_pairs(part) == [(4, 4), (5, 6), (7, 7)]
    assert omr.resolve_slurs(part) == 0


def test_an_empty_notations_element_does_not_survive_its_slur():
    part = a_slurred_part("1( 3)")
    omr.resolve_slurs(part)
    assert part.findall(".//notations") == []


def _musescore():
    import dotenv
    dotenv.load_dotenv(".env")
    return os.getenv("MUSESCORE_CLI_PATH")


def test_a_page_comes_back_with_its_slurs_resolved(monkeypatch, tmp_path):
    """The seam: what ``read_page`` hands back has been through the rule.

    A whole page and one cropped system both come through here, which is the
    reason it lives at this boundary rather than in the assembler -- the
    ``number`` the mis-pairing turns on is the staff number, and nothing about
    that is per-crop.
    """
    part = etree.tostring(a_slurred_part("1( 1) 2( 6) 7( 7)"), encoding="unicode")
    score = ('<?xml version="1.0" encoding="UTF-8"?><score-partwise version="4.0">'
             '<part-list><score-part id="P1"><part-name>V</part-name></score-part>'
             f'</part-list>{part}</score-partwise>')
    written = tmp_path / "homr-said.musicxml"
    written.write_text(score)
    monkeypatch.setenv("HOMR_BIN", stub_homr(
        tmp_path, f'cp "{written}" "${{!#%.*}}.musicxml"\n'))

    lines = []
    produced = omr.read_page(a_page(tmp_path), out_dir=str(tmp_path / "out"),
                             log=lines.append)

    assert slur_pairs(etree.parse(produced).getroot().find("part")) == [(1, 1), (7, 7)]
    assert any("slur" in line for line in lines), lines


def test_a_page_homr_got_right_is_not_rewritten(monkeypatch, tmp_path):
    monkeypatch.setenv("HOMR_BIN", stub_homr(
        tmp_path, 'echo "<score-partwise/>" > "${!#%.*}.musicxml"\n'))
    produced = omr.read_page(a_page(tmp_path), out_dir=str(tmp_path / "out"))
    assert open(produced).read() == "<score-partwise/>\n"


@pytest.mark.skipif(not _musescore() or not os.path.exists(_musescore() or ""),
                    reason="needs the MuseScore CLI")
def test_a_runaway_slur_swallows_syllable_slots_and_the_rule_gives_them_back(tmp_path):
    """The defect and the repair, measured where they are felt.

    A slur continuation takes no syllable, so a slur nobody engraved is a lyric
    line that will not fit. This is B5's m46 in miniature: twelve notes over
    three bars, a start in the first and an unrelated stop in the third.
    """
    from src.clean_score.lyric_txt import slot_counts

    def slots(part, name):
        path = tmp_path / f"{name}.musicxml"
        path.write_text(
            '<?xml version="1.0" encoding="UTF-8"?><score-partwise version="4.0">'
            '<part-list><score-part id="P1"><part-name>V</part-name></score-part>'
            f'</part-list>{etree.tostring(part, encoding="unicode")}</score-partwise>')
        mscx = str(tmp_path / f"{name}.mscx")
        subprocess.run([_musescore(), "-o", mscx, str(path)],
                       check=True, capture_output=True, timeout=300)
        counts = slot_counts(etree.parse(mscx).getroot())
        return sum(sum(bars.values()) for bars in counts.values())

    assert slots(a_slurred_part("1( 3)", bars=3), "runaway") == 4
    resolved = a_slurred_part("1( 3)", bars=3)
    assert omr.resolve_slurs(resolved) == 1
    assert slots(resolved, "resolved") == 12
