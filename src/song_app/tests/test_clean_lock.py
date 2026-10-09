"""Two cleans take turns, and a failed one says what failed (#357).

`clean_score` keeps its staff mapping and reversed-voice table in one process-wide
`GLOBALS`, which `main()` empties as it starts. On 2026-10-09 two songs' cleans began
0.1s apart and the first died at `reversed_voices.py` with `KeyError: 3`, which the
song's log showed as the bare text "3". Running it again worked.
"""
import threading
import time

from src.song_app import pipeline, server


def test_two_cleans_never_run_clean_score_at_once(tmp_path, monkeypatch):
    running, most = [0], [0]
    guard = threading.Lock()

    def fake_main(_mscx, building, **_kw):
        with guard:
            running[0] += 1
            most[0] = max(most[0], running[0])
        time.sleep(0.2)
        with guard:
            running[0] -= 1
        open(building, "w").write("<museScore/>")

    monkeypatch.setattr(pipeline, "convert_to_mscx", lambda path, out_dir, log: path)
    monkeypatch.setattr(pipeline, "clean_main", fake_main)
    for name in ("apply_recorded_fixes", "record_clean_marks", "record_dropped_voices"):
        monkeypatch.setattr(pipeline, name, lambda *a, **k: 0)
    monkeypatch.setattr(pipeline, "check_opens_in_musescore", lambda *a, **k: {})

    logs = [[], []]
    threads = []
    for n in range(2):
        song = tmp_path / f"song{n}"
        song.mkdir()
        (song / "in.mscx").write_text("<museScore/>")
        threads.append(threading.Thread(target=pipeline.run_clean, args=(
            str(song / "in.mscx"), str(song), False), kwargs={"log": logs[n].append}))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert most[0] == 1
    assert any("Waiting for another clean" in line for line in logs[0] + logs[1])
    assert (tmp_path / "song0" / "in_cleaned.mscx").exists()
    assert (tmp_path / "song1" / "in_cleaned.mscx").exists()


def test_the_lock_is_let_go_when_clean_score_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "convert_to_mscx", lambda path, out_dir, log: path)

    def broken(*_a, **_k):
        raise KeyError(3)

    monkeypatch.setattr(pipeline, "clean_main", broken)
    try:
        pipeline.run_clean(str(tmp_path / "in.mscx"), str(tmp_path), False)
    except KeyError:
        pass
    assert pipeline._CLEAN_LOCK.acquire(blocking=False)
    pipeline._CLEAN_LOCK.release()


def test_an_unexpected_error_names_its_type():
    assert server._error_text(KeyError(3)) == "KeyError: 3"
    assert server._error_text(RuntimeError("A recorded fix no longer matches")) == \
        "A recorded fix no longer matches"
