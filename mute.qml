
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
    description: "Mutes or unmutes all intruments in the score."
    menuPath: "Plugins.Toggle Mute All Instruments"

  // Set all parts to volume specified by vol
  // disable mute if enabled.
    function toggleMute()
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
                    channel.mute = !channel.mute;
                }
            }
        }
    }

    onRun: {
        console.log("Plugin started");
        toggleMute();
        Qt.quit();
    }

    Rectangle {
        width: 400
        height: 100
        color: "grey"

        MessageDialog {
            id: ctrlMessageDialog
            icon: StandardIcon.Warning
            title: "Error"
            text: "Error muting."
            visible: false
            onAccepted: visible = false
        }
    }

}
