"""Shared pytest setup for every test package."""

import os

# Tests build their songs in a tmp dir, and a song's media follows MEDIA_ROOT
# (src/media_root.py). The host's .env sets it to the real media disk, and the app
# loads .env without overriding a variable already set — so an empty one here keeps
# every test's videos beside its own tmp songs, servers started by the browser
# tests included.
os.environ["MEDIA_ROOT"] = ""
