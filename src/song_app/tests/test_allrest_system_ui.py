"""The clean grid says so when every staff rests through a printed system (#482).

Such a system has no staves to name, and an empty block read as a missing system.
"""
import os

import pytest

from src.song_app.tests.test_overlong_clean_ui import _evidence, live  # noqa: F401  (fixture)

from lxml import etree
from playwright.sync_api import expect

from src.clean_score.tests.scorebuilder import build_score
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
