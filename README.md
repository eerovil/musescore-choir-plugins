# plugins/

Plugins to help creating practise tracks

Install by linking/copying to ~/Documents/MuseScore3/Plugins

export.qml is used to create mp3:s for each staff. You can also rename the saves quickly for any combination SSAA, SATB, TTBB, SAM (Soprano, alto, men) etc.

voice2.qml splits a selection into two voices (chords are split, lowest note goes to voice 2, single notes are duplicated)

replacelyrics.qml Is a search an replace for lyrics

copylyrics.qml copies topmost lyrics to bottom staves

add_rest_track adds a new staff that contains 16th rests. This makes all measures about evenly spaced

# clean_score.py

How to use

* Get a musescore file or musicxml file.

* Run from command line
    ./clean_score.py "path/to/your/file.mscz"

* Also pass the original PDF file if you want to fix the lyrics using Gemini API
    For gemini api, set .env variable GEMINI_API_KEY to your API key

    e.g.
    ./clean_score.py "Sortunut ääni.pdf" "Sortunut-a-a-ni-pdf.xml" 

* Output file will be saved to songs/

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
