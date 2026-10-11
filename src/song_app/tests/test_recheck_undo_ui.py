"""Check again and Undo last clean, in a real browser (#391).

The routes are pinned in `test_recheck_undo.py`. Here: the Review panel's Check again
button turns two stale rows into answers about the score that is there, and the Clean
panel offers Undo only when there is a score to go back to, asks first, and puts it
back.
"""
import os

import pytest

from src.song_app.tests.test_score_file_ui import _evidence, live  # noqa: F401
from src.song_app import clean_backup, pipeline, state
from src.song_app.tests.test_score_file import CLEANED, FIXED, SCORE

pytestmark = pytest.mark.browser


def _open(page, base, slug, stage):
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/#/song/{slug}")
    page.wait_for_selector(".stagebar")
    page.locator(".stagebar .step", has_text=stage).first.click()
    return errors


def _stale(song):
    """A song whose score was changed by hand after its clean: both checks stale."""
    with open(song.cleaned_path(), "w", encoding="utf-8") as fh:
        fh.write(FIXED)
    with open(song.path("talviuni.mscx"), "w", encoding="utf-8") as fh:
        fh.write(SCORE)
    fresh = state.load(song.slug)
    fresh.data["sources"] = {"xml": "talviuni.mscx"}
    fresh.data["cleaned_fingerprint"] = state.file_fingerprint(song.cleaned_path())
    fresh.data["verification"] = {
        "notes": {"status": "passed", "checked_against": "sha1:old"},
        "musescore": {"status": "passed", "checked_against": "sha1:old"},
    }
    fresh.set_stage("review")
    fresh.save()
    return fresh


def test_check_again_answers_the_stale_rows(live, page, monkeypatch):  # noqa: F811
    base, song, _tmp = live
    song = _stale(song)
    monkeypatch.setattr(pipeline, "musescore_check", lambda _path: [])
    errors = _open(page, base, song.slug, "Review")
    full = page.locator(".review-full")
    full.get_by_text("Note comparison applies to an older cleaned score").wait_for(state="attached")

    page.get_by_role("button", name="Check again").click()
    page.locator(".review-full").get_by_text(
        "MuseScore 3 opens it without calling it corrupted").wait_for(state="attached")
    assert page.locator(".review-full").get_by_text("older cleaned score").count() == 0
    # The hand edit changed a note, and the comparison now says so rather than "stale".
    assert page.locator(".review-full").get_by_text("pitch, measure, or duration differs").count() == 1
    page.locator(".review-panel").scroll_into_view_if_needed()
    _evidence(page, "check-again-review.png")
    assert not errors, f"the panel raised: {errors}"


def test_undo_last_clean_puts_the_score_back(live, page):  # noqa: F811
    base, song, _tmp = live
    song = _stale(song)
    kept = clean_backup.stash(song)
    with open(song.cleaned_path(), "w", encoding="utf-8") as fh:
        fh.write(SCORE)       # what the clean made
    clean_backup.mark_made(kept, state.file_fingerprint(song.cleaned_path()))
    fresh = state.load(song.slug)
    fresh.data["cleaned_fingerprint"] = state.file_fingerprint(song.cleaned_path())
    fresh.set_stage("fix")
    fresh.save()

    errors = _open(page, base, song.slug, "Clean")
    undo = page.get_by_role("button", name="Undo last clean")
    undo.wait_for()
    _evidence(page, "undo-last-clean.png")
    asked = []
    page.on("dialog", lambda d: (asked.append(d.message), d.accept()))
    undo.click()
    page.get_by_role("button", name="Undo last clean").wait_for(state="detached")
    assert asked and "Put back the cleaned score" in asked[0]
    with open(song.cleaned_path(), encoding="utf-8") as fh:
        assert fh.read() == FIXED
    assert state.load(song.slug).stage == "review"
    assert not errors, f"the panel raised: {errors}"


def test_no_undo_without_a_backup(live, page):  # noqa: F811
    base, song, _tmp = live
    errors = _open(page, base, song.slug, "Clean")
    page.get_by_role("button", name="Re-clean (discards manual edits)").wait_for()
    assert page.get_by_role("button", name="Undo last clean").count() == 0
    assert not errors, f"the panel raised: {errors}"


def test_undo_is_not_offered_after_the_score_changed(live, page):  # noqa: F811
    base, song, _tmp = live
    clean_backup.mark_made(clean_backup.stash(song), "sha1:what-the-clean-made")
    errors = _open(page, base, song.slug, "Clean")
    page.get_by_text("Undo last clean is not offered").wait_for()
    assert page.get_by_role("button", name="Undo last clean").count() == 0
    _evidence(page, "undo-not-offered.png")
    assert not errors, f"the panel raised: {errors}"
