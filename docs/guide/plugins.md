# MuseScore plugins

Part of this repository's agent guide; the index is [CLAUDE.md](../../CLAUDE.md). Moved out of it word for word (#513).

## MuseScore plugins (`plugins/`)

QML for MuseScore 3.x. **Install by copying/symlinking into
`~/Documents/MuseScore3/Plugins`** (MuseScore loads them from there, not from
this repo). After changing a plugin, reload it in MuseScore (Plugins → Plugin
Manager, or restart). They cannot be unit-tested from Python.

- `export.qml` — export per-voice mp3s, each choir voice mixed louder than the
  rest; supports SSAA/SATB/TTBB/SAM naming. Run by hand: the macOS recorder that
  used to trigger it is gone (#400), and the plugin was kept.
- `voice2.qml` — split a selection into two voices (lowest note → voice 2).
- `copylyrics.qml` — copy topmost-staff lyrics down to lower staves by tick.
- `replacelyrics.qml` — search/replace across hyphenated lyric syllables.
- `add_rest_track.qml` — add a spacer staff of 16th rests (even measure spacing).
- `mute.qml` — toggle mute / set volume on all instruments.
- `lyric_export_import.qml` — in-app lyric TSV transfer with highlighting.
