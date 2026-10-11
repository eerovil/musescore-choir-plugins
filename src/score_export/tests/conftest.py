import os
import shutil

import pytest
from dotenv import load_dotenv

# Same order as the CLI wrappers and the web app: real .env first, then defaults.
load_dotenv(".env") or load_dotenv(".env.default")

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = os.path.join(HERE, "test_files")


@pytest.fixture
def fermata_mscx():
    """A 2-staff score whose last chord of measure 1 carries a 3x fermata."""
    return os.path.join(FILES, "fermata.mscx")


def musescore_available() -> bool:
    cli = (os.getenv("MUSESCORE_CLI_PATH") or "musescore3")
    return bool(shutil.which(cli) or (os.path.isfile(cli) and os.access(cli, os.X_OK)))


needs_musescore = pytest.mark.skipif(
    not musescore_available(), reason="MuseScore CLI not available (MUSESCORE_CLI_PATH)")
