//==========================================================================================
//  CopyLyricsFromTopStaff Plugin for MuseScore 3.x
//  Copies lyrics from the topmost staff in the selection to all lower staves.
//  Existing lyrics in lower staves are removed. Each syllable is matched by tick.
//  Any remaining syllables from the top staff are matched to notes without lyrics,
//  but only when the note is between two matched lyrics.
//==========================================================================================
import QtQuick 2.1
import QtQuick.Controls 1.0
import QtQuick.Dialogs 1.1
import MuseScore 3.0

MuseScore {
    version: "1.1"
    description: "Copy lyrics from topmost staff in selection to all others."
    menuPath: "Plugins.Copy Lyrics from Top Staff"
    pluginType: "dialog"
    width: 400
    height: 100

    function showError(msg) {
        console.log("Error: " + msg);
        ctrlMessageDialog.text = qsTr(msg);
        ctrlMessageDialog.visible = true;
    }

    function copyLyrics() {
        console.log("Starting copyLyrics()");

        var cursor = curScore.newCursor();
        cursor.rewind(1);

        if (!cursor.segment) {
            showError("You must select a region.");
            return;
        }

        var startStaff = cursor.staffIdx;
        cursor.rewind(2);
        var endTick = (cursor.tick === 0) ? curScore.lastSegment.tick + 1 : cursor.tick;
        var endStaff = cursor.staffIdx;
        cursor.rewind(1);

        var topStaff = Math.min(startStaff, endStaff);
        console.log("Top staff index: " + topStaff);

        // Collect lyrics from top staff
        var topLyrics = [];
        var lyricCursor = curScore.newCursor();
        lyricCursor.rewind(1);
        lyricCursor.staffIdx = topStaff;

        while (lyricCursor.segment && lyricCursor.tick < endTick) {
            if (lyricCursor.element && lyricCursor.element.type === Element.CHORD) {
                var chord = lyricCursor.element;
                for (var i = 0; i < chord.lyrics.length; i++) {
                    var l = chord.lyrics[i];
                    topLyrics.push({ tick: lyricCursor.tick, text: l.text, syllabic: l.syllabic });
                }
            }
            lyricCursor.next();
        }

        console.log("Collected " + topLyrics.length + " lyrics from top staff.");

        curScore.startCmd();

        for (var staff = topStaff + 1; staff <= endStaff; staff++) {
            var clearCursor = curScore.newCursor();
            clearCursor.rewind(1);
            clearCursor.staffIdx = staff;
            clearCursor.voice = 0;

            var matchedTicks = [];
            var notesNoLyrics = [];

            while (clearCursor.segment && clearCursor.tick < endTick) {
                if (clearCursor.element && clearCursor.element.type === Element.CHORD) {
                    var chord = clearCursor.element;
                    if (chord.lyrics.length > 0) {
                        matchedTicks.push(clearCursor.tick);
                    } else {
                        notesNoLyrics.push({ tick: clearCursor.tick, element: chord });
                    }
                    for (var i = chord.lyrics.length - 1; i >= 0; i--) {
                        chord.remove(chord.lyrics[i]);
                    }
                }
                clearCursor.next();
            }

            // Re-apply from top staff using tick matching
            var applyCursor = curScore.newCursor();
            applyCursor.rewind(1);
            applyCursor.staffIdx = staff;
            applyCursor.voice = 0;

            var usedTopTicks = {};

            while (applyCursor.segment && applyCursor.tick < endTick) {
                if (applyCursor.element && applyCursor.element.type === Element.CHORD) {
                    var tick = applyCursor.tick;
                    for (var j = 0; j < topLyrics.length; j++) {
                        if (topLyrics[j].tick === tick && !usedTopTicks[tick]) {
                            var lyric = newElement(Element.LYRICS);
                            lyric.text = topLyrics[j].text;
                            lyric.syllabic = topLyrics[j].syllabic;
                            applyCursor.element.add(lyric);
                            usedTopTicks[tick] = true;
                            matchedTicks.push(tick);
                            break;
                        }
                    }
                }
                applyCursor.next();
            }

            // Sort matched ticks
            matchedTicks.sort(function (a, b) { return a - b; });

            // Fill between matched ticks
            for (var i = 0; i < notesNoLyrics.length && topLyrics.length > 0; i++) {
                var note = notesNoLyrics[i];
                var tick = note.tick;

                // Find boundaries
                var before = null;
                var after = null;
                for (var k = 0; k < matchedTicks.length; k++) {
                    if (matchedTicks[k] < tick) before = matchedTicks[k];
                    if (matchedTicks[k] > tick) {
                        after = matchedTicks[k];
                        break;
                    }
                }

                if (before !== null && after !== null) {
                    // Try to find a top lyric between these ticks that wasn't used
                    for (var j = 0; j < topLyrics.length; j++) {
                        var lyr = topLyrics[j];
                        if (!usedTopTicks[lyr.tick] && lyr.tick > before && lyr.tick < after) {
                            var lyric = newElement(Element.LYRICS);
                            lyric.text = lyr.text;
                            note.element.add(lyric);
                            usedTopTicks[lyr.tick] = true;
                            console.log("Inserted unmatched lyric '" + lyr.text + "' at tick " + tick);
                            break;
                        }
                    }
                }
            }
        }

        curScore.endCmd();
        console.log("Finished copyLyrics()");
    }

    onRun: {
        console.log("Plugin started");
        copyLyrics();
        Qt.quit();
    }

    Rectangle {
        width: 400
        height: 100
        color: "grey"

        MessageDialog {
            id: ctrlMessageDialog
            icon: StandardIcon.Warning
            title: "Lyrics Copy Error"
            text: "Something went wrong."
            visible: false
            onAccepted: visible = false
        }
    }
}
