
import QtQuick 2.2
import QtQuick.Controls 1.1
import QtQuick.Dialogs 1.2
import QtQuick.Layouts 1.1
import QtQuick.Window 2.1
import Qt.labs.folderlistmodel 2.1
import Qt.labs.settings 1.0

import MuseScore 1.0
import FileIO 1.0


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
            part.volume = vol
            part.mute = false  
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
        expName =  getExportDir() + curScore.name + " ALL.mp3"
        console.log ( "createfile: " + expName);
        console.log(writeScore(curScore, expName,"mp3"))
        
        // get number of all parts without piano
        // for every choir voice (eq. part) set all others to volume 50
        var maxPart = getMaxChoirPart()
        for (var partIdx = 0; partIdx < maxPart; partIdx++)
        {
                // all others to 50
                mixerVolAll(30)
                // single choir voice to 100
                mixerVolPart(100,partIdx)		
                
                expName =  getExportDir() + curScore.name + " " + curScore.parts[partIdx].partName + ".mp3"
                console.log ( "createfile: " + expName);
                writeScore(curScore , expName, "mp3" )
        }
        
        // when finished set all back to normal
        mixerVolAll(100)
        Qt.quit()
    } // on run

    function getExportDir() {
        if (Qt.platform.os=="windows") {
            if (exportDirectory.text.slice(-1) !== '\\') {
                return exportDirectory.text + "\\";
            }
        }
        if (exportDirectory.text.slice(-1) !== '/') {
            return exportDirectory.text + "/";
        }
    }

    function namePart(p, name) {
        console.log("namePart", p, name);
        if (curScore.parts.length > p) {
            curScore.startCmd();
            curScore.parts[p].longName = name;
            curScore.parts[p].partName = name;
            if (name.indexOf(".") === -1){
                curScore.parts[p].shortName = name.slice(0,1);
            } else {
                curScore.parts[p].shortName = name.slice(0,3);
            }
            curScore.endCmd();
        }
    }

    function nameAllParts() {
        var abbr = partNames.text + "";
        var p = 0;
        var used = [1,1,1,1,1,1];
        var addstr = "";
        var len = abbr.length;
        var c = "";
        for (var i = 0; i < len; i++){
            c = abbr.charAt(i);
            console.log(c)
            switch (c) {
                case "S":
                    nameAllPartsHelper(used[0], p, "Soprano");
                    p++;
                    used[0]++;
                break;
                case "A":
                    nameAllPartsHelper(used[1], p, "Alto");
                    p++;
                    used[1]++;
                break;
                case "T":
                    nameAllPartsHelper(used[2], p, "Tenor");
                    p++;
                    used[2]++;
                break;
                case "B":
                    nameAllPartsHelper(used[3], p, "Bass");
                    p++;
                    used[3]++;
                break;
                case "W":
                    nameAllPartsHelper(used[4], p, "Women");
                    p++;
                    used[4]++;
                break;
                case "M":
                    nameAllPartsHelper(used[5], p, "Men");
                    p++;
                    used[5]++;
                break;
            }
        }
    }
    function nameAllPartsHelper(used, p, str) {
        var addstr = (used > 1 ? used + "." : "");
        if (used === 2 && p > 0 && curScore.parts[p - 1].partName === str) {
            namePart(p - 1, "1." + str);
        }
        namePart(p, addstr + str);
    }

    Settings {
        id: settings
        property alias exportDirectory: exportDirectory.text
    }

    Component.onDestruction: {
        settings.exportDirectory = exportDirectory.text
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
                    Label {
                        text: "Directory for exporting: "
                    }
                    TextField {
                        id: exportDirectory
                        text: ""
                    }
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
                        text: "SATB"
                    }
                    Button {
                        id: namePartsButton
                        text: qsTr("Name parts (SATBWM)")
                        onClicked: {
                            nameAllParts();
                        } // onClicked
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