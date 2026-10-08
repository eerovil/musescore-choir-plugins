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
    # 2/4, as the fragment says, so health has nothing to say about the bars' lengths.
    rest = ("<TimeSig><sigN>2</sigN><sigD>4</sigD></TimeSig>"
            "<Rest><durationType>half</durationType></Rest>")
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


def _printed(song, staff_map):
    """Record where clean_score printed the parts, as a per-system clean writes it."""
    root = etree.parse(song.cleaned_path()).getroot()
    etree.SubElement(root.find("Score"), "metaTag", name="lyricsSystemMap").text = json.dumps(
        [{"start": 1, "end": 1, "map": {"1": [1]}, "source": {"1": [1]}},
         {"start": 2, "end": 3, "map": staff_map, "source": staff_map}])
    etree.ElementTree(root).write(song.cleaned_path(), encoding="UTF-8")


def test_a_bar_goes_only_to_a_part_printed_on_the_staff_homr_read(make_song):
    """The same notes an octave up on another staff are another singer (#310).

    Lemmen nosto m106: homr read the fourth staff, and its lower voice matched S2,
    printed second, an octave up. With the record of where the parts were printed
    it goes to the part on the staff homr read, or nowhere.
    """
    song = make_song(staves=((1, "S2", 12), (2, "A2", 0)))
    assert [o["part"] for o in bar_readings.offers(song)] == ["S2"]  # no record: first match
    _printed(song, {"1": [2]})  # homr's only staff in that system is A2's
    assert [o["part"] for o in bar_readings.offers(song)] == ["A2"]


def test_a_bar_no_part_on_that_staff_sings_is_not_offered(make_song):
    song = make_song(staves=((1, "S2", 12), (2, "A2", 2)))
    _printed(song, {"1": [2]})
    assert bar_readings.offers(song) == []


def test_a_record_that_does_not_line_up_with_the_fragment_is_not_trusted(make_song):
    # The record puts a part on a second staff; homr's fragment has one, so the
    # two cannot be lined up and every part stays a candidate.
    song = make_song(staves=((1, "S2", 12), (2, "A2", 0)))
    _printed(song, {"1": [2], "2": [1]})
    assert [o["part"] for o in bar_readings.offers(song)] == ["S2"]


def test_a_score_cleaned_before_the_page_record_restricts_nothing(make_song):
    # Only the rank-ordered "map": it numbers staves S<A<T<B, not down the page.
    song = make_song(staves=((1, "S2", 12), (2, "A2", 0)))
    root = etree.parse(song.cleaned_path()).getroot()
    etree.SubElement(root.find("Score"), "metaTag", name="lyricsSystemMap").text = json.dumps(
        [{"start": 1, "end": 3, "map": {"1": [2]}}])
    etree.ElementTree(root).write(song.cleaned_path(), encoding="UTF-8")
    assert [o["part"] for o in bar_readings.offers(song)] == ["S2"]


def _two_staff_fragment(readings):
    """System 2 printed as two staves, each holding C3 D3 in both bars."""
    one = _fragment(readings)
    part = one[one.index("<part id=\"P1\">"):one.index("</score-partwise>")]
    return (one[:one.index("<part-list>")]
            + "<part-list><score-part id=\"P1\"/><score-part id=\"P2\"/></part-list>"
            + part + part.replace("\"P1\"", "\"P2\"") + "</score-partwise>")


def test_a_bar_follows_the_grid_not_the_lyric_rank_when_staves_are_reordered(make_song):
    """The page prints B above T1; the lyric map ranks T1 first (#310 review).

    homr read the second staff, T1's. Numbering by the lyric map's rank would point
    at B, which holds the same notes an octave down, and offer T1's bar to the basses.
    """
    from src.clean_score.utils.per_system import _build_lyric_map

    readings = json.loads(json.dumps(READINGS))
    readings["bars"][0]["part"] = 1  # the fragment's second part: its second staff
    song = make_song(staves=((1, "T1", 0), (2, "B", -12)), readings=readings)
    path = song.path("scan/system-02.musicxml")
    with open(path, "w") as fh:
        fh.write(_two_staff_fragment(readings))
    song.data["scan"]["systems"]["2"]["content"] = scan.content_stamp(path)
    song.save()
    # The grid's answer for system 2: staff 1 is B, staff 2 is T1.
    decls = {0: {(1, 0): "T1"}, 1: {(1, 0): "B", (2, 0): "T1"}}
    lyric_map = _build_lyric_map([(0, 0), (1, 2)], decls, ["T1", "B"], [1, 2])
    assert lyric_map[1]["map"] == {1: [1], 2: [2]}      # ranked: T1 first
    assert lyric_map[1]["source"] == {1: [2], 2: [1]}   # on the page: B first
    root = etree.parse(song.cleaned_path()).getroot()
    etree.SubElement(root.find("Score"), "metaTag", name="lyricsSystemMap").text = \
        json.dumps(lyric_map)
    etree.ElementTree(root).write(song.cleaned_path(), encoding="UTF-8")
    assert [o["part"] for o in bar_readings.offers(song)] == ["T1"]
    # And the card names the staff the same way: T1 is the second of two.
    printed = bar_readings.printed_staves(root)
    assert bar_readings.printed_place(printed, 3, 1) == (2, 2, 1, 1)
    assert bar_readings.printed_place(printed, 3, 2) == (1, 2, 1, 1)


def test_a_fragment_from_an_older_homr_offers_nothing(make_song):
    assert bar_readings.offers(make_song(readings=None)) == []


def test_a_pick_changes_the_score_and_is_recorded(make_song):
    song = make_song()
    [offer] = bar_readings.offers(song)
    bar_readings.record_pick(song, offer["id"], "b")
    assert _tokens(song) == ["quarter.:48", "eighth:50"]
    [entry] = _fixes(song)
    assert entry["kind"] == "bar" and entry["source"] == "reading"
    assert [m["value"] for m in entry["to"]] == ["note_4.", "note_8"]
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


def _rebuild(tmp_path, staves):
    rebuilt = tmp_path / "rebuilt.mscx"
    rebuilt.write_text(_score(staves))
    return rebuilt


def _rebuilt_tokens(rebuilt, staff, measure=3):
    root = etree.parse(str(rebuilt)).getroot()
    return score_fixes._bar_tokens(score_fixes._measure(root, staff, measure))


def test_a_pick_follows_its_notes_when_the_parts_are_regrouped(make_song, tmp_path):
    # #291: the grid was answered again, and the part the pick was made on is now
    # the second staff, under another name. Replaying it on staff 1 failed the clean.
    song = make_song()
    [offer] = bar_readings.offers(song)
    bar_readings.record_pick(song, offer["id"], "b")
    rebuilt = _rebuild(tmp_path, ((1, "S1", 7), (2, "S2b", 0)))
    logged = []
    assert pipeline.apply_recorded_fixes(str(rebuilt), song.dir, logged.append) == 1
    assert _rebuilt_tokens(rebuilt, 2) == ["quarter.:48", "eighth:50"]
    assert _rebuilt_tokens(rebuilt, 1) == ["quarter:55", "quarter:57"]
    [entry] = _fixes(song)
    assert (entry["staff"], entry["part"]) == (2, "S2b")
    assert any("Moved the reading picked for bar 3 from B1 (staff 1) to S2b (staff 2)" in line
               for line in logged)
    # Moved once, it stays put on the next clean.
    logged.clear()
    pipeline.apply_recorded_fixes(str(_rebuild(tmp_path, ((1, "S1", 7), (2, "S2b", 0)))),
                                  song.dir, logged.append)
    assert not any("Moved" in line for line in logged)


def test_a_pick_whose_voice_is_gone_is_dropped_not_fatal(make_song, tmp_path):
    song = make_song()
    [offer] = bar_readings.offers(song)
    bar_readings.record_pick(song, offer["id"], "b")
    rebuilt = _rebuild(tmp_path, ((1, "S1", 7),))
    logged = []
    assert pipeline.apply_recorded_fixes(str(rebuilt), song.dir, logged.append) == 0
    assert _fixes(song) == []
    assert any("Dropped the reading picked for bar 3" in line for line in logged)


def test_picks_on_voices_reading_alike_land_on_one_staff_each(make_song, tmp_path):
    doubled = json.loads(json.dumps(READINGS))
    doubled["bars"].append({**doubled["bars"][0], "voice": "2"})
    song = make_song(staves=((1, "B1", 0), (2, "B2", 0)), readings=doubled)
    first, second = bar_readings.offers(song)
    bar_readings.record_pick(song, first["id"], "b")
    bar_readings.record_pick(song, second["id"], "b")
    # Both parts move down past a new top staff.
    rebuilt = _rebuild(tmp_path, ((1, "S1", 7), (2, "B1", 0), (3, "B2", 0)))
    assert pipeline.apply_recorded_fixes(str(rebuilt), song.dir) == 2
    assert sorted(e["staff"] for e in _fixes(song)) == [2, 3]
    assert _rebuilt_tokens(rebuilt, 2) == _rebuilt_tokens(rebuilt, 3) == ["quarter.:48",
                                                                         "eighth:50"]


def test_a_hand_written_rhythm_entry_stays_strict(make_song, tmp_path):
    song = make_song()
    [offer] = bar_readings.offers(song)
    bar_readings.record_pick(song, offer["id"], "b")
    [entry] = _fixes(song)
    del entry["source"]
    with open(song.path("fixes.json"), "w") as fh:
        json.dump([entry], fh)
    rebuilt = _rebuild(tmp_path, ((1, "S1", 7), (2, "S2b", 0)))
    with pytest.raises(RuntimeError, match="no longer matches"):
        pipeline.apply_recorded_fixes(str(rebuilt), song.dir)
    assert _fixes(song)[0]["staff"] == 1


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


def test_an_offer_past_a_deleted_bar_is_read_and_picked_in_the_cleaned_numbering(
        make_song, tmp_path):
    # The scan read an extra empty bar in system 1, so its bars run one ahead of the
    # cleaned score, which a `delbar` took it out of (#346).
    song = make_song()
    song.data["scan"]["systems"]["1"]["bars"] = 2
    song.save()
    delbar = {"kind": "delbar", "measure": 2, "from": ["measure:R"], "why": "invented"}
    with open(song.path("fixes.json"), "w") as fh:
        json.dump([delbar], fh)
    [offer] = bar_readings.offers(song)
    assert offer["measure"] == 3 and offer["staff"] == 1
    bar_readings.record_pick(song, offer["id"], "b")
    assert _tokens(song) == ["quarter.:48", "eighth:50"]
    # The next clean starts from the scan, invented bar and all, and keeps the pick.
    raw = _score(((1, "B1", 0),)).replace(
        "</Measure><Measure>",
        "</Measure><Measure><voice><Rest><durationType>measure</durationType>"
        "<duration>2/4</duration></Rest></voice></Measure><Measure>", 1)
    rebuilt = tmp_path / "rebuilt.mscx"
    rebuilt.write_text(raw)
    picked = _fixes(song)
    assert pipeline.apply_recorded_fixes(str(rebuilt), song.dir) == 2
    assert _rebuilt_tokens(rebuilt, 1) == ["quarter.:48", "eighth:50"]
    assert _fixes(song) == picked
