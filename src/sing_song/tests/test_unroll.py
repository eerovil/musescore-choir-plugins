import json
import subprocess
import sys
from pathlib import Path

UNROLL = Path(__file__).resolve().parents[1] / "unroll.py"

TIE_NEXT = "<Spanner type='Tie'><next><location><measures>1</measures></location></next></Spanner>"
TIE_PREV = "<Spanner type='Tie'><prev><location><measures>-1</measures></location></prev></Spanner>"
# bar 1: a whole note "la" tied into bar 2, which a repeat plays twice
SCORE = f"""<museScore><Score><Staff id="1">
<Measure><voice><TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>
  <Chord><durationType>whole</durationType><Lyrics><text>la</text></Lyrics>
    <Note>{TIE_NEXT}<pitch>69</pitch></Note></Chord></voice></Measure>
<Measure><startRepeat/><voice>
  <Chord><durationType>whole</durationType><Note>{TIE_PREV}<pitch>69</pitch></Note></Chord></voice></Measure>
</Staff></Score></museScore>"""
# bars 1, 2, 2 again, one second a beat
TIMING = {"points": [[0, 0, 0], [2, 0, 2], [4, 1, 0], [6, 1, 2], [8, 1, 0], [10, 1, 2]], "duration": 12, "measures": 2}


def test_a_note_tied_from_a_bar_the_repeat_skips_is_not_sung(tmp_path):
    # #74: Lemmen nosto's bar 21. MuseScore plays the tied note once, held from bar 1, and
    # leaves it silent when the repeat comes back to it; the singer must too
    (tmp_path / "s.mscx").write_text(SCORE)
    (tmp_path / "t.json").write_text(json.dumps(TIMING))
    out = tmp_path / "o.json"
    subprocess.run([sys.executable, "-I", str(UNROLL), str(tmp_path / "s.mscx"), "1",
                    str(tmp_path / "t.json"), str(out)], check=True, capture_output=True)
    notes = [(n["pitch"], n["syl"], round(n["sec"], 3)) for n in json.loads(out.read_text())]
    assert notes == [(69, "la", 8.0), (None, None, 4.0)]


def unroll(tmp_path, voices, timing, line=""):
    (tmp_path / "s.mscx").write_text(f"""<museScore><Score><Staff id="1"><Measure>{voices}</Measure></Staff></Score></museScore>""")
    (tmp_path / "t.json").write_text(json.dumps(timing))
    out = tmp_path / "o.json"
    import os
    subprocess.run([sys.executable, "-I", str(UNROLL), str(tmp_path / "s.mscx"), "1",
                    str(tmp_path / "t.json"), str(out)], check=True, capture_output=True,
                   env={**os.environ, "LINE": line})
    return [(n["pitch"], n["syl"], round(n["sec"], 3)) for n in json.loads(out.read_text())]


def test_a_note_moved_by_a_location_is_sung_at_its_own_beat(tmp_path):
    # #104: a folded divisi line may start a beat into the bar; it must not be sung from beat 0
    voice = ("<voice><TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>"
             "<location><fractions>1/4</fractions></location>"
             "<Chord><durationType>half</durationType><Lyrics><text>la</text></Lyrics><Note><pitch>72</pitch></Note></Chord>"
             "<Rest><durationType>quarter</durationType></Rest></voice>")
    timing = {"points": [[0, 0, 0], [4, 0, 4]], "duration": 4, "measures": 1}
    notes = unroll(tmp_path, voice, timing)
    assert [n for n in notes if n[0] is not None] == [(72, "la", 2.0)]
    assert notes[0] == (None, None, 1.0)     # the beat before it is silent


def test_voice_two_borrows_the_word_at_the_same_moved_beat(tmp_path):
    v1 = ("<voice><TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>"
          "<Rest><durationType>quarter</durationType></Rest>"
          "<Chord><durationType>half</durationType><Lyrics><text>la</text></Lyrics><Note><pitch>72</pitch></Note></Chord>"
          "<Rest><durationType>quarter</durationType></Rest></voice>")
    v2 = ("<voice><location><fractions>1/4</fractions></location>"
          "<Chord><durationType>half</durationType><Note><pitch>65</pitch></Note></Chord>"
          "<location><fractions>1/4</fractions></location></voice>")
    timing = {"points": [[0, 0, 0], [4, 0, 4]], "duration": 4, "measures": 1}
    notes = unroll(tmp_path, v1 + v2, timing, line="low")
    assert (65, "la", 2.0) in notes


def test_a_second_voice_that_rests_and_then_sings_is_the_low_line(tmp_path):
    # #439 review, the voice2 fix's shape: voice 2 rests a quarter, then holds its own note;
    # the low line sings it and is silent where it rests, not voice 1 again
    v1 = ("<voice><TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>"
          "<Chord><durationType>whole</durationType><Lyrics><text>la</text></Lyrics><Note><pitch>72</pitch></Note></Chord></voice>")
    v2 = ("<voice><Rest><durationType>quarter</durationType></Rest>"
          "<Chord><durationType>half</durationType><dots>1</dots><Lyrics><text>lu</text></Lyrics><Note><pitch>75</pitch></Note></Chord></voice>")
    timing = {"points": [[0, 0, 0], [4, 0, 4]], "duration": 4, "measures": 1}
    assert unroll(tmp_path, v1 + v2, timing, line="low") == [(None, None, 1.0), (75, "lu", 3.0)]
    assert unroll(tmp_path, v1 + v2, timing, line="high") == [(72, "la", 4.0)]
