# `omr.py`: running homr

Part of this repository's agent guide; the index is [CLAUDE.md](../../CLAUDE.md). Moved out of it word for word (#513). These are bullets of the guide's section on the song web app, which is split over [song-app.md](song-app.md), [song-app-panels.md](song-app-panels.md), [omr.md](omr.md), [omr-systems.md](omr-systems.md) and [scan.md](scan.md), in that order; "above" and "below" in the text may point into a sibling file. Here: where homr lives, engines, provenance, GPU builds, the remote runner, slur pairing, whole-measure rests.

- `omr.py` is added by this pull request: **one call that turns a page image into a
  MusicXML path**, `read_page(image, out_dir=..., log=...)`. It runs homr (optical
  music recognition, adopted in #86), which is the first thing the app depends on that
  cannot live in the app's environment — so where it lives is the decision this module
  carries, and the reasoning is worth keeping.
  homr **is not a pip requirement and must not become one.** It is ~660 MB of
  onnxruntime and opencv wheels, plus ~150 MB of model weights it stores *inside its
  own site-packages*, and `scripts/deploy-song-app.sh --unattended` reinstalls
  `pip-requirements.txt` on every merge — so putting it there would re-download the
  weights on a whim and let a resolution failure in an ML stack take the live app
  down. It also is not the `omr` **distrobox** container it was benchmarked in: that
  container is hand-built host state nothing in this repo describes, which is the cost
  CLAUDE.md's "Working in a worktree" section exists to complain about. And it is not
  a container image either — a build system to hold one venv is not worth the second
  thing to keep working.
  So: a `uv`-managed python 3.12 venv outside the checkout, built by
  **`scripts/install-homr.sh`** (idempotent; `HOMR_VENV` moves it), called as a
  subprocess. A fresh clone runs one script.
  **And the app can run that script itself** (#249, `homr_install.py`): the
  Scan panel's *homr* box (moved there from the Library page by #261, since that is
  where homr is used and where "not installed" is said) shows the installed commit against the fork's `main`
  (`git ls-remote`, cached ten minutes) and an **Install / Update homr** button,
  with the script's log fetched every 2s while it runs. It is still a press and
  never automatic — the deploy does not touch homr, so the day a parse changes is a
  day somebody chose. It runs under one heavy slot, is refused while any song job
  runs (it replaces files inside the venv a scan reads from), and scans and *Ask
  homr* answer 409 while it runs — each read holds a token file taken under the same `flock` as the install, so neither can start in the gap after the other's check, even across the old and new server during a restart; a pid lock file beside the songs keeps it to one
  at a time. The button always installs `main` (an explicit `HOMR_SOURCE` is a
  shell's business), and the script now fetches `uv` into `~/.local/bin` when a
  host has none, which was the one step that stopped a fresh install cold.
  **Where homr comes from is one variable in that script, `HOMR_SOURCE`.** It defaulted
  to the immutable `eerovil/homr` commit matching upstream `v0.7.0`; this pull request
  moves it to **`@main`**, the fork's tip, which today is upstream's own tip. An
  explicit `HOMR_SOURCE` still wins, and a commit hash is how an old parse is got back.
  #97 put multi-page joining, the staff-grouping fix and PDF input *inside the fork*,
  because the app only ever sees homr's output and by then the staves are gone; all
  three later landed upstream too, and `choir-0.7.0` is exactly the v0.7.0 tag. The
  fork **carrying nothing of its own** was true for about a month and is not true now:
  it is a couple of hundred commits ahead of `liebharc/homr` — general OMR fixes, choir
  fixtures, and the measurement harness — and it is not going back, since the harness
  alone is a large share of that and belongs there. Since #220 it is **level with
  upstream `main`** at each sync: everything upstream has is merged in (see "Where an OMR
  fix belongs" above, which also says which side of the line a new fix falls on).
  The app passes homr **`--no-title`**: it never uses the title homr reads, and since
  upstream's 9ec3a78 reading one means fetching OCR weights first.
  It also passes **`--mark-doubt`** (#245), to a homr whose source has it
  (`engine_supports`): homr reads each image a second time, at 80% size, and puts a
  red `⚠` text on every bar it is probably wrong about — a near-tie between two
  readings that both fill the bar, an unsure pitch, accidental or voice line, two
  voices giving one shared notehead different lengths, the second reading
  disagreeing, or a note starting off every sixteenth and triplet sixteenth
  (`homr/doubt.py` in the fork). The owner asked that **no wrong bar go unmarked**,
  false alarms second: measured on 38 systems read in the cluster pod, all 9 wrong
  bars with owner-checked references are marked (with 39 of 117 right ones — most of
  Legenda, where homr really is unsure of nearly every triplet), and 26 of 27 on four
  songs whose references are less certain. Confidence alone could not do it: some
  readings are wrong with the decoder sure of every note, and the second reading is
  what catches those. A read takes twice as long. The marks are the same as
  cleaning's (`problem_marks`), so they survive the clean on the first part the
  staff becomes, are listed by health and the Fix panel until deleted, and never
  reach what is published. A homr too old to mark says so in the scan log, because no marks
  then means "not checked".
  What following a branch costs is worth saying rather than skipping: an install is no
  longer reproducible from the checkout alone, so two hosts set up a month apart get
  different OMR and so does one host reinstalled. What buys it back is that **nothing
  installs homr automatically** — it is not in `pip-requirements.txt` and the deploy
  never touches its venv — so the day the parse changes is a day somebody ran the
  script, and running it is when the frozen benchmark should be run again. Nothing in
  `omr.py` or in the rest of the installer moves with it.
  **A branch of the fork is not installed at all.** That install is the one homr
  install there is; a branch is run from a **local working copy**, and this is the
  shape that pull request settled on after trying a venv per branch. The only
  question a homr branch exists to answer is whether it reads *this* repertoire
  better than what we already have, and answering it means editing, re-reading a
  page, editing again — a 660 MB reinstall between passes is not a loop anybody
  uses. So the checkout at `HOMR_CHECKOUT` (default `~/homr`) **and every git
  worktree beside it** are engines: the dependencies come from the installed venv
  and the code comes from the working copy, put in front of it on `PYTHONPATH`
  (`<venv>/bin/python -c "from homr.main import main; main()"` — the package has no
  `__main__`, and the venv's own `bin/homr` would import the installed copy whatever
  `PYTHONPATH` said). Switching a branch there changes what the next
  scan runs, with nothing to rebuild and nothing to keep in step. The label is the
  branch, **read live from git** rather than remembered, because that is the whole
  point; the price is that such an engine is whatever is checked out at the moment it
  runs, which is why the installed venv stays as the fixed thing to compare against.
  **Both labels name what they actually are**, which took a second pass to get right.
  A checkout is `<branch> — <directory>`: neither half identifies it alone, since a
  worktree keeps its directory name when its branch changes (a tree called `system-4`
  is on `main` on this host) and two trees can be on branches that read alike. The
  installed one is `installed: <revision> @ <commit>`, read out of pip's own
  `direct_url.json` — it said `main` before, which was a guess this venv could not
  support: it predates `homr-engine.txt`, so the label would have read `main` whatever
  commit was installed, and "the frozen one" is worth nothing if it cannot say what it
  is frozen at. The marker file and an explicit `HOMR_SOURCE` are the fallbacks behind
  it.
  One thing this costs and pays for: homr keeps its ~150 MB of weights **beside its
  own source**, so a working copy run this way would download its own set — per
  worktree, four times over here. `link_weights` symlinks the installed venv's
  `.onnx` files into a checkout the first time it is picked. Safe because the file
  names carry a content hash: a branch wanting different weights asks for a different
  name and downloads it. Only missing files are linked and a real file is never
  replaced.
  `omr.engines()` lists what this host can run — the installed one first, then the
  working copies — and `engine_for(key)` resolves a choice, **refusing an engine that
  is not there** rather than falling back to the default: a parse nobody can account
  for is worse than a refused request. `read_page(engine=...)` is the whole of the
  rest (an `Engine` carries its argv and the environment it needs), threaded through
  `omr_systems.read_system` and `scan.run`.
  The choice is **per scan run**, and comparing two engines is reading a system with one
  and then the other — the retry button that already exists — and looking at both against
  the page. It used not to be recorded anywhere; this pull request proposes that **every
  parse says which homr produced it** (#154, #157), and the reasoning is under `scan.py`
  below. `read_page` writes it: one comment line before the root element, carrying the
  engine key, the label, the **commit** and whether the working copy was **dirty**.
  `Engine` carries the last two — pip's `direct_url.json` for the installed venv, `git
  rev-parse` and `git status --porcelain` for a working copy, both read at the moment the
  engine is listed, the same way the branch already is. The commit is the record and the
  label is the hint: `main` in a working copy means a different commit next week. The line
  is inserted and removed **textually** (`strip_provenance`), so taking it off gives back
  the bytes homr wrote — which is what lets `scan.content_stamp` step over it, and is
  therefore what keeps this provenance rather than a fourth stamp.
  `GET /api/homr-engines` is app-wide, because which homr a host
  has is a property of the host; the Scan panel's **Read with** picker appears only
  when there is more than one, and the scan route resolves the key at the door so a
  missing engine is a 400 rather than a run that takes the lock and dies on band one.
  Two things the earlier notes got wrong and one that has since changed under us.
  homr declares `>=3.11,<3.16` and installs on 3.14 too, so the version is a choice and
  not a wall — 3.12 because every benchmark number in #93 and #95 came off 3.12. There
  was **no `[cpu]` extra** on 0.7.0, so upstream's `uvx --from 'homr[cpu]' homr` recipe
  silently ignored it and the plain install pulled CPU onnxruntime. On `main` that is
  reversed and the extra is **load-bearing**: onnxruntime has moved out of the base
  dependencies into `cpu`/`cuda`/`rocm`, so a plain install of `main` has no inference
  runtime at all and fails at the first parse rather than at install time. `HOMR_SOURCE`
  therefore names `homr[cpu] @ git+...`; `HOMR_ORT_WHEEL` swaps it for the GPU build below.
  What the module absorbs is the CLI's shape. homr takes one image, writes
  `<image>.musicxml` **beside it**, has no `--output`, and drops a `_teaser.png` next
  to the input, so the run happens on a copy in a scratch dir and only the answer is
  moved to where the caller asked. `--gpu` is never left at `auto`: auto asks whether
  the CUDA provider is *registered*, not whether it can run, and this host's GTX 970 is
  sm_52 against the stock onnxruntime's sm_60 floor, so auto would pick CUDA and die on
  the first segnet node without falling back (#93). The value is `no` (not `off`)
  unless the venv's `homr-engine.txt` says `gpu=force`, and `OMR_GPU` overrides both.
  **The 970 can read** (#418): `scripts/onnxruntime-sm52/build.sh` builds onnxruntime
  1.29.0 for compute 5.2 in an NVIDIA CUDA 12.8 container (`patch.py` beside it says what
  its source needed: a float fallback for one half-precision kernel, and the MoE and
  4-bit MatMulNBits ops taken out because their kernels only exist from 7.5), and
  `HOMR_ORT_WHEEL=<wheel> scripts/install-homr.sh` puts it in the venv with pinned CUDA
  12.8 / cuDNN 9.10.2 wheels, writes `gpu=force`, and keeps the wheel for later plain
  runs. Two traps, both silent CPU fallbacks: a library with an undefined symbol does
  not load (the build now refuses one), and a locally built wheel's `_ld_preload.py`
  finds `libcudart` only on the system path and otherwise sets `ORT_CUDA_UNAVAILABLE`
  (the installer replaces it). homr's GPU route uses its fp16 models; this card has no
  fast fp16 but it is still the quicker route (5 systems: fp16 8.6 s and 10 s of CPU time
  a system, fp32 10.6 s and 17 s). Measured on the Virta fixture's 15 systems
  (`--mark-doubt`, 200 dpi): GPU 8.6 s and 11 s of CPU time a system against the CPU's
  13.7 s and 38 s. The readings are not identical — cuDNN's arithmetic is not the CPU's,
  and borderline calls flip: 10 of 15 systems have the same notes, and across the other
  five the GPU loses 5 notes the CPU reads (shared unison heads) and gains 2 the CPU
  misses, of 856. Every differing bar carries a `⚠` mark in both readings.
  **The GTX 1660 Super reads with the stock build** (eeron-omat-taskit#138): this host's
  card has been a Turing GTX 1660 Super (compute 7.5, 6 GB) since 2026-10-28, and the 970
  build above has no code for it (`cudaErrorNoKernelImageForDevice`). `HOMR_GPU=cuda
  scripts/install-homr.sh` installs homr's `[cuda]` extra instead and keeps it for the Update
  button (`gpu_extra=cuda` in the marker; `HOMR_GPU=cpu` goes back to the CPU). The card
  gives only NaN with homr's fp16 segmentation model, so homr falls back to the fp32 one on
  the card (eerovil/homr#99); its fp16 encoder and decoder are fine. Page 1 of Virta at
  200 dpi: 222 notes, every pitch and length the same as the CPU's, 10.2 s against 15.2 s.
  eero-pc no longer reads (Eero's choice, #138): the runner copies this host's venv and needs
  its `ort_wheel`, so it says it has none to copy and the page is read here. Output is streamed line by line to a `log` callback —
  a page is ~30s and a song 2–5 minutes, so that is the progress channel #93 asked
  for — and every failure raises `HomrError` carrying the tail of what homr said.
  Including the quiet one: homr **deletes its own MusicXML when parsing raises**, so a
  zero exit with no file is a failure and is reported as one. The deadline is a timer
  that kills the process group, not `wait(timeout=...)`, because reading the pipe is
  what blocks. Not installed is its own error (`HomrMissing`) naming the install
  script.
  **A page is read under one of this host's heavy slots**, through the `heavy_slot`
  client described above. A page is ~30s of every core on a four-core host shared
  with the deck's own suites, which is exactly what that queue is for. Its two backwards-looking rules
  carry over unchanged and are not re-argued here: **failing** to get a slot is
  fail-open (the scan runs unqueued with a line in the log), **losing** one is not (a
  heartbeat answering 404 means the cores may already be somebody else's, so the page
  stops with `SlotLost`). homr's own output lines are the checkpoints `Slot.guard`
  needs, and abandoning the read loop kills homr's process group — otherwise it would
  keep running on cores that have been handed away.
  **Another machine may read a band first** (#437, #439): when `.env` names
  `REMOTE_RUNNER`, `read_page` runs it as `<runner> read-band <image> <out dir> <homr venv>
  <homr flags...>` for the installed homr (never a branch engine), streams what it
  prints into the log, and uses `<out dir>/<image>.musicxml` when it exits 0 with the file
  written; anything else reads the band here under the heavy slot, as without one. A runner
  given up on (`REMOTE_TIMEOUT`, or an error while reading) gets SIGTERM and `REMOTE_STOP_S`
  (30 s) to end its job over there before SIGKILL (#443); a local homr is killed at once. The
  runner shipped is `src/kube_runner/runner.py` (see "Where heavy work runs" in
  `docs/pdf-to-practice-track.md`); on the owner's host it reads on eero-pc's GPU.
  `test_remote_runner.py` pins the contract with a stub runner.
  `scripts/ssh-runner.sh` (#483) is a second runner, over plain SSH (`SSH_RUNNER_HOST`): it sings
  lines only (`read-band` exits 2, so the band is read here), copies this checkout's `sing_ufr.py`
  over with each line so the two sides cannot drift, and on SIGTERM ends the singer over there.
  `src/sing_song/tests/test_ssh_runner.py` runs it against stub `ssh`/`scp`.
  **A homr abort after a whole result is kept** (#483): homr can die in onnxruntime's shutdown
  (SIGABRT, exit -6 or 134) after writing its MusicXML. `read_page` uses the file when it parses
  and holds a measure, and logs it; any other crash, or an abort with no whole file, still fails.
  **One slot per page, not one per song.** The page is the unit `read_page` owns and
  each page writes its own MusicXML, so releasing between pages lets a suite in, and an interruption costs the page in flight rather than the song — the
  pages already read are on disk. A caller that would rather hold one lease across a
  whole song passes `queue=False` and wraps the loop itself, so the two never nest
  (nesting would deadlock a one-slot pool).
  **Slur pairing used to happen here and is homr's now** (#144: eerovil/homr#62's
  `homr/slur_resolution.py`; the app copy was removed once the fork was installed and the
  old pass dropped nothing on its output). What follows is why it exists, kept because
  the reasoning is the fork's justification too. It was `resolve_slurs`, added for #113. homr predicts
  `slurStart` / `slurStop` one note at a time and never pairs them, and the MusicXML
  `number` that pairing depends on is the *staff* number — the same for every slur on
  the staff. So a dropped stop does not merely lose its own slur: it leaves the start
  open to be closed by whatever stop comes next. On B5's whole page that produces two
  slurs nobody engraved, one of 5¼ bars and one of 3, covering 21 notes; a slur
  continuation takes no syllable, so the page offers **91 lyric slots for 132 notes**.
  Sixteen swallowed. The direction is the trap: it reads as `too_few`, which the
  playbook below teaches a reader to attribute to a voice sharing another staff's
  words. So `read_page` pairs the tokens the way MuseScore does and keeps only pairs at
  most **one barline** apart (`MAX_SLUR_BARS`, env `OMR_MAX_SLUR_BARS`). That threshold
  is measured, not assumed: across all seven benchmark parses every pair is nought or
  one bar apart except those two runaways, the human-corrected `Lemmen nosto` has no
  longer slur in the 68 bars of the page they come from, and one barline is what a
  genuine melisma crosses (`il-man il-ki-rii-vi-`). It is a claim about *this input*,
  not about engraving — real scores in `songs/` do print four-bar phrase marks, but
  homr has no way to write one deliberately.
  **This is the boundary and not the assembler**, which is where #113 first put it. The
  `number` the mis-pairing turns on is the staff number, so nothing about the defect is
  per-page or per-crop: a whole-page parse has it and so does one system cut out of the
  same page. Normalising it here means every parse gets it however it was cropped, and
  it does not go down with #103 if that card is thrown away.
  **The unmatched tokens go as well, and that is the part that cost the most to find.**
  #112 measured a lone dangler as cosmetic, and in isolation it is — MuseScore drops it.
  In a stream it is not: an unmatched stop loses *every later slur of that number*, and
  a redundant start is merely promoted when the runaway in front of it is removed, so it
  closes on a stop further away still. Measured on B5: dropping the runaway pairs alone
  left a fresh 2-bar runaway at m51, taking out the redundant starts alone took the page
  from 21 slurs to 6, and doing all of it in one pass gives 24, none over a bar — five
  of them short slurs homr got right that the noise had been costing it. What is written
  back is one alternating stream, so the score and the pairing cannot drift apart, and
  running it again finds nothing. A parse with nothing to change is left byte for byte
  as homr wrote it.
  **Every parse also comes back with its whole-measure rests in a voice of their own**
  (`split_measure_rests`, proposed by this pull request for #164, off #130's
  measurement). homr's token language has no way to say "second voice on this staff" —
  no voice token, no voice field on its symbol record, and `upper`/`lower` meaning
  *staff* rather than voice, which upstream state themselves in liebharc/homr#126. So a
  printed whole-bar rest and the notes of the voice engraved beside it come out sharing
  one `<voice>` with no `<backup>` between them, and the bar overfills by a whole note
  automatically. The bar this was found on is 4/4 and came out **seven quarters long**.
  Nothing downstream catches it: `preprocess_corrupted_measures` declines, and
  `fix_overfull_measures` correctly refuses a voice that ends on a note — so it reaches
  the cleaned score as `len="28/16"`, and every *other* staff's measure rest in that bar
  is then the wrong length, which no health check sees and which surfaced only when
  the (since removed) scrolling video renderer refused it.
  The rule is one sentence: **a rest written `type="whole"` that shares a `<voice>` with
  any other note or rest cannot be that voice's, because a whole-measure rest alone fills
  the bar.** Nothing is invented and nothing is deleted — one voice number changes and
  the measure's cursor arithmetic is redone around it, which is what makes the bar
  shorter; relabelling the `<voice>` alone would leave it exactly as long as it was. It
  needs **no meter**, and that is what makes it a boundary repair rather than
  `clean_score`'s: a per-system crop usually declares no time signature at all, so a rule
  that had to know the bar length could not run here.
  **It is narrow, and measured.** All 41 whole rests across the seven benchmark parses
  and the fixture's nine crops carry a full whole note whatever the meter; the rule fires
  on **two** of them, the two that share a voice. The other 39 rest alone in their voice,
  make no musical claim, and `fix_overfull_measures` already re-lengths them to the real
  bar. End to end on the fixture's nine systems, health goes **24 findings to 22** — the
  two `unprinted-meter` rows on that bar, and nothing else moves.
  **What it deliberately does not fix**, and why the record below is not optional: the
  voice the rest was sharing is left however homr read it, which on that bar is three
  quarters of music in a bar of four, because a quarter rest was lost as well. A *short*
  bar is a better failure than a bar seven quarters long — the missing note surfaces as
  lyric syllable overflow at import, while the long bar silently drags the practice track
  — and inventing it back is what `fix_overfull_measures` refuses to do. But cleaning
  then pads that hole and health goes quiet, so a loudly wrong bar becomes a quietly
  wrong one, which is this project's named failure mode occurring inside a repair. Hence
  `read_page`'s `repairs` argument: a caller that has to tell somebody passes a list, and
  `scan.py` writes what comes back into the song's `fixes.json` (below). A log line is
  not a record. The dotted eighth that bar also lost is **not** fixable in principle — a
  dot rides inside a single rhythm token, so there is nothing to recover — and the
  `rest_0` token bug behind the four-quarter whole rest is homr's under the #141 rule,
  opportunistic to upstream and blocking nothing.
  Deliberately **not** here yet, because they belong to other cards on #92's map:
  pages are not stitched into one score (#97), and the deploy and `/healthz` do not
  know homr exists. `read_page` is called now — by `scan.py`, one band at a time.
