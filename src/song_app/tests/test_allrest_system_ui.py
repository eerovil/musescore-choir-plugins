"""The clean grid says so when every staff rests through a printed system (#482).

Such a system has no staves to name, and an empty block read as a missing system.
"""
import os
import re

import pytest

from src.song_app.tests.test_overlong_clean_ui import _evidence, live  # noqa: F401  (fixture)

from lxml import etree
from playwright.sync_api import expect

from src.clean_score.tests.scorebuilder import build_score
from src.clean_score.utils import per_system
from src.clean_score.tests.test_named_part_rests import NAMES, _rest_through

pytestmark = pytest.mark.browser


@pytest.fixture
def all_rest(live):  # noqa: F811
    base, song = live
    root = build_score(staff_ids=(1, 2), measures=3, chords=2, names=NAMES, line_breaks=(1, 2))
    _rest_through(root, 1, 2)
    _rest_through(root, 2, 2)
    path = os.path.join(os.path.dirname(state_path(song)), "in.mscx")
    with open(path, "wb") as fh:
        fh.write(etree.tostring(root))
    return base, song


def state_path(song):
    return song.path(".song.json")


def test_a_system_where_every_staff_rests_shows_one_line(all_rest, page):
    base, song = all_rest
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/#/song/{song.slug}")
    expect(page.locator(".sysblock")).to_have_count(3)
    rest = page.locator(".sysblock.allrest")
    expect(rest).to_have_count(1)
    expect(rest).to_contain_text("measures 2–2")
    expect(rest).to_contain_text("Every staff rests here; names carry on to the next system.")
    _evidence(page, "allrest-system.png")
    assert not errors, f"the panel raised: {errors}"


@pytest.fixture
def three_systems(live, tmp_path):  # noqa: F811
    base, song = live
    root = build_score(staff_ids=(1, 2), measures=3, chords=2, names=NAMES, line_breaks=(1, 2))
    with open(os.path.join(os.path.dirname(state_path(song)), "in.mscx"), "wb") as fh:
        fh.write(etree.tostring(root))
    with per_system.use_answer_file(str(tmp_path / "answers.json")):
        yield base, song


def _no_voices(page, n):
    return page.locator(".sysblock").nth(n).locator("input[data-rest]")


def test_no_voices_here_switches_a_system_off_and_is_saved(three_systems, page):
    """D5: the tick turns the system's rows off, is saved as `"*": "rest"`, and a
    reload shows it ticked again with the rows still off."""
    base, song = three_systems
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/#/song/{song.slug}")
    expect(page.locator(".sysblock")).to_have_count(3)
    expect(page.locator(".sysblock").nth(1)).to_contain_text(
        "No voices here (piano or instruments only)")
    first = page.locator(".sysblock").nth(0).locator("input[data-sys]")
    first.nth(0).fill("T1")
    first.nth(1).fill("T2")
    _no_voices(page, 1).check()
    middle = page.locator(".sysblock").nth(1)
    expect(middle).to_have_class(re.compile(r"\brestsys\b"))
    for i in range(2):
        expect(middle.locator("input[data-sys]").nth(i)).to_be_disabled()
    expect(page.locator(".sysblock").nth(2).locator("input[data-sys]").nth(0)).to_be_enabled()
    _evidence(page, "no-voices-here-desktop.png")
    page.get_by_role("button", name="Save assignments").click()
    expect(page.get_by_role("button", name="Saved ✓")).to_be_visible()

    saved = per_system.saved_answers(os.path.join(os.path.dirname(state_path(song)), "in.mscx"))
    assert saved[1] == {per_system.ALL_REST: per_system.REST, 1: "", 2: ""}
    assert saved[0] == {1: "T1", 2: "T2"}

    page.reload()
    expect(_no_voices(page, 1)).to_be_checked()
    expect(page.locator(".sysblock").nth(1).locator("input[data-sys]").nth(0)).to_be_disabled()
    _no_voices(page, 1).uncheck()
    expect(page.locator(".sysblock").nth(1).locator("input[data-sys]").nth(0)).to_be_enabled()
    assert not errors, f"the panel raised: {errors}"


def test_no_voices_here_fits_a_phone(three_systems, page):
    base, song = three_systems
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base}/#/song/{song.slug}")
    box = page.locator(".sysblock").nth(1).locator(".restpick").bounding_box()
    assert box and box["x"] >= 0 and box["x"] + box["width"] <= 390
    _evidence(page, "no-voices-here-phone.png")
