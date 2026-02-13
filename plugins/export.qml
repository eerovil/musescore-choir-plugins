
import QtQuick 2.2
import QtQuick.Controls 1.1
import QtQuick.Dialogs 1.2
import QtQuick.Layouts 1.1
import QtQuick.Window 2.1
import Qt.labs.folderlistmodel 2.1
import Qt.labs.settings 1.0

import MuseScore 3.0
import FileIO 3.0


MuseScore {
    QtObject {
        id: scriptGlobals
        property bool cancelRequested: false
    }
    version:  "1.2"
    description: "Exports score as mp3 as well as every Choir voice twice as loud as other voices"
    menuPath: "Plugins.Choir Rehearsal Export"

FileIO {
    id: fileWriter
}

  // Set all parts to volume specified by vol
  // disable mute if enabled.
    function mixerVolAll(vol)
    {
     var part
        for (var partIdx = 0; partIdx < curScore.parts.length; partIdx++)
        {
            part = curScore.parts[partIdx];
            //console.log ( "part partName/shortName: " + part.partName + "/" + part.shortName);

            var instrs = part.instruments;
            for (var j = 0; j < instrs.length; ++j) {
                var instr = instrs[j];
                var channels = instr.channels;
                for (var k = 0; k < channels.length; ++k) {
                    var channel = channels[k];
                    channel.volume = vol;
                    channel.mute = false;
                }
            }
        }
    }

  // not everything needs to be exported.
    // use export only for Choir voiced but not for Piano
    // it is assumed the piano is always the last part
    function getMaxChoirPart()
    {
        return curScore.parts.length; 
    }

    // set the volume of a certain part to "vol"
    function mixerVolPart(vol, partIdx)
    {
        var part
        part = curScore.parts[partIdx];
        // console.log ( "part partName/shortName: " + part.partName + "/" + part.shortName);
        part.volume = vol
        part.mute = false

        var instrs = part.instruments;
        for (var j = 0; j < instrs.length; ++j) {
            var instr = instrs[j];
            var channels = instr.channels;
            for (var k = 0; k < channels.length; ++k) {
                var channel = channels[k];
                channel.volume = vol;
            }
        }
    }
    
    // Get a Name/Volume pattern to be used in the export filename
    // e.g. S.50_A.100_T.50_B.50
    function namesVol( maxPart )
    {
         var part
         var retName
         retName = ""
         for (var partIdx = 0; partIdx < maxPart ; partIdx++)
         {
               part = curScore.parts[partIdx];
               retName += "_" + part.shortName + part.volume
         }
         
         return retName
    }

    function readPartNames()
    {
        var currentPartNames = []
        for (var partIdx = 0; partIdx < curScore.parts.length; partIdx++)
        {
            var part = curScore.parts[partIdx];
            if (part.partName === "Drumset") {
                continue;
            }
            if (part.partName === "Click") {
                continue;
            }
            currentPartNames.push(part.partName)
        }
        return currentPartNames
    }

    onRun: {
        console.log("Start");
        var defaultPartNames = readPartNames()
        console.log("partNames: " + defaultPartNames);
        exportDialog.visible = true;
        partNames.text = defaultPartNames.join(" ");
        focusTimer.start();

        console.log("End");
    }

    function exportParts() {
        exportButton.enabled = false;

        var expName;

        if (typeof curScore == 'undefined') { Qt.quit()}
        console.log("parts: " + curScore.parts.length);

        // set Volume of all parts to 100
        mixerVolAll(100)

        console.log(curScore);

        
        // get number of all parts without piano
        // for every choir voice (eq. part) set all others to volume 50
        var maxPart = partNames.text.split(" ").length


        for (var partIdx = 0; partIdx < maxPart; partIdx++)
        {

            if (scriptGlobals.cancelRequested) {
                console.log("Export cancelled.");
                break;
            }


            var partName = partNames.text.split(" ")[partIdx]
                // all others to 50
                mixerVolAll(30)
                // single choir voice to 100
                mixerVolPart(100,partIdx)		
                
                expName =  curScore.scoreName + " " + partName + ".mp3"
                console.log ( "createfile: " + expName);
                writeScoreResp = writeScore(curScore , expName, "mp3" )
                console.log("writeScoreResp: " + writeScoreResp);
                
        }
        
        // when finished set all back to normal
        mixerVolAll(100)

        // export score as mp3 with all voices aat normal
        expName =  curScore.scoreName + " ALL.mp3"
        console.log ( "createfile: " + expName);
        var writeScoreResp = writeScore(curScore, expName,"mp3")
        console.log("writeScoreResp: " + writeScoreResp);

        exportDialog.visible = false;
        Qt.quit()
    } // on run

    Settings {
        id: settings
    }

    Dialog {
        id: exportDialog
        visible: false
        title: qsTr("Choir Rehearsal Export")
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
                        id: exportButton
                        text: qsTranslate("PrefsDialogBase", "Export")
                        onClicked: {
                            exportParts()
                            //Qt.quit()
                        } // onClicked
                    }
                    Label {
                        id: exportStatus
                        text: ""
                    }
                    TextField {
                        id: partNames
                        text: "T1 T2 B1 B2"
                        onAccepted: {
                            exportParts();
                        }
                    }
                    Button {
                        id: cancelButton
                        text: qsTr("Cancel")
                        onClicked: {
                            scriptGlobals.cancelRequested = true;
                            exportDialog.visible = false
                            Qt.quit()
                        } // onClicked
                    }
                }
            }
        }
    }
    Timer {
        id: focusTimer
        interval: 100
        running: false
        repeat: false
        onTriggered: {
            exportButton.forceActiveFocus()
        }
    }

}
