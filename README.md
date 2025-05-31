# musescore-choir-plugins

Plugins to help creating practise tracks

export.qml is used to create mp3:s for each staff. You can also rename the saves quickly for any combination SSAA, SATB, TTBB, SAM (Soprano, alto, men) etc.

voice2.qml splits a selection into two voices (chords are split, lowest note goes to voice 2, single notes are duplicated)

replacelyrics.qml Is a search an replace for lyrics

copylyrics.qml copies topmost lyrics to bottom staves

add_rest_track adds a new staff that contains 16th rests. This makes all measures about evenly spaced


# How to make a stemmanauha

1. Write the score or use some program (I currently use soundslice)

- Use plugins to make it easier

2. Export mp3

3. Take a video and combine with ffmpeg