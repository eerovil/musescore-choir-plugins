"""Getting a cleaned score ready to publish, through the MuseScore 3 CLI.

`score.prepare` makes the copy that is published (silent parts dropped, red marks
stripped, tempo and fermata holds written in), `musescore` runs the CLI, and
`clock` reads MuseScore's own play order and tempo map.
"""
