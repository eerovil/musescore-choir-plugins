"""The printed line breaks, in the cleaned score's bar numbering (#354).

The cleaned previews and the Compare pictures are cut where the page is, using
breaks read off the converted input. A ``delbar`` or ``insbar`` moves the bars
of the cleaned score and not those of the input, so the breaks have to move
with them or every system after the move starts a bar off -- Kun poijat ne
raitilla's pictures did, from system 3 on.
"""
import json
import os

from lxml import etree

from src.song_app import pipeline


def a_score(path, bars, breaks):
    measures = "".join(
        "<Measure><voice><Rest><durationType>measure</durationType></Rest></voice>"
        + ("<LayoutBreak><subtype>line</subtype></LayoutBreak>" if i in breaks else "")
        + "</Measure>" for i in range(bars))
    path.write_text(f'<museScore><Score><Staff id="1">{measures}</Staff></Score></museScore>')
    return str(path)


def fixes(song, *entries):
    (song / "fixes.json").write_text(json.dumps(list(entries)))


def test_kun_poijat_after_its_delbar(tmp_path):
    source = a_score(tmp_path / "in.mscx", 22, {3, 7, 12, 15, 18})
    fixes(tmp_path, {"kind": "delbar", "measure": 10, "from": []})
    assert pipeline.cleaned_line_breaks(str(tmp_path), source) == [3, 7, 11, 14, 17]
    assert pipeline.cleaned_system_starts(str(tmp_path), source) == [0, 4, 8, 12, 15, 18]


def test_no_fixes_leaves_the_breaks_alone(tmp_path):
    source = a_score(tmp_path / "in.mscx", 12, {3, 7})
    assert pipeline.cleaned_line_breaks(str(tmp_path), source) == [3, 7]


def test_a_break_on_the_deleted_bar_moves_to_the_bar_before():
    assert pipeline.moved_breaks([3, 7], [("del", 8)]) == [3, 6]


def test_a_bar_put_in_moves_the_later_breaks_on():
    assert pipeline.moved_breaks([3, 7], [("ins", 2)]) == [4, 8]


def test_moves_count_in_file_order():
    """Kristallen den fina: a bar put in, then the scan's invented one taken out."""
    assert pipeline.moved_breaks([3, 7], [("ins", 2), ("del", 6)]) == [4, 7]


def test_compare_labels_systems_by_the_moved_breaks(tmp_path, monkeypatch):
    from src.song_app import pdf_systems
    cleaned = a_score(tmp_path / "c.mscx", 21, set())
    bounds = [pdf_systems.SystemBounds(index=i, page=1, top=0.1 * i, bottom=0.1 * i + 0.1,
                                       measure_start=s, measure_end=e)
              for i, (s, e) in enumerate([(1, 4), (5, 8), (9, 13), (14, 16),
                                          (17, 19), (20, 22)], 1)]
    monkeypatch.setattr(pdf_systems, "load_bounds", lambda d: bounds)
    monkeypatch.setattr(pipeline, "render_score_pdf", lambda *a, **k: "x.pdf")
    monkeypatch.setattr(pdf_systems, "rendered_system_bands",
                        lambda *a: [object()] * 6)
    pairs = pipeline.compare_systems(str(tmp_path), cleaned, [3, 7, 11, 14, 17])
    assert [(p["measure_start"], p["measure_end"]) for p in pairs] == [
        (1, 4), (5, 8), (9, 12), (13, 15), (16, 18), (19, 21)]


def test_a_render_is_redone_when_its_breaks_move(tmp_path, monkeypatch):
    """The cache used to be checked against the score's mtime alone, and a
    recorded delbar moves the breaks without the cleaned score being newer."""
    score = a_score(tmp_path / "s.mscx", 8, set())
    runs = []

    class Done:
        returncode = 0
        stdout = stderr = ""

    def fake_run(cmd, **kw):
        runs.append(cmd)
        open(cmd[-1], "w").write("pdf")
        return Done()

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    monkeypatch.setattr(pipeline, "BREAK_SCALES", (0.65,))
    pipeline.render_score_pdf(score, [3], tidy=True)
    pipeline.render_score_pdf(score, [3], tidy=True)
    assert len(runs) == 1
    pipeline.render_score_pdf(score, [2], tidy=True)
    assert len(runs) == 2


def test_a_tidy_render_shows_the_hidden_rests(tmp_path):
    score = tmp_path / "s.mscx"
    score.write_text(
        '<museScore><Score><Style/><Staff id="1"><Measure><voice>'
        "<Rest><visible>0</visible><durationType>quarter</durationType></Rest>"
        "</voice></Measure></Staff></Score></museScore>")
    tmp = pipeline._scaled_staff_mscx(str(score), None, 1.0, tidy=True)
    try:
        assert not etree.parse(tmp).getroot().findall(".//visible")
    finally:
        os.remove(tmp)
    assert pipeline._scaled_staff_mscx(str(score), None, 1.0) is None
