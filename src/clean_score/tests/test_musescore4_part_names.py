"""Part names and staff ids of a score saved by MuseScore 4.

MuseScore 4 writes each Part's staves without an id (and gives every Part the same
one), so the name a person knows a staff by cannot be looked up by id. It is found
by position: the first Part owns the first staff of music, and so on.
"""

from lxml import etree

from src.clean_score import lyric_txt
from src.clean_score.utils.rejected_bars import staff_names


def _bar(pitch):
    return ("<Measure><voice><Chord><durationType>whole</durationType>"
            f"<Note><pitch>{pitch}</pitch></Note></Chord></voice></Measure>")


MS4 = f"""<museScore version="4.70"><Score>
  <Part id="1"><Staff/><trackName>S1</trackName>
    <Instrument><trackName>S1</trackName></Instrument></Part>
  <Part id="1"><Staff/><trackName>S1b</trackName>
    <Instrument><trackName>S1b</trackName></Instrument></Part>
  <Staff id="1">{_bar(72)}</Staff>
  <Staff id="2">{_bar(67)}</Staff>
</Score></museScore>"""


def test_staff_names_saved_by_musescore_4_are_the_part_names():
    assert staff_names(etree.fromstring(MS4)) == {1: "S1", 2: "S1b"}


def test_lyric_parts_saved_by_musescore_4_carry_their_staff_ids():
    parts = lyric_txt.lyric_parts(etree.fromstring(MS4))
    assert [(part.id, part.name) for part in parts] == [(1, "S1"), (2, "S1b")]


def test_a_part_name_saved_by_musescore_4_maps_to_its_staff():
    assert lyric_txt._read_part_name_map(etree.fromstring(MS4)) == {"S1": 1, "S1B": 2}
