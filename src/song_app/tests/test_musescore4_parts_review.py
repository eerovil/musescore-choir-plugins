"""What Review and Record are told about a score saved by MuseScore 4.

MuseScore 4 writes each Part's staves without an id, so which notes belong to which
part is worked out by position. A score edited and saved in MuseScore 4 used to show
"0 parts" at Record: every part was taken as having nothing to sing.
"""

from src.song_app import health, verification

_SIG = "<TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>"


def _bar(kind, pitch):
    return (f"<Measure><voice>{_SIG}<Chord><durationType>{kind}</durationType>"
            f"<Note><pitch>{pitch}</pitch></Note></Chord></voice></Measure>")


# S1 fills its bar; S1b is a quarter note in a 4/4 bar, which the health check lists.
MS4 = f"""<museScore version="4.70"><Score>
  <Part id="1"><Staff/><trackName>S1</trackName>
    <Instrument><trackName>S1</trackName></Instrument></Part>
  <Part id="1"><Staff/><trackName>S1b</trackName>
    <Instrument><trackName>S1b</trackName></Instrument></Part>
  <Staff id="1">{_bar("whole", 72)}</Staff>
  <Staff id="2">{_bar("quarter", 67)}</Staff>
</Score></museScore>"""


def _saved(tmp_path):
    path = tmp_path / "score.mscx"
    path.write_text(MS4)
    return str(path)


def test_every_part_saved_by_musescore_4_is_a_singing_part(tmp_path):
    assert verification.singing_parts(_saved(tmp_path)) == ["S1", "S1b"]


def test_notes_saved_by_musescore_4_are_counted_for_their_own_part(tmp_path):
    events = verification._note_events(_saved(tmp_path), only=["S1b"])
    assert sorted(pitch for _measure, pitch, _duration in events) == ["67"]


def test_a_health_finding_saved_by_musescore_4_names_the_part(tmp_path):
    found = [issue for issue in health.scan(_saved(tmp_path))
             if issue["kind"] == "malformed-measure"]
    assert [issue["staff"] for issue in found] == ["S1b"]
