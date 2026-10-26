# Agent guide

- Asked to build a practice track from a PDF (or to put a song on the stemmanauhat site)?
  Follow [docs/pdf-to-practice-track.md](docs/pdf-to-practice-track.md) step by step.
- Everything else: read CLAUDE.md, the full guide to this repository. It is long; list its
  headings with `grep -n '^#' CLAUDE.md` and read the section for your task.
- When a slug or part name becomes a path, check it in one shared helper before any write or
  `rmtree` (`check_slug` in `src/sing_song/main.py` is one), and test it with `..`, `/` and an
  absolute path.
- A remote job that fails (`src/kube_runner/runner.py`) cools that kind off for five minutes, and
  meanwhile every run reads or sings on this host with only a log line saying so. After fixing
  the runner, delete `read-band-cooldown.json` or `sing-line-cooldown.json` under its state dir
  (`HOMR_EERO_PC_STATE`, default `~/.cache/homr-the GPU machine`). `REMOTE_KUBE_COOLDOWN_S=0`
  only stops a new cooldown; it does not end one already written.
- Run the tests of what you changed, e.g. `.venv/bin/python -m pytest src/sing_song/tests -q`;
  CI runs the rest.
