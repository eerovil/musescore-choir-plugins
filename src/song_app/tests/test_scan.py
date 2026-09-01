"""The scan stage: the orchestration, the holes, and the one invalidation rule.

Nothing here runs homr or poppler. Cropping and reading are the two things this
module does not own -- :mod:`omr_systems` owns reading and #103 pinned it -- so
they are stubbed, and what is left under test is exactly what this stage adds:
which bands are cropped and how wide, what happens when one of them cannot be
read, and what stops being true when an input moves.

Flattening and assembling are *not* stubbed. They are cheap, they need no binary,
and stubbing them would leave the seam between a fragment on disk and an
assembled score untested, which is where a hole would go unnoticed.
"""

import json
import os

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from src.clean_score.utils import per_system
from src.song_app import heavy_slot, omr, omr_systems, pdf_systems, scan, server, state


# --- a stub score, small enough to read -----------------------------------

def _fragment_xml(staves: int, bars: int, note: str = "C") -> str:
    """One system's MusicXML as homr would leave it: bars numbered from 1."""
    parts = "".join(f'<score-part id="P{n}"><part-name>Voice</part-name></score-part>'
                    for n in range(1, staves + 1))
    body = ""
    for n in range(1, staves + 1):
        measures = ""
        for bar in range(1, bars + 1):
            attrs = ('<attributes><divisions>1</divisions>'
                     '<key><fifths>0</fifths></key>'
                     '<time><beats>4</beats><beat-type>4</beat-type></time>'
                     '<clef><sign>G</sign><line>2</line></clef></attributes>'
                     if bar == 1 else "")
            measures += (
                f'<measure number="{bar}">{attrs}'
                f'<note><pitch><step>{note}</step><octave>4</octave></pitch>'
                f'<duration>4</duration><voice>1</voice></note></measure>'
            )
        body += f'<part id="P{n}">{measures}</part>'
    return ('<?xml version="1.0" encoding="UTF-8"?><score-partwise version="4.0">'
            f'<part-list>{parts}</part-list>{body}</score-partwise>')


class Reader:
    """Stands in for cropping a band and reading it.

    Records every band it was handed, so a test can assert what was cropped and
    how wide, and can be told to fail for named systems.
    """

    def __init__(self, staves=2, bars=2):
        self.cropped = []
        self.read = []
        self.fail = {}
        self.staves, self.bars = staves, bars

    def crop(self, pdf_path, bounds, out_dir, dpi=400):
        os.makedirs(out_dir, exist_ok=True)
        images = []
        for band in bounds:
            self.cropped.append(band)
            path = os.path.join(out_dir, f"system-{band.index:02d}-{band.top:.4f}.png")
            open(path, "wb").close()
            images.append(pdf_systems.SystemImage(bounds=band, path=path))
        return images

    def read_system(self, image, out_dir, log=None, queue=True):
        self.read.append(image.index)
        boom = self.fail.get(image.index)
        if boom:
            raise boom
        out = os.path.join(out_dir, f"system-{image.index:02d}.musicxml")
        with open(out, "w", encoding="utf-8") as f:
            f.write(_fragment_xml(self.staves, self.bars))
        return omr_systems.SystemScan(index=image.index, musicxml=out,
                                      staves=omr_systems.flatten(out))


@pytest.fixture
def reader(monkeypatch):
    r = Reader()
    monkeypatch.setattr(pdf_systems, "crop_systems", r.crop)
    monkeypatch.setattr(omr_systems, "read_system", r.read_system)
    return r


@pytest.fixture
def songs(tmp_path, monkeypatch):
    d = tmp_path / "songs"
    d.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(d))
    return d


def _song(songs, bands=3, pdf=True):
    song = state.create("Test Song", per_system=True)
    if pdf:
        with open(song.path("scan.pdf"), "wb") as f:
            f.write(b"%PDF-1.4 not really a pdf\n")
        song.data["sources"]["pdf"] = "scan.pdf"
    song.set_stage("scan")
    song.save()
    _bands(song, bands)
    return song


def _bands(song, count, shift=0.0):
    pdf_systems.save_bounds(song.dir, [
        pdf_systems.SystemBounds(index=i, page=1,
                                 top=0.1 * i + shift, bottom=0.1 * i + 0.08 + shift)
        for i in range(1, count + 1)
    ])


def _reload(song):
    return state.load(song.slug)


# --- the stage ------------------------------------------------------------


def test_scan_reads_every_band_and_assembles_one_score(songs, reader):
    song = _song(songs)
    result = scan.run(song)

    assert reader.read == [1, 2, 3]
    assert result["complete"] and result["holes"] == []
    fresh = _reload(song)
    # Complete is not approved: a finished scan is a score the app has, not one
    # anybody has looked at, and only `approve` moves a song off this stage.
    assert fresh.stage == "scan"
    assert not result["approved"]
    assert fresh.data["sources"]["xml"] == scan.ASSEMBLED_NAME
    assert os.path.isfile(fresh.path(scan.ASSEMBLED_NAME))
    # Fragments are kept: the assembled score is derived from them, so they are
    # what a re-run reads instead of asking homr again.
    for index in (1, 2, 3):
        assert os.path.isfile(fresh.path(fresh.data["scan"]["systems"][str(index)]["musicxml"]))


def test_the_assembled_score_holds_every_system_end_to_end(songs, reader):
    reader.bars = 4
    song = _song(songs, bands=3)
    scan.run(song)
    from lxml import etree

    root = etree.parse(_reload(song).path(scan.ASSEMBLED_NAME)).getroot()
    assert len(root.findall("part")) == 2                    # one per staff column
    assert len(root.findall("part")[0].findall("measure")) == 12   # 3 systems x 4 bars
    # Bars are numbered continuously rather than restarting in every system.
    numbers = [m.get("number") for m in root.findall("part")[0].findall("measure")]
    assert numbers == [str(n) for n in range(1, 13)]


def test_the_band_is_padded_before_it_is_cropped(songs, reader):
    song = _song(songs, bands=1)
    scan.run(song)

    printed = pdf_systems.load_bounds(song.dir)[0]
    cropped = reader.cropped[0]
    assert cropped.top == pytest.approx(printed.top - scan.PAD)
    assert cropped.bottom == pytest.approx(printed.bottom + scan.PAD)
    assert scan.PAD > 0, "a tight crop cuts the slur arcs off (#112)"


def test_padding_never_runs_off_the_page(songs, reader):
    song = _song(songs, bands=0)
    pdf_systems.save_bounds(song.dir, [
        pdf_systems.SystemBounds(index=1, page=1, top=0.0, bottom=1.0)])
    scan.run(song)
    assert (reader.cropped[0].top, reader.cropped[0].bottom) == (0.0, 1.0)


# --- a hole ---------------------------------------------------------------


def test_a_failed_system_is_a_hole_not_a_failed_song(songs, reader):
    song = _song(songs)
    reader.fail[2] = omr.HomrError("homr fell over")
    result = scan.run(song)

    assert reader.read == [1, 2, 3], "a failure must not stop the systems after it"
    assert result["holes"] == [2] and result["read"] == 2
    fresh = _reload(song)
    assert fresh.stage == "scan", "the song cannot leave scan with a hole open"
    assert not os.path.exists(fresh.path(scan.ASSEMBLED_NAME))
    assert "homr fell over" in fresh.data["scan"]["systems"]["2"]["error"]


def test_a_lost_lease_costs_only_the_system_in_flight(songs, reader):
    song = _song(songs)
    reader.fail[2] = heavy_slot.SlotLost("the slot went to somebody else")
    result = scan.run(song)

    # Each band takes its own slot, so band 3 asking for a fresh one queues
    # behind whoever the cores went to rather than competing with them.
    assert reader.read == [1, 2, 3]
    assert result["holes"] == [2]
    assert result["read"] == 2


def test_filling_the_hole_reads_only_the_hole_and_then_assembles(songs, reader):
    song = _song(songs)
    reader.fail[2] = omr.HomrError("homr fell over")
    scan.run(song)

    reader.fail.clear()
    reader.read.clear()
    result = scan.run(_reload(song))

    assert reader.read == [2], "a band already read at its current geometry is not re-read"
    assert result["complete"]
    assert _reload(song).stage == "scan", "filling a hole does not approve the scan"


def test_a_song_with_no_bounds_refuses_rather_than_guessing(songs, reader):
    song = _song(songs, bands=0)
    with pytest.raises(scan.ScanError, match="boundaries"):
        scan.run(song)


# --- the invalidation rule ------------------------------------------------


def _answer(song, *indices):
    """Answer the grid for the named systems, the way the route does."""
    source = song.path(song.data["scan"]["assembled"])
    per_system.save_answers(source, {i: {1: "T1", 2: "T2"} for i in indices})
    scan.stamp_answers(song, indices)
    song.save()


def test_a_bounds_edit_discards_only_the_fragments_whose_band_moved(songs, reader, tmp_path):
    with per_system.use_answer_file(str(tmp_path / "answers.json")):
        song = _song(songs)
        scan.run(song)
        song = _reload(song)
        _answer(song, 1, 2, 3)

        bands = pdf_systems.load_bounds(song.dir)
        moved = [b if b.index != 2 else pdf_systems.SystemBounds(
            index=2, page=1, top=b.top + 0.01, bottom=b.bottom) for b in bands]
        pdf_systems.save_bounds(song.dir, moved)

        dropped = scan.reconcile(song)

    assert "the scan of system 2" in dropped
    assert "the grid answers for system 2" in dropped
    assert "the scan of system 1" not in dropped
    assert "the grid answers for system 3" not in dropped
    assert scan.status(song)["holes"] == [2]
    assert song.stage == "scan", "the assembled score is short a system now"


def test_an_inserted_band_repoints_everything_after_it(songs, reader, tmp_path):
    """The dangerous case: both bounds and answers are keyed by position.

    Inserting a band at the top makes what used to be system 1 into system 2, so
    every fragment and every answer after the insertion point is now filed against
    a band it was not read from. Nothing here compares indices — each fragment is
    checked against the geometry that is at its own index now, which is what makes
    a silent re-pointing loud.
    """
    with per_system.use_answer_file(str(tmp_path / "answers.json")):
        song = _song(songs, bands=2)
        scan.run(song)
        song = _reload(song)
        _answer(song, 1, 2)

        old = pdf_systems.load_bounds(song.dir)
        pdf_systems.save_bounds(song.dir, [
            pdf_systems.SystemBounds(index=1, page=1, top=0.02, bottom=0.06),
            pdf_systems.SystemBounds(index=2, page=1, top=old[0].top, bottom=old[0].bottom),
            pdf_systems.SystemBounds(index=3, page=1, top=old[1].top, bottom=old[1].bottom),
        ])
        dropped = scan.reconcile(song)
        remaining = per_system.saved_answers(song.path(scan.ASSEMBLED_NAME)) or {}

    assert "the scan of system 1" in dropped and "the scan of system 2" in dropped
    assert "the grid answers for system 1" in dropped
    assert "the grid answers for system 2" in dropped
    assert remaining == {}, "an answer about staves that moved is worse than no answer"
    assert scan.status(song)["holes"] == [1, 2, 3]


def test_re_reading_a_system_differently_drops_that_system_s_grid_answers(
        songs, reader, tmp_path):
    with per_system.use_answer_file(str(tmp_path / "answers.json")):
        song = _song(songs)
        scan.run(song)
        song = _reload(song)
        _answer(song, 1, 2, 3)

        # The dangerous case is the same staff count in a different order: the
        # grid still reads as answered and every answer points at the wrong staff.
        reader.staves = 3
        scan.run(song, only=[2])
        remaining = per_system.saved_answers(song.path(scan.ASSEMBLED_NAME)) or {}

    assert sorted(remaining) == [1, 3]
    assert "2" not in _reload(song).data["scan"].get("answered_against", {})


def test_re_reading_a_system_to_the_same_answer_changes_nothing(songs, reader, tmp_path):
    """A fragment carries what it was read *from* and what came *back*.

    Nothing downstream of a re-read is discarded unless the reading itself came
    out different, because that is the only case in which anything derived from
    it was wrong. Clearing on the act of re-reading rather than on its result
    would throw away a person's work to no purpose.
    """
    with per_system.use_answer_file(str(tmp_path / "answers.json")):
        song = _song(songs)
        scan.run(song)
        song = _reload(song)
        _answer(song, 1, 2, 3)
        song.data["review"] = {"approved_against": "sha1:whatever",
                               "scan_revision": scan.revision(song)}
        song.save()

        scan.run(_reload(song), only=[2])
        song = _reload(song)
        remaining = per_system.saved_answers(song.path(scan.ASSEMBLED_NAME)) or {}

    assert sorted(remaining) == [1, 2, 3]
    assert song.data["review"]["approved_against"] == "sha1:whatever"


def test_a_re_scan_that_reads_differently_clears_the_song_s_explicit_ok(songs, reader):
    song = _song(songs)
    scan.run(song)
    song = _reload(song)
    song.data["review"] = {"approved_against": "sha1:whatever",
                           "scan_revision": scan.revision(song)}
    song.save()

    reader.bars = 5
    scan.run(_reload(song), only=[3])

    assert "review" not in _reload(song).data, \
        "nobody has looked at what would be recorded now"


def test_a_song_that_never_scanned_derives_nothing_from_any_of_this(songs, reader):
    song = state.create("Legacy", per_system=False)
    song.data["sources"]["xml"] = "legacy.mscx"
    song.data["review"] = {"approved_against": "sha1:whatever"}
    song.set_stage("record")
    song.save()

    assert scan.reconcile(song) == []
    assert song.data["review"] == {"approved_against": "sha1:whatever"}
    assert song.stage == "record"


# --- the stage machine and the routes -------------------------------------


@pytest.fixture
def client(songs):
    return TestClient(server.app)


def test_a_pdf_alone_is_a_song_and_starts_at_scan(client, songs):
    r = client.post("/api/songs", data={"name": "Only A Scan"},
                    files={"pdf": ("page.pdf", b"%PDF-1.4\n", "application/pdf")})
    assert r.status_code == 200
    song = state.load(r.json()["slug"])
    assert song.stage == "scan"
    assert song.data["sources"] == {"pdf": "page.pdf"}


def test_a_score_still_starts_at_clean(client, songs):
    r = client.post("/api/songs", data={"name": "With A Score"},
                    files={"xml": ("s.musicxml", b"<score/>", "text/xml")})
    assert r.status_code == 200
    assert state.load(r.json()["slug"]).stage == "clean"


def test_neither_a_score_nor_a_pdf_is_refused(client, songs):
    r = client.post("/api/songs", data={"name": "Nothing"})
    assert r.status_code == 400


def test_a_song_that_has_a_score_reads_as_past_scanning(client, songs):
    """The 48 existing songs must not grow a stage they never went through."""
    r = client.post("/api/songs", data={"name": "With A Score"},
                    files={"xml": ("s.musicxml", b"<score/>", "text/xml")})
    body = client.get(f"/api/songs/{r.json()['slug']}").json()

    assert "scan" in body["stages"]
    # The rail marks every stage below the current index as done, so being at
    # `clean` is what makes `scan` read as satisfied. Nothing is recorded to say
    # so, and nothing needs to be.
    assert body["stages"].index("scan") < body["stage_index"]


def test_a_legacy_folder_with_a_score_is_imported_past_scanning(songs):
    folder = songs / "legacy"
    folder.mkdir()
    (folder / "legacy.musicxml").write_text("<score/>")
    (folder / "legacy.pdf").write_bytes(b"%PDF-1.4\n")
    assert server.import_legacy() == 1
    assert state.load("legacy").stage == "clean"


def test_a_legacy_folder_with_only_a_pdf_is_imported_at_scan(songs):
    folder = songs / "just-a-pdf"
    folder.mkdir()
    (folder / "page.pdf").write_bytes(b"%PDF-1.4\n")
    assert server.import_legacy() == 1
    assert state.load("just-a-pdf").stage == "scan"


def test_a_second_scan_cannot_start_over_the_first(client, songs, reader):
    song = _song(songs)
    with open(server._scan_lock_path(song), "w") as f:
        f.write(str(os.getpid()))
    r = client.post(f"/api/songs/{song.slug}/scan")
    assert r.status_code == 409


def test_scanning_without_bounds_is_refused_at_the_door(client, songs, reader):
    song = _song(songs, bands=0)
    r = client.post(f"/api/songs/{song.slug}/scan")
    assert r.status_code == 400 and "boundaries" in r.json()["detail"]


def test_reading_a_song_is_where_it_finds_out_what_stopped_being_true(client, songs, reader):
    song = _song(songs)
    scan.run(song)
    _bands(_reload(song), 3, shift=0.02)      # every band moved

    body = client.get(f"/api/songs/{song.slug}").json()

    assert body["scan_discarded"], "a stale fragment must be reported, not quietly kept"
    assert body["scan_status"]["holes"] == [1, 2, 3]
    assert body["scan_status"]["complete"] is False


def test_saving_bounds_says_what_it_threw_away(client, songs, reader):
    song = _song(songs)
    scan.run(song)
    bands = [b.to_dict() for b in pdf_systems.load_bounds(song.dir)]
    bands[1]["top"] += 0.01

    r = client.put(f"/api/songs/{song.slug}/bounds", json={"systems": bands})

    assert r.status_code == 200
    assert "the scan of system 2" in r.json()["discarded"]


# --- the gate: one explicit OK per song ------------------------------------
#
# The scan stage is the one stage that must not advance on its own. Everything
# below is about that: what the OK records, what lapses it, and what it says
# afterwards about where to look.


def test_a_finished_scan_waits_for_a_person_and_then_moves(songs, reader):
    song = _song(songs)
    scan.run(song)
    song = _reload(song)
    assert song.stage == "scan"

    result = scan.approve(song)

    assert result["approved"] is True
    assert _reload(song).stage == "clean"
    assert song.data["scan"]["ok"]["revision"] == scan.revision(song)


def test_there_is_nothing_to_approve_while_a_system_is_a_hole(songs, reader):
    song = _song(songs)
    reader.fail[2] = omr.HomrError("homr fell over")
    scan.run(song)
    song = _reload(song)

    with pytest.raises(scan.ScanError, match="2"):
        scan.approve(song)
    assert song.stage == "scan"


def test_re_reading_a_system_lapses_the_ok_and_says_which_one(songs, reader):
    song = _song(songs)
    scan.run(song)
    song = _reload(song)
    scan.approve(song)

    # The same band, read again and coming back different: this is the case the
    # OK exists for, since what was approved is no longer what would be cleaned.
    reader.staves = 3
    scan.run(_reload(song), only=[2])
    st = scan.status(_reload(song))

    assert st["approved"] is False
    assert st["ever_approved"] is True
    assert st["new_since_ok"] == [2]
    assert _reload(song).stage == "scan", "a lapsed OK puts the song back on Scan"


def test_a_re_read_that_came_out_the_same_costs_the_operator_nothing(songs, reader):
    song = _song(songs)
    scan.run(song)
    song = _reload(song)
    scan.approve(song)

    scan.run(_reload(song), only=[2])
    st = scan.status(_reload(song))

    assert st["approved"] is True and st["new_since_ok"] == []
    assert _reload(song).stage == "clean"


def test_a_page_with_no_bands_on_it_is_refused_rather_than_left_out(songs, reader, monkeypatch):
    song = _song(songs, bands=2)                 # every band is on page 1
    monkeypatch.setattr(pdf_systems, "page_count", lambda path: 3)

    assert scan.pages_without_bands(song) == [2, 3]
    with pytest.raises(scan.ScanError, match="2, 3"):
        scan.run(song)
    assert reader.read == [], "nothing is read while a page is unmarked"


def test_no_poppler_is_not_the_same_as_no_bands(songs, reader, monkeypatch):
    """A missing binary must not read as an operator who has not drawn them."""
    def boom(path):
        raise RuntimeError("pdfinfo: not found")

    monkeypatch.setattr(pdf_systems, "page_count", boom)
    song = _song(songs, bands=2)

    assert scan.pages_without_bands(song) == []
    scan.run(song)
    assert reader.read == [1, 2]


def test_the_ok_route_advances_the_song(client, songs, reader):
    song = _song(songs)
    scan.run(song)
    revision = scan.status(_reload(song))["revision"]

    r = client.post(f"/api/songs/{song.slug}/approve-scan", json={"revision": revision})

    assert r.status_code == 200
    assert r.json()["stage"] == "clean"
    assert r.json()["scan_status"]["approved"] is True


def test_the_ok_route_refuses_a_click_aimed_at_an_older_reading(client, songs, reader):
    song = _song(songs)
    scan.run(song)

    r = client.post(f"/api/songs/{song.slug}/approve-scan",
                    json={"revision": "not-what-is-on-disk"})

    assert r.status_code == 409
    assert _reload(song).stage == "scan"


def test_the_ok_route_refuses_an_unfinished_scan(client, songs, reader):
    song = _song(songs)
    reader.fail[2] = omr.HomrError("homr fell over")
    scan.run(song)

    r = client.post(f"/api/songs/{song.slug}/approve-scan")

    assert r.status_code == 400
    assert _reload(song).stage == "scan"


def test_scanning_a_song_whose_pages_are_not_all_marked_is_refused_at_the_door(
        client, songs, reader, monkeypatch):
    song = _song(songs, bands=2)
    monkeypatch.setattr(pdf_systems, "page_count", lambda path: 2)

    r = client.post(f"/api/songs/{song.slug}/scan")

    assert r.status_code == 400 and "Page(s) 2" in r.json()["detail"]


# --- the parse as a picture, beside the band it was read from --------------


def test_a_scanned_system_is_rendered_from_its_own_fragment(client, songs, reader,
                                                            monkeypatch):
    song = _song(songs)
    reader.fail[2] = omr.HomrError("homr fell over")
    scan.run(song)
    rendered = []

    def fake_render(song_dir, musicxml, dpi=200):
        rendered.append((musicxml, dpi))
        out = os.path.join(song_dir, "rendered.png")
        open(out, "wb").close()
        return out

    monkeypatch.setattr(server.pipeline, "scan_system_render", fake_render)

    assert client.get(f"/api/songs/{song.slug}/scan-system/1").status_code == 200
    assert rendered[0][0].endswith("system-01.musicxml")
    # The hole has no fragment, so there is nothing to render and the comparison
    # shows the reason instead of a picture of the system before it.
    assert client.get(f"/api/songs/{song.slug}/scan-system/2").status_code == 404
