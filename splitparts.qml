import QtQuick 2.2
import QtQuick.Controls 1.1
import QtQuick.Dialogs 1.2
import QtQuick.Layouts 1.1
import MuseScore 1.0

MuseScore {
    menuPath: "Plugins.SplitPart"
    onRun: {
        splitStaff();
        Qt.quit();
        //exportDialog.visible = true
    }
    Timer {
        id: myTimer; interval: 100; running: false; repeat: false
        onTriggered: pasteStaff();
    }
    Timer {
        id: myTimer2; interval: 300; running: false; repeat: false
        onTriggered: removeAllButHighestNotes();
    }
    Timer {
        id: myTimer3; interval: 500; running: false; repeat: false
        onTriggered: pasteStaff();
    }
    Timer {
        id: myTimer4; interval: 700; running: false; repeat: false
        onTriggered: removeHighestNotes();
    }
    Timer {
        id: myTimer5; interval: 900; running: false; repeat: false
        onTriggered: afterStuff();
    }
    function splitStaff() {
        copyStaff();                  // copy               0
        this.myTimer.running = true;  // paste              100
        this.myTimer2.running = true; // remove lowest      300
        this.myTimer3.running = true; // paste again        500
        this.myTimer4.running = true; // remove Highest     700
        this.myTimer5.running = true; // afterStuff         900
    }
    function copyStaff() {
        cmd("select-begin-score");
        cmd("select-end-score");
        cmd("copy");
        console.log("copy");
    }
    function pasteStaff() {
        curScore.startCmd();
        curScore.appendPart("empty");
        curScore.endCmd();
        for (var i = 0; i<curScore.ntracks; i++) {
            cmd("next-track");
        }
        cmd("select-next-measure");
        cmd("select-begin-score");
        cmd("paste");
        console.log("paste");
    }
    function afterStuff() {
        cmd("voice-x12");
        cmd("next-track");
        cmd("select-similar");
    }


      function removeHighestNotes() {
            var cursor = curScore.newCursor();
            cursor.rewind(1);
            cursor.rewind(0);
            var el;
            var selStaff = cursor.staffIdx;
            while (true ) {
                console.log("slpit");
                var highest = null;
                var pitchStr = "";
                var chord = null;
                var rest = null;
                var notecount = 0;
                var voiceToDelete = null;
                for (var voice = 0; voice< 4; voice++) {
                  cursor.voice = voice;
                  el = cursor.element;
                  if (el !== null) {
                  console.log("el: ",el);
                  //console.log(cursor.element, cursor.staffIdx, cursor.voice);
                    if (el.type === Element.CHORD) {
                        for (var i = 0; i< el.notes.length; i++) {
                            var p = el.notes[i].pitch;
                            if (highest === null  || p > highest.pitch) {
                                highest = el.notes[i];
                                chord = el;
                                voiceToDelete = voice;
                            }
                            notecount++;
                        } // end for
                    }
                  } // end if
               } // end for
               if (chord !== null && notecount > 1) {
                    console.log("removing ", highest);
                    if (chord.notes.length > 1) {
                        chord.remove(highest);
                    } else {
                        console.log("have to do rest", chord.notes.length, voiceToDelete)
                        var newRest = newElement(Element.REST);
                        newRest.duration = chord.duration
                        cursor.voice = voiceToDelete;
                        cursor.add(newRest);
                    }
                }
               cursor.voice = 0;
               if (!cursor.next()) break;
            } // end while
            } // end function

    function removeAllButHighestNotes() {
            var cursor = curScore.newCursor();
            cursor.rewind(1);
            cursor.rewind(0);
            var el;
            var selStaff = cursor.staffIdx;
            while (true ) {
                console.log("slpit");
                var highest = null;
                var pitchStr = "";
                var chord = null;
                var rest = null;
                var notecount = 0;
                var voiceToDelete = null;
                for (var voice = 0; voice< 4; voice++) {
                  cursor.voice = voice;
                  el = cursor.element;
                  if (el !== null) {
                  console.log("el: ",el);
                  //console.log(cursor.element, cursor.staffIdx, cursor.voice);
                    if (el.type === Element.CHORD) {
                        for (var i = 0; i< el.notes.length; i++) {
                            var p = el.notes[i].pitch;
                            if (highest === null  || p > highest.pitch) {
                                highest = el.notes[i];
                                chord = el;
                                voiceToDelete = voice;
                            }
                            notecount++;
                        } // end for
                    }
                  } // end if
                } // end for
                cursor.voice = 0;
                console.log("notecount: ", notecount);
                if (notecount > 1) {
                    for (var voice = 0; voice< 4; voice++) {
                        cursor.voice = voice;
                        el = cursor.element;
                        if (el !== null && el.type === Element.CHORD) {
                            var chordLen = el.notes.length;
                            var notesToRemove = [];
                            for (var i = 0; i< el.notes.length; i++) {
                                console.log("el.note[i]: ",el.notes[i])
                                if (el.notes[i] !== highest) {
                                    if (chordLen > 1) {
                                        console.log("removing",el.notes[i])
                                        notesToRemove.push(el.notes[i]);
                                        chordLen--;
                                        //el.remove(el.notes[i]);
                                        console.log("Removed");
                                    } else {
                                        console.log("have to do rest",el.notes[i])
                                        var newRest = newElement(Element.REST);
                                        newRest.duration = el.duration
                                        cursor.add(newRest);
                                        notesToRemove = [];
                                        break;
                                    }
                                } else {
                                    console.log("not highest ",el.notes[i])
                                }
                            }
                            for (var i = 0; i<notesToRemove.length; i++) {
                                el.remove(notesToRemove[i]);
                            }
                        }
                    }
                    cursor.voice = 0;
                }
               if (!cursor.next()) break;
            } // end while
            } // end function




    /*Dialog {
        id: exportDialog
        visible: true
        title: qsTr("Trimmer")
        width: formbackground.width
        height: formbackground.height
        contentItem: Rectangle {
            id: formbackground
            width: exporterColumn.width + 20
            height: exporterColumn.height + 20
            color: "lightgrey"
            ColumnLayout {
                id: exporterColumn
                GridLayout {
                    id: grid
                    columns: 2
                    anchors.fill: parent
                    anchors.margins: 10
                    Button {
                        id: button_highest
                        text: qsTr("Remove highest notes")
                        onClicked: {
                            removeHighestNotes();
                            exportDialog.visible = false;
                            Qt.quit()
                        }
                    }
                    Button {
                        id: button_allButHighest
                        text: qsTr("Remove all but highest notes")
                        onClicked: {
                            removeAllButHighestNotes();
                            exportDialog.visible = false;
                            Qt.quit()
                        }
                    }
                    Button {
                        id: button_duplicate
                        text: qsTr("Duplicate staff")
                        onClicked: {
                            splitStaff();
                            exportDialog.visible = false;
                            Qt.quit()
                        }
                    }
                }
            }
        }
    }*/

      } // end MuseScore
