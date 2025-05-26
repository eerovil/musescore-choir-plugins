//==========================================================================================
//  LowestNoteToVoice2 Plugin for MuseScore 3.x
//  Moves or clones the lowest note of each chord in the selection to voice 2.
//  Aborts if any notes already exist in voice 2.
//==========================================================================================
import QtQuick 2.1
import QtQuick.Controls 1.0
import QtQuick.Dialogs 1.1
import MuseScore 3.0

MuseScore {
    version: "1.0"
    description: "Move or clone the lowest note of each chord to voice 2. Abort if voice 2 notes already exist."
    menuPath: "Plugins.Lowest Note to Voice 2"
    pluginType: "dialog"
    width: 400
    height: 100

    function showError(msg) {
        console.log("Error: " + msg);
        ctrlMessageDialog.text = qsTr(msg);
        ctrlMessageDialog.visible = true;
    }

    function processChords() {
        console.log("Starting processChords()");

        var cursor = curScore.newCursor();
        cursor.rewind(1);

        if (!cursor.segment) {
            showError("You must select a region of notes on a single staff.");
            return;
        }

        var startStaff = cursor.staffIdx;
        cursor.rewind(2);
        var endTick = (cursor.tick === 0) ? curScore.lastSegment.tick + 1 : cursor.tick;
        var endStaff = cursor.staffIdx;

        if (startStaff !== endStaff) {
            showError("You must select a single staff only.");
            return;
        }

        // Check for existing voice 2 notes
        console.log("Checking for existing notes in voice 2...");
        var checkCursor = curScore.newCursor();
        checkCursor.rewind(1);
        checkCursor.voice = 1; // voice 2 is index 1
        checkCursor.staffIdx = startStaff;

        while (checkCursor.segment && checkCursor.tick < endTick) {
            if (checkCursor.element && checkCursor.element.type === Element.CHORD) {
                showError("Voice 2 already contains notes in the selected range.");
                return;
            }
            checkCursor.next();
        }

        curScore.startCmd();
        cursor.rewind(1);
        cursor.voice = 0; // voice 1 is index 0
        cursor.staffIdx = startStaff;

        // Move cursor to insert point in voice 2
        var insertCursor = curScore.newCursor();
        while (cursor.segment && cursor.tick < endTick) {
            if (cursor.element && (cursor.element.type === Element.CHORD || cursor.element.type === Element.REST)) {
                var chord = cursor.element;
                var notes = chord.notes || [];
                var tick = cursor.tick;
                var duration = chord.actualDuration;
                var staffIdx = cursor.staffIdx;

                var lowest;
                if (cursor.element.type === Element.CHORD) {
                    lowest = notes[0];
                    for (var j = 1; j < notes.length; j++) {
                        if (notes[j].pitch < lowest.pitch)
                            lowest = notes[j];
                    }
                }

                console.log("Processing chord at tick " + tick + ", lowest note pitch: " + lowest + ", duration: " + duration);

                insertCursor.rewind(1);
                insertCursor.staffIdx = staffIdx;
                insertCursor.voice = 1; // voice 2

                console.log("insertCursor.segment: " + insertCursor.segment + ", tick: " + insertCursor.tick);
                
                // Try to reach exact tick
                var foundTick = false;
                while (insertCursor.segment && insertCursor.tick <= tick) {
                    if (insertCursor.tick === tick) {
                        insertCursor.setDuration(duration.numerator, duration.denominator);
                        if (chord.type === Element.CHORD) {
                            insertCursor.addNote(lowest.pitch);
                        } else if (chord.type === Element.REST) {
                            insertCursor.addRest(duration);
                        }
                        console.log("Inserted note in voice 2 at tick " + tick + " duration " + duration);
                        foundTick = true;
                        break;
                    }
                    insertCursor.next();
                }

                if (!foundTick) {
                    console.log("Warning: Couldn't find exact tick " + tick + " to insert note. Voice 2 may be too sparse.");
                }

                if (notes.length > 1) {
                    chord.remove(lowest);
                    console.log("Removed lowest note from voice 1");
                }
            }
            cursor.next();
        }

        curScore.endCmd();
        console.log("Finished processChords()");
    }

    onRun: {
        console.log("Plugin started");
        processChords();
        Qt.quit();
    }

    Rectangle {
        width: 400
        height: 100
        color: "grey"

        MessageDialog {
            id: ctrlMessageDialog
            icon: StandardIcon.Warning
            title: "Voice 2 Conflict"
            text: "Voice 2 already contains notes."
            visible: false
            onAccepted: visible = false
        }
    }
}
