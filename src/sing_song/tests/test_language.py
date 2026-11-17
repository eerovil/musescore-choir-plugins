"""The song's language decides what a singing run sings in (#484)."""
import json

import pytest

from src.sing_song import language_check
from src.sing_song import main as sing

FINNISH = "Virta venhettä vie aallot laulaa ilta saapuu rantaan hiljaa".split()
ENGLISH = "For unto us a child is born unto us a son is given wonderful counsellor".split()


def saved(tmp_path):
    return json.loads((tmp_path / language_check.FILE).read_text())


def test_the_saved_language_is_used(tmp_path):
    language_check.save(str(tmp_path), "en", "app")
    assert sing.song_language(str(tmp_path), None, None, ENGLISH) == "en"
    assert sing.song_language(str(tmp_path), "en", None, ENGLISH) == "en"


def test_a_lang_that_disagrees_with_the_saved_one_is_refused(tmp_path):
    language_check.save(str(tmp_path), "en", "app")
    with pytest.raises(SystemExit, match=r"saved as English \(en\); --lang fi disagrees.*--change-lang fi"):
        sing.song_language(str(tmp_path), "fi", None, FINNISH)
    assert saved(tmp_path)["language"] == "en"


def test_the_first_lang_is_saved(tmp_path):
    assert sing.song_language(str(tmp_path), "de", None, ENGLISH) == "de"
    assert saved(tmp_path)["language"] == "de" and saved(tmp_path)["by"] == "sing_song.py"


def test_change_lang_changes_it(tmp_path):
    language_check.save(str(tmp_path), "fi", "app")
    assert sing.song_language(str(tmp_path), None, "en", ENGLISH) == "en"
    assert saved(tmp_path)["language"] == "en"
    with pytest.raises(SystemExit, match="disagree"):
        sing.song_language(str(tmp_path), "fi", "la", ENGLISH)
    assert saved(tmp_path)["language"] == "en"


def test_no_language_and_english_words_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="do not look Finnish.*child"):
        sing.song_language(str(tmp_path), None, None, ENGLISH)
    assert not (tmp_path / language_check.FILE).exists()


def test_no_language_and_finnish_words_is_finnish_and_saves_nothing(tmp_path):
    assert sing.song_language(str(tmp_path), None, None, FINNISH) == "fi"
    assert not (tmp_path / language_check.FILE).exists()


def test_a_finnish_song_with_a_few_loan_words_still_looks_finnish():
    # two odd words among many, under MIN_WORDS: not foreign
    assert not language_check.looks_foreign(FINNISH + ["Bethlehem", "Gloria"])
    assert language_check.looks_foreign(ENGLISH)


def test_score_words_reads_lyrics_only(tmp_path):
    mscx = tmp_path / "s.mscx"
    mscx.write_text(
        "<museScore><Score><Staff id='1'>"
        "<Measure><voice><StaffText><text>⚠ rhythm? Allegro</text></StaffText>"
        "<Chord><Lyrics><syllabic>begin</syllabic><text>coun</text></Lyrics></Chord>"
        "<Chord><Lyrics><syllabic>middle</syllabic><text>sel</text></Lyrics></Chord></voice></Measure>"
        "<Measure><voice><Chord><Lyrics><syllabic>end</syllabic><text>lor</text></Lyrics></Chord>"
        "<Chord><Lyrics><text>light,</text></Lyrics></Chord></voice></Measure>"
        "</Staff></Score></museScore>")
    assert language_check.score_words(str(mscx)) == ["counsellor", "light,"]
