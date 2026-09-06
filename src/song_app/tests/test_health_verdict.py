"""The verdict on a whole parse: is it worth repairing, or is it a bad reading?

The rule under test is one sentence -- more than a fifth of the bars carrying a
finding, and at least a handful of bars -- so most of what is worth pinning is what
the rule must NOT do: condemn a long score for being long, condemn a wide score for
having staves, go quiet on the score most worth judging, or turn into a gate.
"""

from __future__ import annotations

import os

from src.song_app import health, state, verification


def _malformed(measure: int, staff: str = "T1") -> dict:
    return {"id": f"malformed-m{measure}-s1-v0", "kind": "malformed-measure",
            "measure": measure, "staff": staff, "detail": "voice 1 fills 7/8 of 1",
            "status": "open"}


def test_a_clean_score_gets_no_verdict_to_argue_with():
    assert health.verdict([], 52)["level"] == "clean"


def test_a_handful_of_findings_is_a_repair_list():
    judged = health.verdict([_malformed(m) for m in (4, 11, 35)], 52)
    assert judged["level"] == "repairable"
    assert judged["bars_touched"] == 3


def test_the_walks_own_song_is_called_unusable():
    # 60 findings over 28 of its 52 bars: the parse the operator looked at and said
    # "sixty is very probably a garbage scan".
    issues = [_malformed(m) for m in range(1, 29)]
    issues += [_malformed(m, "B2") for m in range(1, 29)]
    judged = health.verdict(issues, 52)
    assert judged["level"] == "unusable"
    assert judged["findings"] == 56
    assert "read it against the page" in judged["message"].lower()


def test_a_long_song_is_not_condemned_for_being_long():
    # Same number of findings as the walk's song, spread over twice the music. A raw
    # count cannot tell these two apart, which is why the count is not the signal.
    spread = [_malformed(m) for m in range(1, 57, 2)]
    assert len(spread) == 28
    assert health.verdict(spread, 200)["level"] == "repairable"


def test_a_wide_score_is_not_condemned_for_having_staves():
    # Eight staves means eight chances per bar to earn a finding. Sharing one bar
    # between them is one damaged bar, not eight.
    crowded = [dict(_malformed(4), id=f"malformed-m4-s{s}-v0", staff=f"s{s}")
               for s in range(1, 9)]
    assert health.verdict(crowded, 52)["level"] == "repairable"


def test_a_short_score_is_not_condemned_by_two_bad_bars():
    # 2 of 8 bars is a quarter of the score, and it is also two findings. A verdict
    # about a whole parse should not be reachable that cheaply.
    assert health.verdict([_malformed(1), _malformed(2)], 8)["level"] == "repairable"
    assert health.verdict([_malformed(1), _malformed(2), _malformed(3)], 8)["level"] \
        == "unusable"


def test_a_collapsed_meter_row_is_judged_by_the_bars_it_stands_for():
    # The #124 hole one level up: a score whose findings are shown as one line must
    # not be judged as one line. The row names its bars, and they all count.
    collapsed = {"id": "meter-collapsed-40", "kind": "meter-collapsed", "measure": 2,
                 "staff": "whole score", "collapsed": 40, "collapsed_bars": 20,
                 "collapsed_measures": list(range(2, 22)), "status": "open"}
    assert health.bars_touched([collapsed]) == 20
    assert health.verdict([collapsed], 52)["level"] == "unusable"
    # ...and a record written before the bar numbers were kept still counts them.
    legacy = {k: v for k, v in collapsed.items() if k != "collapsed_measures"}
    assert health.verdict([legacy], 52)["level"] == "unusable"


def test_bars_touched_is_a_union_and_not_a_tally():
    both = [_malformed(4), dict(_malformed(4), id="extra-voices-m4-s2",
                                kind="extra-voices", staff="B1")]
    assert health.bars_touched(both) == 1


def test_the_share_cannot_exceed_the_score():
    # A legacy collapsed row claims more bars than the score has; the share stays a
    # share rather than reading as 140%.
    legacy = {"id": "meter-collapsed-80", "kind": "meter-collapsed", "measure": 1,
              "collapsed": 80, "collapsed_bars": 70, "status": "open"}
    judged = health.verdict([legacy], 52)
    assert judged["bars_touched"] == 52
    assert judged["share"] == 1.0


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


def test_the_review_summary_says_the_verdict_next_to_the_count(tmp_path, monkeypatch):
    song = _song(tmp_path, monkeypatch, [_malformed(m) for m in range(1, 6)])
    result = verification.summary(song, systems=3)["health"]
    # The count is still there -- the verdict is what the count means, not a
    # replacement for it -- and the verdict rides beside it rather than inside the
    # sentence, so the panel can say it once instead of twice on one screen.
    assert result["detail"] == "Current score checked; 5 open issue(s)."
    assert result["verdict"]["level"] == "unusable"
    assert "read it against the page" in result["verdict"]["message"].lower()


def test_a_repairable_score_says_nothing_extra(tmp_path, monkeypatch):
    song = _song(tmp_path, monkeypatch, [_malformed(1), _malformed(2)])
    result = verification.summary(song, systems=3)["health"]
    assert result["verdict"]["level"] == "repairable"
    assert result["detail"] == "Current score checked; 2 open issue(s)."


def test_the_verdict_does_not_gate_the_review_stage(tmp_path, monkeypatch):
    # The whole point: it warns and nothing else. `summary` reports a warning for any
    # open finding already, and the verdict must not turn that into a failure or take
    # the approval away.
    song = _song(tmp_path, monkeypatch, [_malformed(m) for m in range(1, 6)])
    result = verification.summary(song, systems=3)["health"]
    assert result["status"] == "warning"
