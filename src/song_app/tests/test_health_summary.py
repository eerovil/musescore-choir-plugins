"""Health on the Review stage: a count and a warning, and nothing more.

#170 added a verdict ("this parse looks unusable") on top of the count; #356 took it
out again, because on scanned songs it was set off mostly by homr's `⚠` questions,
which flag a bar on purpose when it is merely unsure. What is pinned here is that
the count is still said, that no verdict rides beside it, and that nothing gates.
"""

from __future__ import annotations

import os

from src.song_app import health, state, verification


def _malformed(measure: int, staff: str = "T1") -> dict:
    return {"id": f"malformed-m{measure}-s1-v0", "kind": "malformed-measure",
            "measure": measure, "staff": staff, "detail": "voice 1 fills 7/8 of 1",
            "status": "open"}


def test_score_bars_reads_the_longest_staff(tmp_path):
    score = tmp_path / "s.mscx"
    staff = "<Staff id=\"{i}\">" + "<Measure></Measure>" * 6 + "</Staff>"
    score.write_text("<museScore><Score>"
                     + staff.format(i=1) + staff.format(i=2)
                     + "</Score></museScore>", encoding="utf-8")
    assert health.score_bars(str(score)) == 6


# --- where it is said -------------------------------------------------------

_SCORE = ("<museScore><Score>"
          + ("<Staff id=\"1\">" + "<Measure></Measure>" * 10 + "</Staff>")
          + "</Score></museScore>")


def _song(tmp_path, monkeypatch, issues) -> state.Song:
    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    song = state.create("Rough", per_system=False)
    cleaned = song.path("rough_cleaned.mscx")
    with open(cleaned, "w", encoding="utf-8") as handle:
        handle.write(_SCORE)
    song.data["cleaned"] = os.path.basename(cleaned)
    song.data["cleaned_fingerprint"] = state.file_fingerprint(cleaned)
    song.data["health"] = {"checked_against": state.file_fingerprint(cleaned),
                           "issues": issues}
    song.save()
    return song


def test_a_score_with_findings_on_most_bars_is_counted_not_judged(tmp_path, monkeypatch):
    # Five findings on five of ten bars: half the score. Before #356 this carried an
    # "unusable" verdict; now it is the count and nothing else.
    song = _song(tmp_path, monkeypatch, [_malformed(m) for m in range(1, 6)])
    result = verification.summary(song, systems=3)["health"]
    assert result["detail"] == "Current score checked; 5 open issue(s)."
    assert "verdict" not in result
    assert not hasattr(health, "verdict")


def test_findings_warn_and_do_not_gate_the_review_stage(tmp_path, monkeypatch):
    # Open findings are a warning, never a failure, and never take the approval away.
    song = _song(tmp_path, monkeypatch, [_malformed(m) for m in range(1, 6)])
    result = verification.summary(song, systems=3)["health"]
    assert result["status"] == "warning"
