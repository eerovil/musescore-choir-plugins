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
