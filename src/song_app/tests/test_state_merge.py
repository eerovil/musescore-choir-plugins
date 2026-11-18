"""A save keeps what another writer saved meanwhile (#498).

A clean loads `.song.json`, works for minutes, then saved its whole copy back,
which erased anything saved meanwhile — such as the publish record `sing_song.py
--publish` writes from another process. Now `song_lock` is held across processes
and a save writes only the top-level records this copy changed (with `publish`
and `publish.sites` merged a key deeper), keeping the rest as it is on disk.
"""
import json
import os
import subprocess
import sys
import threading

import pytest

from src.song_app import clean_backup, pipeline, publish, scan, server, state, verification
from src.song_app.tests.test_scan import Reader, _song
from src.song_app import omr_systems, pdf_systems

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

RECORD = {"prefix": "songs/x/v2", "version": "v2", "singer_version": 3}


@pytest.fixture
def songs(tmp_path, monkeypatch):
    d = tmp_path / "songs"
    d.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(d))
    return d


def _disk(song):
    with open(song.state_path(), encoding="utf-8") as f:
        return json.load(f)


def _sing_publishes(slug, choir="jm", record=RECORD):
    """What `sing_song.py --publish` does at its end (src/sing_song/main.py)."""
    with state.song_lock(slug):
        other = state.load(slug)
        pub = other.data.setdefault("publish", {})
        pub.setdefault("sites", {})[choir] = dict(record)
        other.save()


# --- the jobs the issue names -----------------------------------------------


def test_a_clean_keeps_a_publish_record_written_while_it_ran(songs, monkeypatch):
    song = state.create("Kettu", per_system=False)
    song.data["publish"] = {"bpm": 90}
    song.save()
    slug = song.slug

    def fake_clean(xml, song_dir, per_system=False, log=None, voicing=None, check=None):
        # The minutes a clean takes: the song is published and sung meanwhile.
        _sing_publishes(slug)
        cleaned = os.path.join(song_dir, "kettu_cleaned.mscx")
        source = os.path.join(song_dir, "kettu.mscx")
        for path in (cleaned, source):
            with open(path, "w", encoding="utf-8") as f:
                f.write("<museScore/>")
        return cleaned, source

    monkeypatch.setattr(pipeline, "run_clean", fake_clean)
    monkeypatch.setattr(clean_backup, "stash", lambda song: None)
    monkeypatch.setattr(verification, "compare_notes", lambda a, b: {"ok": True})
    monkeypatch.setattr(server, "_health_scan", lambda song, cleaned: [])
    monkeypatch.setattr(server, "_label_bounds", lambda song, path: None)

    server._run_clean(slug)

    disk = _disk(song)
    assert disk["cleaned"] == "kettu_cleaned.mscx"          # the clean's own work
    assert disk["stage"] == "fix"
    assert disk["publish"]["sites"]["jm"] == RECORD          # the record it used to erase
    assert disk["publish"]["bpm"] == 90


def test_a_scan_band_save_keeps_a_publish_record_written_meanwhile(songs, monkeypatch):
    reader = Reader()
    monkeypatch.setattr(pdf_systems, "crop_systems", reader.crop)
    song = _song(songs)

    def read_and_publish(image, *args, **kwargs):
        if image.index == 2:
            _sing_publishes(song.slug)
        return reader.read_system(image, *args, **kwargs)

    monkeypatch.setattr(omr_systems, "read_system", read_and_publish)
    scan.run(song)

    disk = _disk(song)
    assert sorted(disk["scan"]["systems"]) == ["1", "2", "3"]
    assert disk["publish"]["sites"]["jm"] == RECORD


def test_a_health_check_saved_during_a_clean_ends_as_the_clean_s_whole(songs):
    song = state.create("Kettu", per_system=False)
    song.data["health"] = {"checked_against": "A", "issues": [{"id": "a"}]}
    song.save()
    clean = state.load(song.slug)

    watcher = state.load(song.slug)
    watcher.data["health"] = {"checked_against": "B", "issues": [{"id": "b"}]}
    watcher.save()

    clean.data["health"] = {"checked_against": "C", "issues": []}
    clean.save()
    # Written whole, not stitched from the two: a record nobody wrote is worse
    # than either.
    assert _disk(song)["health"] == {"checked_against": "C", "issues": []}


def test_panel_settings_and_a_cli_s_sites_both_survive(songs):
    song = state.create("Kettu", per_system=False)
    song.data["publish"] = {"bpm": 80, "sites": {"joy": {"prefix": "p1"}}}
    song.save()
    panel = state.load(song.slug)

    _sing_publishes(song.slug, "jm")
    panel.data["publish"]["bpm"] = 96
    panel.data["publish"]["sites"]["joy"] = {"prefix": "p2"}
    panel.save()

    pub = _disk(song)["publish"]
    assert pub["bpm"] == 96
    assert pub["sites"] == {"joy": {"prefix": "p2"}, "jm": RECORD}


def test_removals_this_copy_made_are_written(songs):
    song = state.create("Kettu", per_system=False)
    song.data["publish"] = {"bpm": 80, "site": {"prefix": "old"},
                            "sites": {"joy": {"prefix": "p1"}, "jm": {"prefix": "p2"}}}
    song.data["approval"] = {"by": "Eero"}
    song.save()
    mine = state.load(song.slug)

    other = state.load(song.slug)
    other.data["lyrics"] = {"ok": True}
    other.save()

    del mine.data["approval"]
    mine.data["publish"].pop("site")
    del mine.data["publish"]["sites"]["jm"]
    mine.save()

    disk = _disk(song)
    assert "approval" not in disk
    assert "site" not in disk["publish"]
    assert disk["publish"]["sites"] == {"joy": {"prefix": "p1"}}
    assert disk["lyrics"] == {"ok": True}


def test_a_song_stored_at_an_old_stage_saves_upgraded(songs):
    song = state.create("Kettu", per_system=False)
    with open(song.state_path(), encoding="utf-8") as f:
        data = json.load(f)
    data["stage"] = "record"
    data["record"] = {"bpm": 72}
    with open(song.state_path(), "w", encoding="utf-8") as f:
        json.dump(data, f)

    mine = state.load(song.slug)
    mine.data["lyrics"] = {"ok": True}
    mine.save()

    disk = _disk(song)
    assert disk["stage"] == "publish"
    assert disk["publish"]["bpm"] == 72


def test_adopt_then_save_writes_nothing_older_over_newer(songs):
    song = state.create("Kettu", per_system=False)
    song.save()
    route = state.load(song.slug)
    fresh = state.load(song.slug)
    fresh.data["lyrics"] = {"ok": True}
    fresh.save()
    route.adopt(fresh)

    _sing_publishes(song.slug)
    route.data["voicing"] = "men"
    route.save()

    disk = _disk(song)
    assert disk["publish"]["sites"]["jm"] == RECORD
    assert disk["lyrics"] == {"ok": True}
    assert disk["voicing"] == "men"


def test_a_song_never_loaded_writes_whole(songs):
    song = state.create("Kettu", per_system=False)
    _sing_publishes(song.slug)
    data = dict(state.load(song.slug).data)
    data.pop("publish")
    state.Song(song.slug, data).save()
    assert "publish" not in _disk(song)


def test_a_reference_held_across_a_save_still_reaches_the_song(songs):
    song = state.load(state.create("Kettu", per_system=False).slug)
    pub = song.data.setdefault("publish", {})
    pub["bpm"] = 80
    song.save()
    _sing_publishes(song.slug)      # merged into the same dicts on the next save
    pub["bpm"] = 100
    song.save()

    assert not song._rebound
    disk = _disk(song)
    assert disk["publish"]["bpm"] == 100
    assert disk["publish"]["sites"]["jm"] == RECORD


def test_a_rebound_copy_rebases_when_nothing_was_saved_since(songs, caplog):
    song = state.load(state.create("Kettu", per_system=False).slug)
    data = dict(song.data)
    data["voicing"] = "men"
    song.data = data
    song.save()
    assert _disk(song)["voicing"] == "men"
    assert "rebound" not in caplog.text


# --- the lock -----------------------------------------------------------------


def test_the_lock_nests_in_one_thread(songs):
    with state.song_lock("kettu"):
        with state.song_lock("kettu"):
            pass
        with state.song_lock("kettu", songs_dir=str(songs)):
            pass


def _child(code, env=None):
    return subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def test_another_process_waits_for_the_lock(songs):
    code = ("from src.song_app import state; import sys; "
            f"state.SONGS_DIR = {str(songs)!r}; "
            "lock = state.song_lock('kettu'); lock.acquire(); print('got it')")
    with state.song_lock("kettu"):
        child = _child(code)
        with pytest.raises(subprocess.TimeoutExpired):
            child.wait(timeout=1.5)
    out, err = child.communicate(timeout=30)
    assert child.returncode == 0, err
    assert out.strip() == "got it"


def test_the_lock_file_does_not_follow_home_or_tmpdir(songs, tmp_path):
    code = ("from src.song_app import state; "
            f"print(state.lock_path('kettu', {str(songs)!r}))")
    paths = set()
    for n in (1, 2):
        env = dict(os.environ, HOME=str(tmp_path / f"home{n}"),
                   TMPDIR=str(tmp_path / f"tmp{n}"), XDG_STATE_HOME=str(tmp_path / f"x{n}"))
        child = _child(code, env)
        out, err = child.communicate(timeout=30)
        assert child.returncode == 0, err
        paths.add(out.strip())
    assert paths == {state.lock_path("kettu", str(songs))}


def test_moving_publish_records_waits_for_the_lock_and_keeps_a_clean(songs):
    song = state.create("Kettu", per_system=False)
    song.data["publish"] = {"sites": {"jm": {"prefix": "old", "version": "v1"}}}
    song.save()
    clean = state.load(song.slug)
    change = {"old": "old", "new": "new", "version": "v2", "files": ["a"]}
    lines = []

    with state.song_lock(song.slug):
        mover = threading.Thread(
            target=publish._move_records, args=(str(songs), change, lines.append))
        mover.start()
        mover.join(timeout=1.0)
        assert mover.is_alive()      # waiting for the clean's save
        clean.data["cleaned"] = "kettu_cleaned.mscx"
        clean.save()
    mover.join(timeout=30)

    disk = _disk(song)
    assert disk["cleaned"] == "kettu_cleaned.mscx"
    assert disk["publish"]["sites"]["jm"]["prefix"] == "new"
    assert lines


# --- health dismissed while a job ran -----------------------------------------


def test_a_row_dismissed_while_a_clean_ran_stays_dismissed(songs, monkeypatch):
    song = state.create("Kettu", per_system=False)
    song.data["health"] = {"checked_against": "A",
                           "issues": [{"id": "x", "status": "open"}]}
    song.save()
    slug = song.slug

    def fake_clean(xml, song_dir, per_system=False, log=None, voicing=None, check=None):
        server.api_dismiss(slug, "x")      # the person dismisses it meanwhile
        cleaned = os.path.join(song_dir, "kettu_cleaned.mscx")
        source = os.path.join(song_dir, "kettu.mscx")
        for path in (cleaned, source):
            with open(path, "w", encoding="utf-8") as f:
                f.write("<museScore/>")
        return cleaned, source

    monkeypatch.setattr(pipeline, "run_clean", fake_clean)
    monkeypatch.setattr(clean_backup, "stash", lambda song: None)
    monkeypatch.setattr(verification, "compare_notes", lambda a, b: {"ok": True})
    monkeypatch.setattr(server, "_health_scan", lambda song, cleaned: [{"id": "x"}])
    monkeypatch.setattr(server, "_label_bounds", lambda song, path: None)

    server._run_clean(slug)

    issues = _disk(song)["health"]["issues"]
    assert issues == [{"id": "x", "status": "dismissed"}]


def test_dismiss_works_on_the_record_as_it_is_now(songs, monkeypatch):
    song = state.create("Kettu", per_system=False)
    song.data["health"] = {"checked_against": "A", "issues": [{"id": "x", "status": "open"}]}
    song.save()
    stale = state.load(song.slug)
    monkeypatch.setattr(server, "_require", lambda slug: stale)

    checker = state.load(song.slug)        # a check finished after the route loaded
    checker.data["health"] = {"checked_against": "B", "issues": [
        {"id": "x", "status": "open"}, {"id": "y", "status": "open"}]}
    checker.save()

    server.api_dismiss(song.slug, "x")
    health = _disk(song)["health"]
    assert health["checked_against"] == "B"
    assert health["issues"] == [{"id": "x", "status": "dismissed"},
                                {"id": "y", "status": "open"}]


# --- grid answers saved while a system is read again ---------------------------
#
# The grid counts systems from 0 and the scan's bands from 1, so grid system 0 is
# fragment "1" and grid system 1 is fragment "2" (`scan.fragment_order`). These
# send what the browser sends: grid numbers.


def _scanned(songs, monkeypatch):
    reader = Reader()
    monkeypatch.setattr(pdf_systems, "crop_systems", reader.crop)
    monkeypatch.setattr(omr_systems, "read_system", reader.read_system)
    song = _song(songs)
    scan.run(song)
    return state.load(song.slug)


def _set_content(slug, index, stamp):
    with state.song_lock(slug):
        other = state.load(slug)
        other.data["scan"]["systems"][str(index)]["content"] = stamp
        other.save()


def test_grid_answers_are_refused_when_their_system_was_read_again(songs, monkeypatch):
    song = _scanned(songs, monkeypatch)
    saved = []
    monkeypatch.setattr(server, "_require", lambda slug: state.load(slug))
    # The system is read again while the slow conversion runs.
    monkeypatch.setattr(pipeline, "convert_to_mscx",
                        lambda xml, d: _set_content(song.slug, 2, "new") or "x.mscx")
    monkeypatch.setattr(pipeline, "save_system_answers", lambda m, a: saved.append(a))

    with pytest.raises(server.HTTPException) as err:
        server.api_save_systems(song.slug, {"0": {"1": "T1"}, "1": {"1": "B1"}})
    assert err.value.status_code == 409
    assert "System 2" in err.value.detail
    assert saved == []                                   # no answers written
    assert _disk(song)["scan"].get("answered_against", {}) == {}  # nothing stamped


def test_grid_answers_are_stamped_with_the_reading_they_answer(songs, monkeypatch):
    song = _scanned(songs, monkeypatch)
    saved = []
    monkeypatch.setattr(server, "_require", lambda slug: state.load(slug))
    monkeypatch.setattr(pipeline, "convert_to_mscx",
                        lambda xml, d: _sing_publishes(song.slug) or "x.mscx")
    monkeypatch.setattr(pipeline, "save_system_answers", lambda m, a: saved.append(a))

    server.api_save_systems(song.slug, {"0": {"1": "T1"}})
    disk = _disk(song)
    assert saved == [{0: {1: "T1"}}]
    assert disk["scan"]["answered_against"] == {"1": disk["scan"]["systems"]["1"]["content"]}
    assert disk["publish"]["sites"]["jm"] == RECORD      # saved meanwhile, kept


def _grid_patches(monkeypatch, saved):
    monkeypatch.setattr(server, "_require", lambda slug: state.load(slug))
    monkeypatch.setattr(pipeline, "convert_to_mscx", lambda xml, d: "x.mscx")
    monkeypatch.setattr(pipeline, "system_grid", lambda mscx: [{}, {}, {}, {}])
    monkeypatch.setattr(pipeline, "overlong_in", lambda mscx: [])
    monkeypatch.setattr(pipeline, "save_system_answers", lambda m, a: saved.append(a))


def test_grid_answers_are_refused_when_their_system_was_read_again_after_the_grid_loaded(
        songs, monkeypatch):
    song = _scanned(songs, monkeypatch)
    saved = []
    _grid_patches(monkeypatch, saved)
    stamps = server.api_systems(song.slug)["stamps"]
    systems = _disk(song)["scan"]["systems"]
    assert stamps == {"0": systems["1"]["content"], "1": systems["2"]["content"],
                      "2": systems["3"]["content"], "3": None}

    _set_content(song.slug, 2, "new")       # read again while the person typed
    with pytest.raises(server.HTTPException) as err:
        server.api_save_systems(song.slug, {"answers": {"0": {"1": "T1"}, "1": {"1": "B1"}},
                                            "stamps": stamps})
    assert err.value.status_code == 409
    assert "System 2" in err.value.detail
    assert saved == []

    # The grid's score is the one assembled before the re-read, so a grid loaded
    # again still shows that reading, and its answers are refused until a Scan
    # puts the score together again (#498).
    stamps = server.api_systems(song.slug)["stamps"]
    assert stamps["1"] == systems["2"]["content"]
    with pytest.raises(server.HTTPException) as err:
        server.api_save_systems(song.slug, {"answers": {"1": {"1": "B1"}}, "stamps": stamps})
    assert err.value.status_code == 409
    assert "Press Scan" in err.value.detail
    assert saved == []

    scan.run(state.load(song.slug))         # assembles the new reading
    stamps = server.api_systems(song.slug)["stamps"]
    assert stamps["1"] == "new"
    server.api_save_systems(song.slug, {"answers": {"1": {"1": "B1"}}, "stamps": stamps})
    assert saved == [{1: {1: "B1"}}]
    assert _disk(song)["scan"]["answered_against"] == {"2": "new"}


def test_a_scan_band_save_keeps_grid_answers_stamped_meanwhile(songs, monkeypatch):
    song = _scanned(songs, monkeypatch)
    saved = []
    _grid_patches(monkeypatch, saved)
    read = omr_systems.read_system

    def read_and_answer(image, *args, **kwargs):
        # System 2 is read again; grid row 0 (band 1) is answered meanwhile.
        stamps = server.api_systems(song.slug)["stamps"]
        server.api_save_systems(song.slug, {"answers": {"0": {"1": "T1"}},
                                            "stamps": stamps})
        return read(image, *args, **kwargs)

    monkeypatch.setattr(omr_systems, "read_system", read_and_answer)
    scan.run(state.load(song.slug), only=[2])

    disk = _disk(song)
    assert sorted(disk["scan"]["systems"]) == ["1", "2", "3"]
    assert saved == [{0: {1: "T1"}}]
    assert disk["scan"]["answered_against"] == {"1": disk["scan"]["systems"]["1"]["content"]}


def test_a_scan_that_died_before_saving_its_order_is_finished_by_the_next(
        songs, monkeypatch):
    """The score is written, then the order with it: a crash between the two (#498).

    The song then still holds the old order and the old revision, so the scan is
    not complete, the grid's save says to press Scan, and the next Scan assembles
    again and saves the order, after which the answers are taken.
    """
    song = _scanned(songs, monkeypatch)
    saved = []
    _grid_patches(monkeypatch, saved)
    real = omr_systems.assemble

    def assemble_and_die(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("the process was killed")

    _set_content(song.slug, 2, "new")       # a re-read, saved
    monkeypatch.setattr(omr_systems, "assemble", assemble_and_die)
    with pytest.raises(RuntimeError):
        scan.run(state.load(song.slug))
    monkeypatch.setattr(omr_systems, "assemble", real)

    fresh = state.load(song.slug)
    assert not scan.status(fresh)["complete"]
    stamps = server.api_systems(song.slug)["stamps"]
    with pytest.raises(server.HTTPException) as err:
        server.api_save_systems(song.slug, {"answers": {"1": {"1": "B1"}}, "stamps": stamps})
    assert err.value.status_code == 409 and "Press Scan" in err.value.detail
    assert saved == []

    assert scan.run(state.load(song.slug))["complete"]
    stamps = server.api_systems(song.slug)["stamps"]
    server.api_save_systems(song.slug, {"answers": {"1": {"1": "B1"}}, "stamps": stamps})
    assert saved == [{1: {1: "B1"}}]


def test_an_answer_write_that_fails_keeps_the_answers_already_saved(
        songs, monkeypatch, tmp_path):
    """The stamp is saved first; a failed write of the answers changes no answer."""
    from src.clean_score.utils import per_system
    song = _scanned(songs, monkeypatch)
    saved = []
    _grid_patches(monkeypatch, saved)
    monkeypatch.setattr(pipeline, "save_system_answers", _real_save_system_answers)
    with per_system.use_answer_file(str(tmp_path / "answers.json")):
        stamps = server.api_systems(song.slug)["stamps"]
        server.api_save_systems(song.slug, {"answers": {"0": {"1": "T1"}}, "stamps": stamps})
        before = per_system.saved_answers("x.mscx")
        assert before == {0: {1: "T1"}}

        def no_room(*args, **kwargs):
            raise OSError(28, "No space left on device")

        class FullDisk:            # `os` as the answer module sees it, disk full
            def __getattr__(self, name):
                return no_room if name == "replace" else getattr(os, name)

        with monkeypatch.context() as m:
            m.setattr(per_system, "os", FullDisk())
            with pytest.raises(server.HTTPException) as err:
                server.api_save_systems(song.slug, {"answers": {"0": {"1": "B1"}},
                                                    "stamps": stamps})
        assert err.value.status_code == 500
        assert per_system.saved_answers("x.mscx") == before
        assert not [n for n in os.listdir(tmp_path) if n.endswith(".tmp")]


_real_save_system_answers = pipeline.save_system_answers


def test_two_songs_answering_at_once_both_keep_their_answers(tmp_path):
    """The answer file holds every song: two writers must not lose each other's."""
    from src.clean_score.utils import per_system
    with per_system.use_answer_file(str(tmp_path / "answers.json")):
        def answer(name):
            for n in range(30):
                per_system.update_answers(
                    str(tmp_path / name / f"{name}.mscx"),
                    lambda old, n=n: {**(old or {}), n: {1: name}})

        threads = [threading.Thread(target=answer, args=(name,)) for name in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for name in ("a", "b"):
            got = per_system.saved_answers(str(tmp_path / name / f"{name}.mscx"))
            assert sorted(got) == list(range(30))


def test_a_damaged_answer_file_is_refused_not_overwritten(tmp_path):
    from src.clean_score.utils import per_system
    path = tmp_path / "answers.json"
    path.write_text("{ not json", encoding="utf-8")
    with per_system.use_answer_file(str(path)):
        with pytest.raises(per_system.AnswerFileError):
            per_system.update_answers(str(tmp_path / "x.mscx"), lambda old: {0: {1: "T1"}})
    assert path.read_text(encoding="utf-8") == "{ not json"
