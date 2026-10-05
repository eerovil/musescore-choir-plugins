"""Picking between homr's readings of an unsure bar (#269).

homr writes, for each voice of a bar it doubted, the few readings that fill the bar.
What these pin is the app's half: an option is offered on the cleaned staff whose bar
holds those notes (found by what the bar says, since cleaning renumbers everything),
on the right bar number; a pick goes onto the score and into fixes.json and comes back
on a re-clean; "none of these" is remembered; and a pick made on a reading the scan
has since replaced is dropped rather than replayed.
"""
import json
import os

import pytest
from lxml import etree

from src.clean_score.utils import score_fixes
from src.clean_score.utils.score_fixes import FixError
from src.song_app import bar_readings, pipeline, scan, state

# Two moments in a 2/4 bar, C3 and D3. homr wrote two quarters; it also weighed a
# dotted quarter and an eighth, and the other way round.
READINGS = {"version": 1, "bars": [
    {"part": 0, "staff": 1, "bar": 2, "voice": "1", "length": "1/2",
     "moments": [{"kind": "note", "pitches": [{"step": "C", "alter": 0, "octave": 3}],
                  "value": "note_4"},
                 {"kind": "note", "pitches": [{"step": "D", "alter": 0, "octave": 3}],
                  "value": "note_4"}],
     "readings": [{"values": ["note_4", "note_4"], "score": -1.0},
                  {"values": ["note_4.", "note_8"], "score": -1.5},
                  {"values": ["note_8", "note_4."], "score": -3.0}]}]}


def _fragment(readings=READINGS):
    misc = ""
    if readings is not None:
        misc = ("<identification><miscellaneous><miscellaneous-field name=\"homr-bar-readings\">"
                + json.dumps(readings) + "</miscellaneous-field></miscellaneous></identification>")
    bar = ("<note><pitch><step>C</step><octave>3</octave></pitch><duration>1</duration>"
           "<type>quarter</type><voice>1</voice></note>"
           "<note><pitch><step>D</step><octave>3</octave></pitch><duration>1</duration>"
           "<type>quarter</type><voice>1</voice></note>")
    return (f"<score-partwise>{misc}<part-list><score-part id=\"P1\"/></part-list>"
            "<part id=\"P1\"><measure number=\"1\"><attributes><divisions>1</divisions>"
            "<key><fifths>-1</fifths></key><time><beats>2</beats><beat-type>4</beat-type></time>"
            "<clef><sign>F</sign><line>4</line></clef></attributes>"
            f"{bar}</measure><measure number=\"2\">{bar}</measure></part></score-partwise>")


def _bar(p1, p2):
    return (f"<Chord><durationType>quarter</durationType><Lyrics><text>la</text></Lyrics>"
            f"<Note><pitch>{p1}</pitch><tpc>14</tpc></Note></Chord>"
            f"<Chord><durationType>quarter</durationType><Note><pitch>{p2}</pitch>"
            f"<tpc>16</tpc></Note></Chord>")


def _staff(sid, shift=0):
    rest = "<Rest><durationType>half</durationType></Rest>"
    bars = [rest, _bar(48 + shift, 50 + shift), _bar(48 + shift, 50 + shift)]
    return f"<Staff id=\"{sid}\">" + "".join(
        f"<Measure><voice>{b}</voice></Measure>" for b in bars) + "</Staff>"


def _score(staves):
    parts = "".join(f"<Part><trackName>{name}</trackName><Staff id=\"{sid}\"/></Part>"
                    for sid, name, _ in staves)
    return ("<museScore version=\"3.02\"><Score>" + parts
            + "".join(_staff(sid, shift) for sid, _, shift in staves) + "</Score></museScore>")


@pytest.fixture
def make_song(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "SONGS_DIR", str(tmp_path))

    def make(staves=((1, "B1", 0),), readings=READINGS):
        song = state.create("Unsure", per_system=False)
        os.makedirs(song.path("scan"))
        first, second = song.path("scan/system-01.musicxml"), song.path("scan/system-02.musicxml")
        with open(first, "w") as fh:
            fh.write(_fragment(None))
        with open(second, "w") as fh:
            fh.write(_fragment(readings))
        song.data["scan"] = {"systems": {
            "1": {"index": 1, "musicxml": "scan/system-01.musicxml",
                  "content": scan.content_stamp(first), "bars": 1, "error": None},
            "2": {"index": 2, "musicxml": "scan/system-02.musicxml",
                  "content": scan.content_stamp(second), "bars": 2, "error": None}}}
        with open(song.path("song_cleaned.mscx"), "w") as fh:
            fh.write(_score(staves))
        song.data["cleaned"] = "song_cleaned.mscx"
        song.save()
        return song
    return make


def _tokens(song, staff=1, measure=3):
    root = etree.parse(song.cleaned_path()).getroot()
    return score_fixes._bar_tokens(score_fixes._measure(root, staff, measure))


def _fixes(song):
    path = song.path("fixes.json")
    return json.load(open(path)) if os.path.exists(path) else []


def test_the_bar_is_offered_where_it_landed_in_the_score(make_song):
    song = make_song()
    [offer] = bar_readings.offers(song)
    # System 2's bar 2, after system 1's one bar.
    assert offer["measure"] == 3
    assert offer["staff"] == 1 and offer["part"] == "B1"
    assert [o["letter"] for o in offer["options"]] == ["a", "b", "c"]
    assert [o["current"] for o in offer["options"]] == [True, False, False]
    assert offer["decision"] is None


def test_a_tenor_marked_an_octave_down_still_matches(make_song):
    song = make_song(staves=((1, "T1", 12),))
    assert [o["part"] for o in bar_readings.offers(song)] == ["T1"]


def test_a_bar_cleaning_changed_is_not_offered(make_song):
    song = make_song(staves=((1, "B1", 2),))  # a different note, not an octave
    assert bar_readings.offers(song) == []


def test_two_voices_reading_alike_go_to_one_staff_each(make_song):
    doubled = json.loads(json.dumps(READINGS))
    doubled["bars"].append({**doubled["bars"][0], "voice": "2"})
    song = make_song(staves=((1, "B1", 0), (2, "B2", 0)), readings=doubled)
    assert sorted(o["part"] for o in bar_readings.offers(song)) == ["B1", "B2"]


def test_a_fragment_from_an_older_homr_offers_nothing(make_song):
    assert bar_readings.offers(make_song(readings=None)) == []


def test_a_pick_changes_the_score_and_is_recorded(make_song):
    song = make_song()
    [offer] = bar_readings.offers(song)
    bar_readings.record_pick(song, offer["id"], "b")
    assert _tokens(song) == ["quarter.:48", "eighth:50"]
    [entry] = _fixes(song)
    assert entry["kind"] == "rhythm" and entry["source"] == "reading"
    assert entry["to"] == ["note_4.", "note_8"]
    assert entry["content"] == song.data["scan"]["systems"]["2"]["content"]
    # The words stay on their notes.
    root = etree.parse(song.cleaned_path()).getroot()
    assert score_fixes._measure(root, 1, 3).findtext(".//Lyrics/text") == "la"
    [offer] = bar_readings.offers(song)
    assert offer["decision"] == {"picked": "b"}


def test_the_pick_comes_back_on_a_rebuild(make_song, tmp_path):
    song = make_song()
    [offer] = bar_readings.offers(song)
    bar_readings.record_pick(song, offer["id"], "c")
    rebuilt = tmp_path / "rebuilt.mscx"
    rebuilt.write_text(_score(((1, "B1", 0),)))
    assert pipeline.apply_recorded_fixes(str(rebuilt), song.dir) == 1
    root = etree.parse(str(rebuilt)).getroot()
    assert score_fixes._bar_tokens(score_fixes._measure(root, 1, 3)) == ["eighth:48",
                                                                         "quarter.:50"]


def test_none_of_these_keeps_the_reading_and_is_remembered(make_song):
    song = make_song()
    [offer] = bar_readings.offers(song)
    before = _tokens(song)
    bar_readings.record_pick(song, offer["id"], "none")
    assert _tokens(song) == before
    assert _fixes(song) == []
    [offer] = bar_readings.offers(state.load(song.slug))
    assert offer["decision"] == {"none": True}


def test_a_bar_already_decided_cannot_be_picked_again(make_song):
    song = make_song()
    [offer] = bar_readings.offers(song)
    bar_readings.record_pick(song, offer["id"], "b")
    with pytest.raises(FixError, match="already"):
        bar_readings.record_pick(song, offer["id"], "c")


def test_an_offer_that_is_gone_is_refused(make_song):
    song = make_song()
    with pytest.raises(FixError, match="no readings on offer"):
        bar_readings.record_pick(song, "s2-nothing-p0-st1-b2-v1", "b")


def test_a_pick_lapses_when_its_system_is_read_again_differently(make_song):
    song = make_song()
    [offer] = bar_readings.offers(song)
    bar_readings.record_pick(song, offer["id"], "b")
    song.data["scan"]["systems"]["2"]["content"] = "something-else"
    assert bar_readings.drop_stale_picks(song) == 1
    assert _fixes(song) == []
    # And a pick on a reading still current stays.
    song = make_song()
    [offer] = bar_readings.offers(song)
    bar_readings.record_pick(song, offer["id"], "b")
    assert bar_readings.drop_stale_picks(song) == 0
    assert len(_fixes(song)) == 1


def test_each_option_is_engraved(make_song):
    song = make_song()
    [offer] = bar_readings.offers(song)
    svg = bar_readings.option_svg(song, offer["id"], "b")
    assert svg.lstrip().startswith(("<?xml", "<svg"))
    musicxml = bar_readings.option_musicxml(
        song.path("scan/system-02.musicxml"), READINGS["bars"][0], ["note_4.", "note_8"])
    root = etree.fromstring(musicxml)
    assert [n.findtext("type") for n in root.iter("note")] == ["quarter", "eighth"]
    assert root.find(".//note/dot") is not None
    assert root.findtext(".//clef/sign") == "F"


def test_triplet_options_are_bracketed():
    entry = {"part": 0, "staff": 1, "bar": 1, "moments": [
        {"kind": "note", "pitches": [{"step": s, "alter": 0, "octave": 3}]} for s in "CDE"]}
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".musicxml", delete=False) as fh:
        fh.write(_fragment(None))
    try:
        root = etree.fromstring(bar_readings.option_musicxml(
            fh.name, entry, ["note_12", "note_12", "note_12"]))
    finally:
        os.remove(fh.name)
    tuplets = [t.get("type") for t in root.iter("tuplet")]
    assert tuplets == ["start", "stop"]
    assert len(root.findall(".//time-modification")) == 3
