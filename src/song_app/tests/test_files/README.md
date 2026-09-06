# Test files for `src/song_app`

`shared_whole_rest.musicxml` is a **committed copy** of one real homr parse:
printed system 3 of the song `test` on the development host (Herää Suomi!, the
public-domain nine-band slice), read at 200 dpi. Its second measure is the bar
issue #130 diagnosed and #164 repairs — a printed whole-bar rest written into
the same `<voice>` as the sung notes beside it, so a 4/4 bar comes out seven
quarters long.

It is committed rather than read out of `songs/`, which is live state shared
with the running app: the bar changed under #130 in the middle of that card,
and a test that reads it there is a test of whatever the host happens to hold.

`voice_rank_join_first.musicxml` and `voice_rank_join_second.musicxml` are the
same kind of thing for issue #187: **committed copies** of two real homr
parses, printed systems 16 and 17 of Kaksi laulua krapulasta 2, read band by
band at 200 dpi. They are the two systems either side of one join, and the join
issue #173 measured losing 21 points with every note present, at the right
pitch, on the right beat, in the wrong part.

What makes them the fixture is what homr wrote rather than what it read. On the
upper staff of both, homr numbers the higher voice 1 and the lower one 2 all the
way through — but it *writes* the lower one first in all three bars of system
16 and in the first bar of system 17, and the higher one first in the rest. A
renumbering that followed the written order therefore swapped the two singers
mid-phrase and again across the join, which is the defect these files pin.

They are committed for the same reason `shared_whole_rest.musicxml` is: the
parses live on the development host under
`~/.local/share/musescore-choir-plugins/issue-173/`, which is neither in the
repository nor guaranteed to survive, and the song itself is in `songs/`, which
is live and has changed under two cards already.
