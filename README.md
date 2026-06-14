# plugins/

Plugins to help creating practise tracks

Install by linking/copying to ~/Documents/MuseScore3/Plugins

export.qml is used to create mp3:s for each staff. You can also rename the saves quickly for any combination SSAA, SATB, TTBB, SAM (Soprano, alto, men) etc.

voice2.qml splits a selection into two voices (chords are split, lowest note goes to voice 2, single notes are duplicated)

replacelyrics.qml Is a search an replace for lyrics

copylyrics.qml copies topmost lyrics to bottom staves

add_rest_track adds a new staff that contains 16th rests. This makes all measures about evenly spaced

# clean_score.py

Splits a score where two voices share a staff into one-voice-per-staff, names the
parts, and writes a `lyricsStaffMap` so lyrics can be re-applied later.

How to use

* Get a musescore file or musicxml file.

* Run from command line
    ./clean_score.py "path/to/your/file.mscz"

* Output is saved to songs/<name>/<name>_cleaned.mscx

* Force-add empty practice staves with --add, e.g. --add SSAA

* OCR'd scores sometimes have a measure with more than two voices (a chord
  exploded into several voices, or a real extra part for a few bars). By default
  (when run in a terminal) clean_score does interactive re-voicing:
    1. It asks you to name the normal voices top to bottom once, e.g. T1, T2, B.
    2. At each measure with more than two voices it shows the current voicing and
       each voice's notes, and asks for the new voicing — one name per voice, in
       order (blank = drop that voice).
    3. A name from this staff stays put; a new name (e.g. T3) is placed on a new
       staff (rests everywhere else); a name belonging to another part is moved
       into that part's staff.
  Pass --no-interactive to skip all prompts and just reduce such measures to the
  staff's normal voice count (with a warning).

* For really badly-parsed scores, where the physical staves carry different parts
  in different systems (e.g. staff 1 is T1+T2 at the start but T3 from measure 20),
  use --per-system. clean_score walks the score one printed system at a time (split
  at line breaks) and, for each system, asks you to name each staff's voices. It
  then rebuilds the score as one clean staff per part (T1, T2, T3, B, …), pulling
  each part from whichever staff/voice you named in each system and filling rests
  where a part is absent. Empty/unused staves are dropped. Per voice: a blank name
  skips it; if two staves are given the same name in one system, the first wins.
  Each staff prompt shows the previous system's answer in [brackets] — press Enter
  to reuse it (type '-' to clear a staff), so unchanged systems need almost no
  typing. Answers are cached per input file in .persystem_cache.json (repo root), so
  re-running reuses them automatically (and lets the conversion run without a prompt).
  The printed system layout (line breaks) is preserved in the result. For lyrics,
  clean_score writes a per-system staff map so the JSON's printed staff numbers (which
  shift as parts are omitted per system) land on the right output voices.

# lyric_txt.py — fixing lyrics from a PDF

Scores often arrive with garbled OCR'd lyrics. To replace them with clean lyrics
read from the original PDF:

1. Split the score first with clean_score.py (this writes the staff map needed below).

2. Ask an LLM (e.g. ChatGPT) to read the PDF and output JSON in the format of
   lyric_json_prompt.txt (one entry per printed line). Label each line with the voice
   part(s) it belongs to by NAME ("parts": ["T1","T2"]), read from the staff label in
   the score. The LLM output will not be 100% correct — fix it by hand as needed.

3. Import the JSON, replacing the existing OCR lyrics:
       ./lyric_txt.py import laulun_aika.json "songs/Laulun aika/Laulun aika_cleaned.mscx" --replace

   * "parts" by name maps straight to the matching output staff (T1, T2, T3, B …),
     so it works even when a part is omitted on some lines or printed out of order
     (e.g. an ossia T3 on top). List several names for a unison line.
   * If a line has no "parts", import falls back to staff_number + above/below position
     mapped via the per-system staff map (less robust; names are preferred).
   * "parts" also accepts output staff ids (integers) if you prefer.
   * Without --replace, only the measures/staves named in the JSON are changed
     (partial edit); existing lyrics elsewhere are kept.
   * Ties dropped by OCR are recovered automatically by clean_score when a parallel
     voice still has the same-pitch tie. Slurs are NOT auto-recovered (mirroring a slur
     between voices guesses wrong) — fix them by hand in the score. Lyric alignment is
     per-measure, so a missing slur only affects its own measure, not the rest of the line.

You can also export the current lyrics to a checkable text format:
    ./lyric_txt.py export "songs/Laulun aika/Laulun aika_cleaned.mscx" -o lyrics.txt

# record_stemmanauha

IF ONLY RECORDING AUDIO: just run export.qml plugin in musescore
you don't need this script

Install QuickRecorder.
Set up QuickRecorder: Add keyboard shortcuts to start record and stop
	-- Send Shift+Control+Cmd + R to start
    -- Send Shift+Control+Cmd + S to stop

Setup musescore 3 to export with keyboard shortcut.
i.e. install plugins and run plugin export.qml with keyboard shortcut
	-- Send Command-Option-E

Open wanted sheet music in musescore
Test quickrecorder that the recording area is correct

Run this script with the same basename as the directory in songs/
i.e. if your song is in songs/MySong, run
    ./record_stemmanauha.py MySong

media files should appear in song folder. To re-record, delete files
