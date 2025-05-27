//==========================================================================================
//  AddSpacerPercussion Plugin for MuseScore 3.x
//  Adds a spacer percussion staff and fills it with 16th note rests across the score.
//==========================================================================================
import QtQuick 2.1
import QtQuick.Controls 1.0
import QtQuick.Dialogs 1.1
import MuseScore 3.0

MuseScore {
    version: "1.0"
    description: "Add a spacer percussion staff filled with 16th rests."
    menuPath: "Plugins.Add Spacer Percussion"
    pluginType: "dialog"
    width: 300
    height: 150

    function ensureSpacerPercussionTrack() {
        for (var i = 0; i < curScore.parts.length; i++) {
            var part = curScore.parts[i];
            if (part && part.partName === "Drumset") {
                console.log("Percussion staff already present");
                return i;
            }
            console.log("Checking part " + i + ": " + (part ? part.partName : "null"));
        }

        console.log("Adding percussion spacer track");
        curScore.startCmd();
        curScore.appendPart("drumset");
        curScore.endCmd();

        return curScore.parts.length - 1;
    }

    function fillSpacerWithSixteenthRests(staffIdx) {
        if (staffIdx === -1) {
            console.log("Spacer staff index invalid");
            return;
        }

        curScore.startCmd();
        var cursor = curScore.newCursor();
        cursor.rewind(0);
        cursor.staffIdx = staffIdx;
        cursor.voice = 0;
        cursor.setDuration(1, 16);
        while (cursor.segment) {
            cursor.addRest();
            cursor.next();
        }
        curScore.endCmd();
    }

    function runSpacerSetup() {
        var spacerIdx = ensureSpacerPercussionTrack();
        var staff = curScore.parts[spacerIdx];
        fillSpacerWithSixteenthRests(spacerIdx);
        Qt.quit();
    }

    onRun: {
        runSpacerSetup();
    }

    // No UI needed for this one
}
