"""Singing parts get a voice instrument, so MuseScore 4 puts new dynamics above them (#481)."""
import os
import re
import subprocess

import pytest
from lxml import etree

from src.clean_score.utils.voice_instruments import (
    needs_voice_instruments, set_voice_instruments, voice_for)


def _part(name, staff, instrument="<Instrument><longName>{n}</longName><trackName>{n}</trackName>"
          "<Channel><program value=\"0\"/><controller ctrl=\"10\" value=\"63\"/></Channel>"
          "</Instrument>"):
    return (f"<Part><Staff id=\"{staff}\"/><trackName>{name}</trackName>"
            + instrument.format(n=name) + "</Part>")


NOTE = "<Chord><durationType>whole</durationType><Note><pitch>60</pitch></Note></Chord>"
REST = "<Rest><durationType>measure</durationType></Rest>"


def _score(parts, music):
    staves = "".join(f"<Staff id=\"{i + 1}\"><Measure><voice>{m}</voice></Measure></Staff>"
                     for i, m in enumerate(music))
    return etree.fromstring(f"<museScore><Score>{''.join(parts)}{staves}</Score></museScore>")


def _ids(root):
    return [(i.get("id"), i.findtext("instrumentId")) for i in root.iter("Instrument")]


@pytest.mark.parametrize("name, ident", [
    ("S1", "voice.soprano"), ("S1b", "voice.soprano"), ("Soprano 2", "voice.soprano"),
    ("Sopraano", "voice.soprano"), ("A", "voice.alto"), ("A2b", "voice.alto"),
    ("Altto 1", "voice.alto"), ("T1", "voice.tenor"), ("Tenori", "voice.tenor"),
    ("B2", "voice.bass"), ("Basso", "voice.bass"), ("Bar", "voice.baritone"),
    ("Baritone", "voice.baritone"), ("Alto 1-2", "voice.alto"), ("M", "voice.vocals"), ("W", "voice.vocals"),
    ("Solo", "voice.vocals"), ("Track 3", "voice.vocals"), ("", "voice.vocals"),
])
def test_a_part_name_gives_the_voice(name, ident):
    assert voice_for(name)[1] == ident


def test_each_singing_part_gets_its_voice_and_keeps_its_sound():
    root = _score([_part("S1", 1), _part("T1", 2), _part("B", 3)], [NOTE, NOTE, NOTE])
    before = [etree.tostring(c) for c in root.iter("Channel")]

    assert set_voice_instruments(root) == 3

    assert _ids(root) == [("soprano", "voice.soprano"), ("tenor", "voice.tenor"),
                          ("bass", "voice.bass")]
    # The sound is the Channel's; the MIDI must not change.
    assert [etree.tostring(c) for c in root.iter("Channel")] == before
    # The id sits after the names, where MuseScore writes it.
    first = root.find(".//Instrument")
    assert [c.tag for c in first][:3] == ["longName", "trackName", "instrumentId"]


def test_a_click_staff_and_a_drumset_are_left_alone():
    drums = ("<Instrument id=\"drumset\"><longName>{n}</longName><trackName>{n}</trackName>"
             "<useDrumset>1</useDrumset></Instrument>")
    root = _score([_part("S1", 1), _part("Click", 2), _part("Drumset", 3, drums)],
                  [NOTE, REST, NOTE])

    assert set_voice_instruments(root) == 1
    assert _ids(root) == [("soprano", "voice.soprano"), (None, None), ("drumset", None)]


def test_an_instrument_someone_chose_is_kept():
    flute = ("<Instrument id=\"flute\"><longName>{n}</longName><trackName>{n}</trackName>"
             "<instrumentId>wind.flutes.flute</instrumentId></Instrument>")
    alto = ("<Instrument id=\"alto\"><longName>{n}</longName><trackName>{n}</trackName>"
            "<instrumentId>voice.alto</instrumentId></Instrument>")
    piano = ("<Instrument id=\"piano\"><longName>{n}</longName><trackName>{n}</trackName>"
             "<instrumentId>keyboard.piano.grand</instrumentId></Instrument>")
    # S1 carries a flute, S2 an alto voice on a soprano name, "Piano" a piano.
    root = _score([_part("S1", 1, flute), _part("S2", 2, alto), _part("Piano", 3, piano)],
                  [NOTE, NOTE, NOTE])

    assert not needs_voice_instruments(root)
    assert set_voice_instruments(root) == 0


@pytest.mark.parametrize("ident, instrument_id", [
    ("piano", "keyboard.piano"),               # MuseScore 3's import of a MusicXML part
    ("grand-piano", "keyboard.piano.grand"),   # MuseScore 4's save of a part with no id
])
def test_a_singing_part_left_as_a_piano_gets_its_voice(ident, instrument_id):
    piano = (f"<Instrument id=\"{ident}\"><longName>{{n}}</longName><trackName>{{n}}</trackName>"
             f"<instrumentId>{instrument_id}</instrumentId></Instrument>")
    root = _score([_part("T2", 1, piano), _part("Solo", 2, piano)], [NOTE, NOTE])

    assert set_voice_instruments(root) == 2
    assert _ids(root) == [("tenor", "voice.tenor"), ("voice", "voice.vocals")]
    assert len(root.findall(".//instrumentId")) == 2


def test_a_part_named_as_an_instrument_is_left_alone():
    root = _score([_part("S1", 1), _part("Piano RH", 2), _part("Violin 1", 3),
                   _part("Urut", 4)], [NOTE, NOTE, NOTE, NOTE])

    assert set_voice_instruments(root) == 1
    assert _ids(root)[1:] == [(None, None)] * 3


def test_a_second_run_changes_nothing():
    root = _score([_part("A1", 1)], [NOTE])
    set_voice_instruments(root)
    once = etree.tostring(root)

    assert not needs_voice_instruments(root)
    assert set_voice_instruments(root) == 0
    assert etree.tostring(root) == once


def test_the_clean_writes_them(tmp_path):
    from src.clean_score.main import main
    here = os.path.join(os.path.dirname(__file__), "test_files")
    out = tmp_path / "out.mscx"
    main(os.path.join(here, "simple_1_input.mscx"), str(out))

    root = etree.parse(str(out)).getroot()
    singing = [i for i in root.iter("Instrument") if i.findtext("instrumentId")]
    assert singing and all(i.findtext("instrumentId").startswith("voice.") for i in singing)


# --- MuseScore 4 itself (skips without it) -----------------------------------------

def _musescore_4():
    for cli in (os.environ.get("MUSESCORE4_CLI_PATH"), os.environ.get("MUSESCORE_CLI_PATH"),
                os.path.expanduser("~/.local/bin/MuseScore4.AppImage")):
        if cli and os.path.exists(cli) and "4" in os.path.basename(cli):
            return cli
    return None


def _placement(cli, mscx, tmp_path, tag):
    """Where MuseScore 4 puts a dynamic it was given no side for, in this score."""
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    work = tmp_path / tag
    work.mkdir()
    saved = work / "saved.mscx"
    subprocess.run([cli, str(mscx), "-o", str(saved)], env=env, timeout=180,
                   capture_output=True)
    text = saved.read_text(encoding="utf-8")
    # A dynamic added in MuseScore 4 carries no side; one converted from 3.x does.
    text = re.sub(r"<direction>\w+</direction>", "", text)
    saved.write_text(text, encoding="utf-8")
    xml = work / "out.musicxml"
    subprocess.run([cli, str(saved), "-o", str(xml)], env=env, timeout=180,
                   capture_output=True)
    found = re.search(r'<direction placement="(\w+)">\s*<direction-type>\s*<dynamics',
                      xml.read_text(encoding="utf-8"))
    return found.group(1) if found else None


@pytest.mark.skipif(not _musescore_4(), reason="needs MuseScore 4")
def test_musescore_4_puts_a_new_dynamic_above_a_voice_and_below_a_piano(tmp_path):
    # Program 0, as cleaned scores carry: with no id MuseScore 4 guesses from it.
    root = _score([_part("S1", 1)], [
        "<TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>"
        "<Dynamic><subtype>mf</subtype></Dynamic>" + NOTE])
    root.set("version", "3.02")
    plain = tmp_path / "plain.mscx"
    plain.write_bytes(etree.tostring(root))
    set_voice_instruments(root)
    voiced = tmp_path / "voiced.mscx"
    voiced.write_bytes(etree.tostring(root))

    cli = _musescore_4()
    assert _placement(cli, plain, tmp_path, "plain") == "below"
    assert _placement(cli, voiced, tmp_path, "voiced") == "above"
