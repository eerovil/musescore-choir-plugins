//==========================================================================================
//  SearchAndReplaceLyrics Plugin for MuseScore 3.x
//  Finds lyrics in the score matching a hyphenated sequence and replaces with another.
//==========================================================================================
import QtQuick 2.1
import QtQuick.Controls 1.0
import QtQuick.Dialogs 1.1
import MuseScore 3.0

MuseScore {
    version: "1.0"
    description: "Search and replace lyrics in hyphenated syllables."
    menuPath: "Plugins.Search and Replace Lyrics"
    pluginType: "dialog"
    width: 400
    height: 180

    property string searchPattern: ""
    property string replacePattern: ""

    function showError(msg) {
        console.log("Error: " + msg);
        ctrlMessageDialog.text = qsTr(msg);
        ctrlMessageDialog.visible = true;
    }

    function searchAndReplaceLyrics() {
        console.log("Starting search and replace lyrics");

        if (searchPattern.trim() === "" || replacePattern.trim() === "") {
            showError("Search and Replace fields must not be empty.");
            return;
        }

        var searchSyllables = searchPattern.split("-");
        var replaceSyllables = replacePattern.split("-");

        for (var staff = 0; staff < curScore.nstaves; staff++) {

            var cursor = curScore.newCursor();
            cursor.rewind(0);
            cursor.staffIdx = staff;

            curScore.startCmd();

            console.log("Searching for lyrics matching: " + searchSyllables.join(", "));
            if (!cursor.segment) {
                showError("You must select a region of notes on a single staff.");
                return;
            }
            while (cursor.segment) {
                console.log("Current tick: " + cursor.tick + ", staff: " + cursor.staffIdx);
                var buffer = [];
                var tickBuffer = [];
                var chordBuffer = [];
                var startTick = cursor.tick;

                var tempCursor = curScore.newCursor();
                tempCursor.rewind(0);
                tempCursor.voice = cursor.voice;
                tempCursor.staffIdx = cursor.staffIdx;
                while (tempCursor.segment && tempCursor.tick < cursor.tick)
                    tempCursor.next();

                // Try to match a sequence
                var i = 0;
                var newReplaceSyllables = replaceSyllables.slice(0);
                while (tempCursor.segment && i < searchSyllables.length) {
                    if (tempCursor.element && tempCursor.element.type === Element.CHORD && tempCursor.element.lyrics.length > 0) {
                        var text = tempCursor.element.lyrics[0].text;
                        var replaceSyllable = replaceSyllables[i] || "";
                        if (text !== searchSyllables[i]) {
                            console.log("Wrong syllable at tick " + tempCursor.tick + ": expected '" + searchSyllables[i] + "', found '" + text + "'");
                            // Check if there is a match lowecased
                            if (text.toLowerCase() == searchSyllables[i].toLowerCase()) {
                                console.log("lowercase match at tick " + tempCursor.tick);
                                // Convert replace syllable to match case of original
                                // i.e. just check the first letter
                                if (text.length > 0 && replaceSyllable.length > 0) {
                                    if (text[0] === text[0].toUpperCase()) {
                                        replaceSyllable = replaceSyllable[0].toUpperCase() + replaceSyllable.slice(1);
                                    } else {
                                        replaceSyllable = replaceSyllable[0].toLowerCase() + replaceSyllable.slice(1);
                                    }
                                }
                            } else {
                                console.log("No match at tick " + tempCursor.tick);
                                break;
                            }
                        }

                        newReplaceSyllables[i] = replaceSyllable;
                        buffer.push(tempCursor.element.lyrics[0]);
                        tickBuffer.push(tempCursor.tick);
                        chordBuffer.push(tempCursor.element);
                        i++;
                    } else {
                        break;
                    }
                    tempCursor.next();
                }

                if (i === searchSyllables.length) {
                    console.log("Match found at tick " + startTick);
                    for (var j = 0; j < buffer.length; j++) {
                        buffer[j].text = newReplaceSyllables[j] || "";
                    }
                    cursor.rewind(0);
                    while (cursor.tick < tickBuffer[tickBuffer.length - 1])
                        cursor.next();
                } else {
                    cursor.next();
                }
            }
        }

        curScore.endCmd();
        console.log("Search and replace done.");
    }

    onRun: {
        console.log("Plugin started");
        pluginUI.visible = true;
    }

    Rectangle {
        id: pluginUI
        width: 400
        height: 180
        color: "lightgray"
        visible: false

        Column {
            anchors.centerIn: parent
            spacing: 10

            TextField {
                id: inputSearch
                width: 300
                placeholderText: "Search (e.g., syl-lab-le)"
                onTextChanged: searchPattern = text
            }

            TextField {
                id: inputReplace
                width: 300
                placeholderText: "Replace (e.g., rep-la-ce)"
                onTextChanged: replacePattern = text
            }

            Button {
                text: "Replace"
                onClicked: searchAndReplaceLyrics()
            }
        }

        MessageDialog {
            id: ctrlMessageDialog
            icon: StandardIcon.Warning
            title: "Lyrics Search Error"
            text: "Invalid input."
            visible: false
            onAccepted: visible = false
        }
    }
}
