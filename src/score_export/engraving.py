"""What every verovio toolkit in the app is set up with."""

from importlib.resources import files

# Verovio's element ids are otherwise random per run; pinning them makes an
# engraving byte-for-byte reproducible.
XML_ID_SEED = 1

# The native library does not carry the package's default resource path into the
# executor thread used by the song app. Give each toolkit its bundled fonts
# explicitly wherever engraving runs.
RESOURCE_PATH = str(files("verovio") / "data")
