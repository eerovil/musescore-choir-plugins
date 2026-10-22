# Agent guide

- Asked to build a practice track from a PDF (or to put a song on the stemmanauhat site)?
  Follow [docs/pdf-to-practice-track.md](docs/pdf-to-practice-track.md) step by step.
- Everything else: read CLAUDE.md, the full guide to this repository. It is long; list its
  headings with `grep -n '^#' CLAUDE.md` and read the section for your task.
- Run the tests of what you changed, e.g. `.venv/bin/python -m pytest src/sing_song/tests -q`;
  CI runs the rest.
