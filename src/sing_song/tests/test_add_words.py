import json

from src.sing_song import add_words


class FakeSite:
    def __init__(self, rows):
        self.rows = rows

    def query(self, sql):
        return [{"results": self.rows}]


def row(choir, slug, prefix, names, sung=True, compact=True):
    """A songs row, its parts written as the site's script does (compact) or the app's publish."""
    parts = [{"name": n, **({"file": f"parts/{i}-{n}.mp3", "sung": True} if sung else {})} for i, n in enumerate(names, 1)]
    return {"choir": choir, "slug": slug, "prefix": prefix,
            "parts": json.dumps(parts, separators=(",", ":")) if compact else json.dumps(parts, ensure_ascii=False)}


def test_a_version_two_lists_share_is_done_once_and_slugs_narrow_it():
    # a joy-alumnit song is on joy too, with one copy of its files (#101)
    site = FakeSite([row("joy", "katti", "songs/joy/katti/1/", ["Tenori", "Basso"]),
                     row("joy-alumnit", "katti", "songs/joy/katti/1/", ["Tenori", "Basso"]),
                     row("jm", "muu", "songs/jm/muu/2/", ["T1"])])
    names = lambda versions: [(prefix, slug, [p["name"] for p in parts]) for prefix, slug, parts in versions]
    assert names(add_words.sung_versions(site)) == [("songs/joy/katti/1/", "katti", ["Tenori", "Basso"]),
                                                    ("songs/jm/muu/2/", "muu", ["T1"])]
    assert names(add_words.sung_versions(site, {"muu"})) == [("songs/jm/muu/2/", "muu", ["T1"])]


def musicxml(*staves):
    """A score with one part per staff; each staff a list of (voice, [eighth notes]), a note a
    syllable (on C4) or (step, syllable)."""
    parts = []
    for voices in staves:
        notes = ""
        for k, (voice, words) in enumerate(voices):
            if k:
                notes += f"<backup><duration>{len(voices[k - 1][1])}</duration></backup>"
            for w in words:
                step, w = w if isinstance(w, tuple) else ("C", w)
                notes += (f"<note><pitch><step>{step}</step><octave>4</octave></pitch><duration>1</duration>"
                          f"<voice>{voice}</voice>" + (f"<lyric number='1'><text>{w}</text></lyric>" if w else "") + "</note>")
        parts.append(f"<part id='P{len(parts) + 1}'><measure number='1'>{notes}</measure></part>")
    return f"<score-partwise>{''.join(parts)}</score-partwise>".encode()


def test_a_part_counts_only_with_every_word_where_it_was_sung():
    live = musicxml([("1", ["kol", "li", "kis", "sa"])], [("1", ["dum", "dum", "dum", "dum"])])
    tb = [{"name": "Tenori"}, {"name": "Basso"}]
    assert add_words.verified_parts(live, live, tb) == [True, True]
    # a word fixed, or two swapped, with the notes as they were (#110 review)
    fixed = musicxml([("1", ["kol", "li", "kas", "sa"])], [("1", ["dum", "dum", "dum", "dum"])])
    swapped = musicxml([("1", ["kol", "li", "sa", "kis"])], [("1", ["dum", "dum", "dum", "dum"])])
    assert add_words.verified_parts(live, fixed, tb) == [False, True]
    assert add_words.verified_parts(live, swapped, tb) == [False, True]
    # moved one note later
    moved = musicxml([("1", ["kol", "li", None, "kis"])], [("1", ["dum", "dum", "dum", "dum"])])
    assert add_words.verified_parts(live, moved, tb) == [False, True]


def test_a_part_whose_voice_has_no_words_in_the_live_file_is_not_checked():
    # S1 and S2 share a staff and the export kept only voice 1's words
    live = musicxml([("1", ["kis", "sa"]), ("2", [None, None])])
    parts = [{"name": "S1", "staff": 0, "voices": [1]}, {"name": "S2", "staff": 0, "voices": [2]}]
    assert add_words.verified_parts(live, live, parts) == [True, False]


def test_a_second_voice_given_words_since_the_singing_fails_its_part():
    # the lower line borrowed voice 1's words when sung; now voice 2 has its own, notes unchanged
    live = musicxml([("1", ["kis", "sa"]), ("2", [None, None])])
    now = musicxml([("1", ["kis", "sa"]), ("2", ["dum", "dum"])])
    assert add_words.verified_parts(live, now, [{"name": "Tenori"}]) == [False]
    assert add_words.verified_parts(live, live, [{"name": "Tenori"}]) == [True]


def test_voices_that_swapped_pitches_fail_their_parts_though_words_and_starts_stay():
    live = musicxml([("1", [("E", "kis"), ("E", "sa")]), ("2", [("C", "dum"), ("C", "dum")])])
    now = musicxml([("1", [("C", "kis"), ("C", "sa")]), ("2", [("E", "dum"), ("E", "dum")])])
    parts = [{"name": "T", "staff": 0, "voices": [1]}, {"name": "B", "staff": 0, "voices": [2]}]
    assert add_words.verified_parts(live, live, parts) == [True, True]
    assert add_words.verified_parts(live, now, parts) == [False, False]


def test_a_version_the_apps_publish_wrote_is_found_and_a_piano_one_is_not():
    # the app writes parts with json.dumps(parts, ensure_ascii=False): "sung": true, with a space
    site = FakeSite([row("jm", "laulu", "songs/jm/laulu/1/", ["T1", "B1"], compact=False),
                     row("jm", "piano", "songs/jm/piano/1/", ["T1"], sung=False, compact=False)])
    assert [v[0] for v in add_words.sung_versions(site)] == ["songs/jm/laulu/1/"]


def test_a_number_changed_in_the_words_since_the_singing_fails_its_part():
    live = musicxml([("1", ["Laulu‿118"])])
    assert add_words.verified_parts(live, live, [{"name": "T"}]) == [True]
    assert add_words.verified_parts(live, musicxml([("1", ["Laulu‿119"])]), [{"name": "T"}]) == [False]
