//==========================================================================================
//  SearchAndReplaceLyrics Plugin for MuseScore 3.x
//  Finds lyrics in the score matching a hyphenated sequence and replaces with another.
//==========================================================================================
import QtQuick 2.1
import QtQuick.Controls 1.0
import QtQuick.Dialogs 1.1
import MuseScore 3.0

MuseScore {
    version: "1.2"
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

    // MuseScore stores hyphenated syllables with leading/trailing hyphens (e.g. "-ti").
    // Normalize for comparison and trim whitespace.
    function normalizeSyllableText(s) {
        if (typeof s !== "string") return "";
        return s.replace(/^-|-$/g, "").trim();
    }

    function searchAndReplaceLyrics() {
        console.log("=== Search and Replace Lyrics: start ===");

        if (searchPattern.trim() === "" || replacePattern.trim() === "") {
            console.log("Abort: search or replace field empty.");
            showError("Search and Replace fields must not be empty.");
            return;
        }

        var searchSyllables = searchPattern.split("-");
        var replaceSyllables = replacePattern.split("-");
        console.log("Input  search: '" + searchPattern + "' -> syllables: [" + searchSyllables.join(", ") + "]");
        console.log("Input replace: '" + replacePattern + "' -> syllables: [" + replaceSyllables.join(", ") + "]");
        console.log("Score: nstaves=" + curScore.nstaves);

        curScore.startCmd();
        var totalReplacements = 0;

        for (var staff = 0; staff < curScore.nstaves; staff++) {
            var cursor = curScore.newCursor();
            cursor.rewind(0);
            cursor.staffIdx = staff;

            console.log("--- Staff " + staff + " (of " + curScore.nstaves + ") ---");
            if (!cursor.segment) {
                curScore.endCmd();
                console.log("Abort: no segment on staff " + staff);
                showError("No score content found.");
                return;
            }
            var staffReplacements = 0;
            while (cursor.segment) {
                var startTick = cursor.tick;
                var tempCursor0 = curScore.newCursor();
                tempCursor0.rewind(0);
                tempCursor0.voice = cursor.voice;
                tempCursor0.staffIdx = cursor.staffIdx;
                while (tempCursor0.segment && tempCursor0.tick < startTick)
                    tempCursor0.next();
                var chordAtStart = (tempCursor0.element && tempCursor0.element.type === Element.CHORD) ? tempCursor0.element : null;
                var maxVerses = (chordAtStart && chordAtStart.lyrics) ? chordAtStart.lyrics.length : 0;

                var matched = false;
                var matchBuffer = [];
                var matchTickBuffer = [];
                var matchNewReplaceSyllables = [];

                for (var verse = 0; verse < maxVerses && !matched; verse++) {
                    var tempCursor = curScore.newCursor();
                    tempCursor.rewind(0);
                    tempCursor.voice = cursor.voice;
                    tempCursor.staffIdx = cursor.staffIdx;
                    while (tempCursor.segment && tempCursor.tick < startTick)
                        tempCursor.next();

                    console.log("Try from tick " + startTick + " (staff " + staff + ", voice " + cursor.voice + ", verse " + verse + ")");

                    var buffer = [];
                    var tickBuffer = [];
                    var newReplaceSyllables = replaceSyllables.slice(0);
                    var i = 0;
                    while (tempCursor.segment && i < searchSyllables.length) {
                        if (tempCursor.element && tempCursor.element.type === Element.CHORD && tempCursor.element.lyrics.length > verse) {
                            var lyric = tempCursor.element.lyrics[verse];
                            var rawText = lyric.text;
                            var text = normalizeSyllableText(rawText);
                            var searchSyl = normalizeSyllableText(searchSyllables[i]);
                            var replaceSyl = normalizeSyllableText(replaceSyllables[i] || "");
                            var replaceSyllable = replaceSyllables[i] || "";
                            // Match if score syllable equals search OR replace (so we find both "mi-ti" and already "mi-tä")
                            var matchSearch = (text === searchSyl) || (text.toLowerCase() === searchSyl.toLowerCase());
                            var matchReplace = (text === replaceSyl) || (text.toLowerCase() === replaceSyl.toLowerCase());
                            var match = matchSearch || matchReplace;
                            console.log("  tick " + tempCursor.tick + " syl[" + i + "] verse=" + verse + ": raw='" + rawText + "' norm='" + text + "' search='" + searchSyl + "' replaceSyl='" + replaceSyl + "' match=" + match + " (search=" + matchSearch + " replace=" + matchReplace + ")");
                            if (!match) {
                                console.log("  -> no match, break (need " + searchSyllables.length + " syllables, got " + i + ")");
                                break;
                            }
                            // Apply case from score to replacement when possible
                            if (text.length > 0 && replaceSyllable.length > 0) {
                                if (text[0] === text[0].toUpperCase()) {
                                    replaceSyllable = replaceSyllable[0].toUpperCase() + replaceSyllable.slice(1);
                                } else {
                                    replaceSyllable = replaceSyllable[0].toLowerCase() + replaceSyllable.slice(1);
                                }
                            }

                            newReplaceSyllables[i] = replaceSyllable;
                            buffer.push(lyric);
                            tickBuffer.push(tempCursor.tick);
                            i++;
                        } else {
                            var reason;
                            if (!tempCursor.element) reason = "no element";
                            else if (tempCursor.element.type !== Element.CHORD) reason = "not chord (type=" + tempCursor.element.type + ")";
                            else reason = "no lyrics verse " + verse + " (length=" + (tempCursor.element.lyrics ? tempCursor.element.lyrics.length : 0) + ")";
                            console.log("  tick " + tempCursor.tick + ": skip - " + reason + ", break");
                            break;
                        }
                        tempCursor.next();
                    }

                    if (i === searchSyllables.length) {
                        console.log("MATCH at tick " + startTick + " verse " + verse + ": replacing " + buffer.length + " syllables with [" + newReplaceSyllables.join(", ") + "]");
                        matchBuffer = buffer;
                        matchTickBuffer = tickBuffer;
                        matchNewReplaceSyllables = newReplaceSyllables;
                        matched = true;
                    }
                }

                if (matched) {
                    for (var j = 0; j < matchBuffer.length; j++) {
                        matchBuffer[j].text = matchNewReplaceSyllables[j] || "";
                    }
                    staffReplacements++;
                    totalReplacements++;
                    cursor.rewind(0);
                    while (cursor.tick < matchTickBuffer[matchTickBuffer.length - 1])
                        cursor.next();
                    cursor.next();
                } else {
                    cursor.next();
                }
            }
            console.log("Staff " + staff + ": " + staffReplacements + " replacement(s)");
        }

        curScore.endCmd();
        console.log("=== Done. Total replacements: " + totalReplacements + " ===");
    }

    onRun: {
        console.log("Search and Replace Lyrics plugin v1.2 started");
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
