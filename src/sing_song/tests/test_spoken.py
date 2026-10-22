import sys
from pathlib import Path

import pytest

from src.sing_song.spoken import number_words, spoken_syllables, split_spoken, syllables  # noqa: E402

# The three spoken lines of Laulu reiän tarpeesta (#61), as they were split by hand for the
# first sung version.
LINES = {
    "yleisimmät‿teroituskulmat‿ovat‿118‿ja‿135":
        "y lei sim mät te roi tus kul mat o vat sa ta kah dek san tois ta ja sa ta kol me kym men tä vii si",
    "Poraus‿suoritetaan‿yhtenäisellä‿liikkeellä":
        "Po raus suo ri te taan yh te näi sel lä liik keel lä",
    "Poratessa‿silmäsuojainten‿käyttö‿on‿erityisen":
        "Po ra tes sa sil mä suo jain ten käyt tö on e ri tyi sen",
}


@pytest.mark.parametrize("text,expected", LINES.items())
def test_the_songs_spoken_lines(text, expected):
    assert spoken_syllables(text) == expected.split()


@pytest.mark.parametrize("n,words", [(118, "satakahdeksantoista"), (135, "satakolmekymmentäviisi"),
                                     (10, "kymmenen"), (200, "kaksisataa"), (1918, "tuhatyhdeksänsataakahdeksantoista"),
                                     (0, "nolla")])
def test_number_words(n, words):
    assert number_words(n) == words


@pytest.mark.parametrize("word,expected", [
    ("kierroksia", "kier rok si a"),   # i-a is not a diphthong
    ("radio", "ra di o"),              # io neither
    ("silmäsuojainten", "sil mä suo jain ten"),   # uo kept in a compound
    ("vaan,", "vaan,"),                # punctuation stays on
    ("Ää", "Ää"),
])
def test_syllables(word, expected):
    assert syllables(word) == expected.split()


def test_split_spoken_keeps_the_notes_length():
    notes = [{"pitch": 59, "beats": 4.0, "sec": 5.0, "syl": "ovat‿118", "syllabic": "single",
              "melisma": False},
             {"pitch": 60, "beats": 1.0, "sec": 1.25, "syl": "as", "syllabic": "begin", "melisma": False}]
    out = split_spoken(notes)
    assert [n["syl"] for n in out] == "o vat sa ta kah dek san tois ta as".split()
    assert sum(n["sec"] for n in out) == pytest.approx(6.25)
    assert sum(n["beats"] for n in out) == pytest.approx(5.0)
    assert {n["pitch"] for n in out[:-1]} == {59}
    assert out[-1] is notes[1]
