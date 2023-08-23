
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
    version:  "1.2"
    description: "Exports score as mp3 as well as every Choir voice twice as loud as other voices"
    menuPath: "Plugins.Choir Rehearsal Export"

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

    onRun:
    {
        console.log("Start");
exportDialog.visible = true;
  console.log("End");
    }

    function exportParts() {
        var expName;

        if (typeof curScore == 'undefined') { Qt.quit()}
        console.log("parts: " + curScore.parts.length);

        // set Volume of all parts to 100
        mixerVolAll(100)

        console.log(curScore);

        // export score as mp3 with all voices aat normal
        expName =  curScore.scoreName + " ALL.mp3"
        console.log ( "createfile: " + expName);
        writeScore(curScore, expName,"mp3")
        
        // get number of all parts without piano
        // for every choir voice (eq. part) set all others to volume 50
        var maxPart = getMaxChoirPart()


        for (var partIdx = 0; partIdx < maxPart; partIdx++)
        {
            var partName = partNames.text.split(" ")[partIdx]
                // all others to 50
                mixerVolAll(30)
                // single choir voice to 100
                mixerVolPart(100,partIdx)		
                
                expName =  curScore.scoreName + " " + partName + ".mp3"
                console.log ( "createfile: " + expName);
                writeScore(curScore , expName, "mp3" )
        }
        
        // when finished set all back to normal
        mixerVolAll(100)
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
                    }
                    Button {
                        id: cancelButton
                        text: qsTr("Close")
                        onClicked: {
                            exportDialog.visible = false
                            Qt.quit()
                        } // onClicked
                    }
                }
            }
        }
    }

}
