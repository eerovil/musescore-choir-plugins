import QtQuick 2.1
import QtQuick.Controls 1.0
import MuseScore 3.0

MuseScore {
    version: "1.1"
    description: "Export and import lyrics (TSV) with highlighting"
    menuPath: "Plugins.Lyrics TSV Transfer"
    pluginType: "dialog"
    width: 500
    height: 400

    property string mode: "export"  // or "import"

    onRun: {
        pluginUI.visible = true;
    }

    function exportLyrics() {
        var result = "";
        for (var staff = 0; staff < curScore.nstaves; staff++) {
            for (var voice = 0; voice < 4; voice++) {
                var cursor = curScore.newCursor();
                cursor.staffIdx = staff;
                cursor.voice = voice;
                cursor.rewind(0);

                while (cursor.segment) {
                    if (cursor.element && cursor.element.type === Element.CHORD) {
                        var chord = cursor.element;
                        for (var v = 0; v < chord.lyrics.length; v++) {
                            var lyric = chord.lyrics[v];
                            if (lyric) {
                                result += cursor.tick + "\t" +
                                        cursor.staffIdx + "\t" +
                                        cursor.voice + "\t" +
                                        (v + 1) + "\t" +  // 1-based verse
                                        (lyric.syllabic || "single") + "\t" +
                                        lyric.text + "\n";
                            }
                        }
                    }
                    cursor.next();
                }
            }
        }

        tsvBox.text = result;
        console.log("Lyrics exported to TSV");
    }

function parseSyllabic(s) {
    if (s === "single") return 0;
    if (s === "begin") return 1;
    if (s === "middle") return 2;
    if (s === "end") return 3;
    return s; // fallback
}

    function importLyrics() {
    var lines = tsvBox.text.trim().split("\n");
    var updates = 0;

    for (var i = 0; i < lines.length; i++) {
        var line = lines[i].trim();
        if (line === "") continue;

        var parts = line.split("\t");
        if (parts.length < 6) continue;

        var tick     = parseInt(parts[0]);
        var staff    = parseInt(parts[1]);
        var voice    = parseInt(parts[2]);
        var verse    = parseInt(parts[3]) - 1;
        var syllabic = parts[4];
        var text     = parts.slice(5).join("\t");

        var cursor = curScore.newCursor();
        cursor.rewind(0);
        cursor.staffIdx = staff;
        cursor.voice = voice;

        while (cursor.segment && cursor.tick < tick)
            cursor.next();

        if (!cursor.segment || cursor.tick !== tick)
            continue;

        if (!cursor.element || cursor.element.type !== Element.CHORD)
            continue;

        var chord = cursor.element;
        if (verse >= chord.lyrics.length || !chord.lyrics[verse])
            continue;

        var lyric = chord.lyrics[verse];
        var syllabicValue = parseSyllabic(syllabic);

        if (lyric.text != text || lyric.syllabic != syllabicValue) {
            console.log("Updating lyric at tick " + tick + ", staff " + staff + ", voice " + voice + ", verse " + (verse + 1));
            console.log("Old text: " + lyric.text + ", syllabic: " + lyric.syllabic);
            console.log("New text: " + text + ", syllabic: " + syllabicValue);
            lyric.text = text;
            lyric.syllabic = syllabicValue;
            lyric.color = "#FF0000";
            updates++;
        }
    }

    console.log("Lyrics import complete. Updated " + updates + " lyrics.");
}

    Rectangle {
        id: pluginUI
        width: parent.width
        height: parent.height
        color: "lightgrey"
        visible: false

        Column {
            anchors.fill: parent
            anchors.margins: 10
            spacing: 10

            ComboBox {
                id: modeSelector
                width: 150
                model: ["export", "import"]
                onCurrentTextChanged: mode = currentText
            }

            Button {
                text: "Run"
                width: 100
                onClicked: {
                    curScore.startCmd();
                    if (mode === "export")
                        exportLyrics();
                    else
                        importLyrics();
                    curScore.endCmd();
                }
            }

            TextArea {
                id: tsvBox
                width: parent.width - 20
                height: parent.height - 120
                wrapMode: TextArea.NoWrap
                font.family: "Courier New"
                font.pointSize: 10
            }
        }
    }
}
