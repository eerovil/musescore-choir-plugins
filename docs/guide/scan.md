# The scan stage: `scan.py`, `system_finder.py`, the Scan panel

Part of this repository's agent guide; the index is [CLAUDE.md](../../CLAUDE.md). Moved out of it word for word (#513). These are bullets of the guide's section on the song web app, which is split over [song-app.md](song-app.md), [song-app-panels.md](song-app-panels.md), [omr.md](omr.md), [omr-systems.md](omr-systems.md) and [scan.md](scan.md), in that order; "above" and "below" in the text may point into a sibling file.

- `scan.py` is added by this pull request: the **scan stage**, which is what makes the
  app able to read a score off its PDF rather than be handed one. `omr_systems` could
  read a band and join the bands up, and nothing called it. This does.
  `STAGES` gains **`scan`**, between `register` and `clean`, and **registering inverts**:
  a PDF on its own is now a song and starts at `scan`, while a song handed a score still
  starts at `clean` — importing MusicXML from elsewhere stays the manual route (#86).
  The eight-stage rail costs the 48 existing songs nothing, and the reason is worth
  keeping: the rail marks a stage done when its index is below the current one, so a
  song sitting at `clean` reads `scan` as satisfied without anything being recorded to
  say so. **Having an input score is what being past scanning means.** `import_legacy`
  says the same thing from the other end — a folder with a score is `clean` as it always
  was, and only a folder with nothing but a PDF is `scan`.
  Each band is **padded** by `PAD` (2% of page height, env `SCAN_BAND_PAD`) before it is
  cropped. #112 measured why: B5's second system cropped on its printed bounds came back
  as 2 parts and padded by 60px came back as 3 — the staves the page prints — with the
  same bars, notes and slurs, while tightening the same crop lost five slur tokens,
  because a slur's arc hangs below its staff and a tight edge cuts it off. A fraction
  rather than pixels, for the same reason bounds are: it has to mean the same thing at
  any resolution.
  **One heavy slot per band**, through `omr_systems.read_systems`' own rule, not a
  second one. And **a failed band is a hole, not a failed song**: twenty homr runs is
  twenty chances to fail, and losing the nineteenth must not throw away the eighteen
  that worked. The fragment MusicXML is kept in `songs/<slug>/scan/`, a later run reads
  only what is missing, and the song cannot leave `scan` while a hole is open — an
  assembled score quietly short of a system reads as a complete score and would be
  cleaned, lyricked and sung. A **lost lease** is a hole too: each band takes its own
  slot, so band N+1 asking for a fresh one queues behind whoever the cores went to
  rather than competing with them. What is *not* a hole is anything that is not a way of
  reading a band failing — a missing module, a full disk, a bug — because catching
  broadly turned one unrelated import error into fifteen identical "homr could not read
  this band" holes and hid it completely.
  The assembled input score (`scanned.musicxml`) is **derived**: regenerate it, never
  hand-edit it.
  **The invalidation rule is one idea, built once** (`reconcile`), and this is the part
  most worth reading. Every derived thing records the stamp of what it was made from, and
  `reconcile` walks the chain in dependency order discarding anything whose recorded
  stamp no longer matches the current one. A bounds edit throwing away fragments, a
  re-read throwing away that system's grid answers, and a re-scan clearing the reviewer's
  approval are three rows of that chain rather than three special cases, the cascade is
  free (answers whose fragment is already gone find nothing to have been answered
  against), and a fourth derived thing is a fourth row. Three ad-hoc invalidations is how
  a fourth gets forgotten.
  Two details that carry weight. A fragment holds **two** stamps: the *band* it was read
  from, which decides whether it is still an answer about the page, and the *content*
  that came back, which is what the grid answers, the assembly and the approval hang off
  — so a re-read discards them exactly when the reading came out different, and a re-read
  that came out the same costs a person nothing. And **the band stamp is geometry, not
  index**: `SystemBounds.index` and `Answers` are both keyed positionally, so an inserted
  band silently re-points everything after it, and nothing here compares indices — each
  fragment is checked against the geometry now sitting at its own, which is what makes
  the shift loud.
  Reading a song is where the app finds this out (`_derived` calls `reconcile`, which
  writes only when something really was discarded), so it is said everywhere rather than
  at one route. A song with no scan returns at once, which is every song that predates
  the stage.
  **A fragment also says which homr read it, and that is a third kind of thing: it is
  provenance, and it invalidates nothing.** This pull request proposes it (#154, #157).
  A parse carries the reading engine's commit, label and dirty flag in the MusicXML
  itself as well as in `.song.json` — the file, because a fragment is routinely opened
  straight off disk by something that never opens the app, which is exactly how #129 came
  to spend a session diagnosing a defect that had been fixed months earlier. It is
  deliberately **not** a `reconcile` row: the band and the content are what a fragment was
  *made from*, and the reader is not — the crop is the same crop and the parse is still
  the parse. Making it a stamp would discard 48 songs' fragments and re-read them for
  hours because one person ran one script, to fix what is an information problem. So
  `content_stamp` strips the provenance line before hashing, which means a system re-read
  by a newer homr that came back the same costs its grid answers and the reviewer's
  approval nothing; a reading that came back *different* costs exactly what it always did.
  Upgrading homr discards nothing, re-reads nothing, and lapses no approval. Re-reading
  stays what it already was — a person pressing the per-system or whole-song button.
  Fragments made before this read as **unknown**, which is the true value: "nobody knows
  which homr wrote this" is the state #129 was in, said out loud. `scan.status` puts the
  per-system record and `homr_now` (the engine installed today) on the wire, and the Scan
  panel's **Read by** section and the `Scan vs page` rows show them; the approval banner
  may say it was given under an older homr and never takes it away.
  **A whole-measure rest the boundary moved is written down here, and that half is not
  optional** (#164, proposed by this pull request). `omr.split_measure_rests` straightens
  the bar; `_record_repairs` → `pipeline.record_scan_repairs` writes one `text` entry per
  moved rest into the song's `fixes.json`, naming the printed system, the bar, the staff
  and the voices, and saying that the voice the rest was sharing may still be short of a
  note. The Fix panel lists it as outstanding (`pipeline.free_text_fixes`). Repairing it
  quietly would be worse than leaving it: cleaning pads what the repair leaves, health
  then says nothing, and a bar that was loudly wrong becomes quietly wrong. A `text` entry
  and not a replayable kind, because there is nothing to replay — the repair happens at
  the boundary on every parse, so a re-clean gets it for free, and what is left over is
  the judgement, which is a person's. Re-reading a system **replaces** that system's
  entries rather than adding to them, so a band read five times is one sentence and a
  re-read that came back clean takes the old one away; entries somebody typed are never
  touched. It is written after the fragment is on disk, and a `fixes.json` broken by hand
  costs the record with a line in the log rather than the twenty bands of reading —
  cleaning is where such a file is refused properly.
  Measured end to end on the fixture, real crops and real homr: **15 systems, 201s, no
  holes, 52 bars** — the same bar count as the fixture's own cleaned score — every system
  finding the 2 staves the page prints.
  **A whole reading moves the song on by itself** (#281). From #99 to #281 it did not:
  only a person's OK (`scan.approve`) moved a song off `scan`, on the argument that the
  dangerous parse is the **tidy** one. The owner removed that gate. Checking a whole
  reading against the page could not really be done on that screen, so the OK was
  pressed without looking, and since re-reading a system lapsed it, songs already cleaned
  and lyricked were sent back to `scan` and stayed there. The tidy-but-wrong parse is now
  caught later and bar by bar — homr's `⚠` doubt marks (#245), the Fix panel's other
  readings of an unsure bar (#269), the health findings and Review's approval, the one
  approval left, which still lapses when a re-read changes a system. So `_assemble` moves a
  song on `scan` to `clean`, a re-read never moves a song backwards, and only a **hole**
  keeps or puts a song back on `scan` (`_drop_assembled`), because a score missing a system
  must not be cleaned. `reconcile` also moves on a song the old gate left waiting, and
  says so (`scan.MOVED_ON`, shown and logged as it stands rather than as a discard).
  **A page nobody marked is refused, not scanned.** `pages_without_bands` is a
  precondition rather than a hole to fill later: the scan reads the bands and nothing
  else, so an unmarked page is music that would never be read at all and the assembled
  score would still look complete. Without poppler it answers "no gaps" rather than "every
  page is a gap" — a missing binary must not be indistinguishable from an operator who has
  not drawn them yet.
  **A page with no music can be left out** (#480): a title page, a page of piano solo.
  The Systems editor's *Leave this page out* switch stores it in `.systems.json` as
  `"skipped_pages": [n, ...]` (`pdf_systems.load_skipped`), and `pages_without_bands` counts
  it as marked. A band on a left-out page is refused on save, Find systems proposes none
  there, and a save that does not name `skipped_pages` keeps the ones stored. Saving bands
  pings the song's `state`, so the Scan button turns on without a reload.

- `system_finder.py` is added by this pull request: **where the printed systems are**,
  proposed rather than decided. Every band on this host was drawn by a person dragging,
  or by an AI reading a page with a percentage ruler on it — and a song cannot be
  scanned until every page is marked, so that drag is the front door of the whole scan
  route. This proposes the bands and hands them to the **Systems editor unsaved and
  dirty**, exactly as if they had been dragged (`POST /find-systems`, the `Find systems`
  button). Nothing writes `.systems.json`, which is the same argument the scan's own OK
  rests on: the tidy-looking answer is the one worth looking at.
  **Why this can work when #80 could not.** That attempt read the pixels itself —
  morphology for the staff lines, the left-margin bracket for the grouping — and died at
  half a degree of skew, at 20% ink dropout, and on the editions that print no bracket
  (it agreed with the score twice out of nine songs). This asks **homr**, which finds
  staves for a living: the same segmentation network and the same `detect_staff` that
  read the music, stopped before any of it is parsed. Since #144 the whole proposal is
  homr's own command (`--find-system-bounds`, eerovil/homr#65, documented in the fork's
  `SYSTEM_BOUNDS.md`): this module only schedules it a page at a time and turns its JSON
  into `SystemBounds`. It is reached through the same `Engine` the scan uses, so proposing
  bands and reading music are the same homr, and a homr too old to have the command is
  refused by name rather than answered by an app-side copy of the rule. A page is ~8s (a segmentation pass, not a parse)
  and takes **one heavy slot per page**, `omr.py`'s rule unchanged.
  **The grouping is decided by the barlines, and that is the whole idea.** Which staves
  make one system is what homr does not answer — its `MultiStaff` is a brace or a grand
  staff, which choral engraving mostly does not print. The obvious rule fails on the
  measurements: on page 1 of the fixture the gaps *inside* a system run 0.060–0.077 of
  the page and the gaps *between* systems run 0.067–0.086, so no threshold separates
  them. What separates them is what the music says — two staves of one system carry the
  same bars, so their barlines stand at the same x, and two staves of different systems
  do not. Measured across seven pages: 0.6–1.0 agreement within a system, 0.0–0.5 across
  a break. The opening and closing lines are not counted, since every system has them
  wherever its bars fall. The gaps keep one job, as a **veto**: a break must not sit
  *closer* than an ordinary within-system gap. That catches a scan that lost a staff's
  barlines outright (B1b's last system at 0.91 of an ordinary gap, one pair on fixture
  page 3 at 0.97) without touching a real break (1.05 to 1.37).
  Band edges are halfway between systems, so the lyrics under a system's last staff stay
  with it; the first and last get the same room again, clamped to the page — a generous
  band costs white paper and a tight one cuts the words off.
  **Measured against the bands a person actually drew**: all 15 of the fixture's, plus
  B1a and B1b. Every page comes back with the systems it prints, and every internal
  boundary within 0.02 of page height of the hand-drawn one (worst 0.020 on B1b, the
  rest ≤0.014). That is what `test_system_finder.py`'s `omr` tier pins, and it still
  passes through the fork's command; the rule itself is pinned in the fork.
  **`Find systems` no longer asks homr by default; it reads the page itself** (#234,
  `system_finder.quick_bands`). Asking homr is ~8s a page and needs homr installed, and a
  person drags the bands into place anyway, so the default is a deterministic finder that
  takes well under a second a page. It answers #80's two failures: staff lines are found
  in 24 narrow vertical strips and kept when a quarter of the strips agree, so a tilted or
  broken line is still whole across one strip; and two staves are one system when a single
  column of ink spans the gap between them — the systemic barline, which every scan on this
  host prints whether or not it has a bracket. When nothing on a page joins, the gaps decide
  if they clearly come in two sizes. Measured against the hand-drawn bands of all 14
  scanned songs here and B1a/B1b: every page comes back with the number of systems it
  prints, internal boundaries within ~0.035 of the hand ones (it cuts halfway between
  systems). `Ask homr` (`{"method": "homr"}`) is still there, shown only when homr is
  installed. `test_quick_system_finder.py` pins it: drawn pages for the rules, the fixture
  and both B1 scans for the acceptance (needs poppler, no homr).
  **A grey page sets its own ink threshold** (#480, `_ink`): ink was anything darker than
  230, so a scan on grey paper read as ink from edge to edge and no system was found on any
  page. A page whose median brightness is 240 or less now counts as ink what is clearly
  darker than its own paper: 10 grey levels below the median, or four times the paper's
  grain on a noisy scan, and never above 230. A whiter page keeps 230, so the pages
  already drawn by hand read as before. Otsu's method was tried first and refused: on
  near-white paper with faint staff lines its threshold fell between the black ink and
  the lines, so the lines were read as paper and the page lost its systems.
  `scripts/finder_vs_hand.py` counts the systems found on every hand-drawn page in `songs/`
  against the bands drawn there (run it before and after a change to the finder). The
  lyric editor's staff finder and `bar_box` use the same `_ink`.
  **What joins two staves is stricter** (#480, `_joined`): pages over-split when a tight
  crop put the systemic barline within the old 3% edge margin, and under-split when a
  scan's printed border ran down the side through every gap. Now the outer **1%** is
  ignored; a joining column must also run through the half of each staff next to the
  gap; it must be darker than halfway between the paper and the page's darkest ink (a
  grey shadow along the binding is not, nor a faint scan line where the staves start); and a **border column** never joins (`_border_columns`): one inked
  through the page's top or bottom margin, or, within 3% of a side, one inked down every
  staff of the page **and** through the two staff heights above the first or below the
  last, read with the ink smeared 10 px sideways so a frame on a tilted scan is still
  one line, plus 8 px either side. A tall system's own barline stops at its system and
  so is not a border. The owner chose the 1%, the run into both staves and
  the margin rule; the shadow and the near-side reach rule were added when the host's
  pages showed them. `scripts/finder_vs_hand.py` on this host's hand-drawn pages: 42
  pages read a different system count from the hand-drawn one before, 12 after, and none
  got worse.
- The **Scan panel** is added by this pull request (#116), replacing the holding one that
  #115 left. Its whole job is to stop a tidy-looking parse becoming a practice track, and
  every piece of it follows from that.
  **It opens on the Systems editor** (`renderWorkspace` picks `systems` as the first
  document for a song at `scan`), and the Scan button stays disabled until every page has
  bands. No fourth stage for bounds: one stage, one screen, and the thing that must happen
  is the thing in front of you (#103). Bounds are dragged by hand, always — nothing
  proposes them, and #80 measured why.
  **During the scan** it is the raw log, the same one `clean` and `publish` show, streamed
  over the per-slug WebSocket. ~20 systems at 16–20s, and on a busy host it stalls between
  them waiting for a heavy slot — `heavy_slot` already says so in that log, which is the
  whole mitigation.
  **A hole is visible, blocking, and retried on its own**: each one is listed with what
  homr said and a button that re-reads that system alone. It was a `prompt()` asking for
  comma-separated numbers.
  **A system that read fine can be read again**, from the panel (numbered buttons) and
  from each row of `Scan vs page`. A scan skips a band whose geometry has not moved, so
  until this the only way to look at a system again — with the other engine, or because
  it came back wrong — was to drag its boundary: editing the page's own geometry to
  provoke a side effect, which then also invalidated the band stamp for a reason that
  was a lie. The cost is said on the row rather than in a dialog, because it is only a
  cost when the reading actually changes: `reconcile` discards that system's answers and
  lapses Review's approval when the content stamp moves, and a re-read that came out the same costs
  nothing. The panel owns the run (it has the engine picker and the log) and publishes
  it as `scanRerun`, so the compare rows re-read through the same call rather than a
  second copy of it — carrying its slug, since a closure from another song would
  otherwise still answer.
  **Which homr reads it is a picker, not a config file** — a `Read with` select over
  `GET /api/homr-engines`, shown only when this host has more than one installed and
  applying to that run and its per-system retries. Trying a homr branch against real
  music otherwise meant editing `.env` or reinstalling over the engine being compared
  against; see the `omr.py` notes for why a branch is installed beside the default.
  **And which homr actually read each system is shown** — a `Read by` section grouping the
  systems by the engine that produced them, the engine installed today beside it, and one
  line saying that a difference has discarded nothing and that re-reading is the button
  above. The `Scan vs page` rows say the same per system, where the reading is being
  judged. Proposed by this pull request (#157); the rule it follows is under `scan.py`.
  **Comparison is system by system**, in a `Scan vs page` viewer tab: each printed crop
  (`/system/{index}`) above what was read off it, engraved through MuseScore
  (`/scan-system/{index}` → `pipeline.scan_system_render`, `-T` so a one-system fragment
  comes back as that system rather than a mostly blank A4). The Fix panel's `/compare`
  idiom, one stage earlier. Page-against-page is not offered, because a whole A4 rendered
  small enough to look at cannot show a slur — this repo has made a confident and
  substantially wrong reading that way once already. A system that could not be read shows
  its reason **in its own row**, so the sequence stays intact instead of the comparison
  quietly skipping a system: that is what "a refused parse is kept and shown" comes to
  here, since nothing in the app refuses a parse on its shape today.
  **There is nothing to approve** (#281; #116 had one explicit OK here). Once every system
  is read the panel says so, says the song is on Clean and that unsure bars are marked `⚠`
  and listed in Fix, and offers a **Go to Clean** button that only changes the view.
  The comparison is redrawn when the scan moves and not otherwise (`_refreshScan`), since
  redrawing reloads every crop; a system read while it is open therefore appears in it.
  On a phone it is the existing pane switcher and the compare rows, both already built
  for 390px.
