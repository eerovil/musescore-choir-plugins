"""Per-voice mixes are a volume edit on the score, not a GUI session."""

from lxml import etree

from src.scrollvideo.audio import (BACKGROUND_VOLUME, FOCUS_VOLUME, VOLUME_CTRL,
                                   part_names, set_mix)

SCORE = """<museScore><Score>
  <Part><trackName>S1</trackName><Instrument><Channel>
    <program value="0"/><controller ctrl="10" value="63"/></Channel></Instrument></Part>
  <Part><trackName>B1</trackName><Instrument><Channel>
    <program value="0"/><controller ctrl="10" value="63"/></Channel></Instrument></Part>
</Score></museScore>"""


def _volumes(root):
    return {(p.findtext("trackName") or "").strip():
            next(c.get("value") for c in p.iter("controller") if c.get("ctrl") == VOLUME_CTRL)
            for p in root.iter("Part")}


def test_part_names_follow_score_order():
    assert part_names(etree.fromstring(SCORE)) == ["S1", "B1"]


def test_focus_part_is_loud_and_the_rest_are_background():
    root = set_mix(etree.fromstring(SCORE), "B1")
    assert _volumes(root) == {"S1": str(BACKGROUND_VOLUME), "B1": str(FOCUS_VOLUME)}


def test_no_focus_means_an_even_mix():
    root = set_mix(etree.fromstring(SCORE), None)
    assert set(_volumes(root).values()) == {str(FOCUS_VOLUME)}


def test_existing_volume_is_replaced_not_duplicated():
    root = set_mix(set_mix(etree.fromstring(SCORE), "S1"), "B1")
    for part in root.iter("Part"):
        volumes = [c for c in part.iter("controller") if c.get("ctrl") == VOLUME_CTRL]
        assert len(volumes) == 1
    assert _volumes(root)["B1"] == str(FOCUS_VOLUME)


def test_pan_and_program_are_left_alone():
    root = set_mix(etree.fromstring(SCORE), "S1")
    channel = next(root.iter("Channel"))
    assert channel.find("program") is not None
    assert [c.get("value") for c in channel.findall("controller") if c.get("ctrl") == "10"] == ["63"]
