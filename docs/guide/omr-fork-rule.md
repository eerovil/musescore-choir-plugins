# Where an OMR fix belongs

Part of this repository's agent guide; the index is [CLAUDE.md](../../CLAUDE.md). Moved out of it word for word (#513).

## Where an OMR fix belongs: the fork or this repo

Scanning is split across two repositories — `eerovil/homr` (the fork) and this one —
and until this pull request there was no written rule saying which gets a given fix.
One was being followed consistently enough to be real and inconsistently enough that
"why is this here?" had a different answer per case. This section is that rule,
proposed by this pull request and settled on #141. This file carries the full
reasoning; the fork's `README.md` carries a short statement of the rule ("About this
fork", merged as `eerovil/homr#22`).

The deck releases a fork pull request with its card too, as long as its description
names the card in full (`eerovil/musescore-choir-plugins#<n>` or the issue URL; a bare
`#<n>` does not count). It goes through the same review and test gates and is not
merged by hand.

**The fork is a permanent home we own.** Not a staging area, not a waiting room on the
way upstream. `scripts/install-homr.sh` installs it and a second host reproduces it by
running that one script, which is the whole story — there is no plan for the fork to
be retired or emptied.

**The rule.** homr's job is to produce MusicXML that, **when rendered, looks like the
original PDF** — including stem direction, which is to say the voices. Anything after
that point is the choir app's.

So: if homr got the page wrong, the fix is the fork's, whether or not the evidence is
still in the pixels. If the parse matches the page and we want something else from it —
a stage, an operator's judgement, a practice track — that is ours. The rule is a claim
about the *output*, which makes it testable: render the MusicXML and hold it against
the page.

That is deliberately wider than "can the fix be made without the pixels?", which is
what had been operating. Under the old reading a defect visible in the MusicXML could
be repaired in either place and the call went both ways for reasons that lived only in
commit messages. Under this one it cannot: a runaway slur is not on the page, so it is
homr's to not emit.

**Which means this repo's OMR boundary layer was drift, and #144 moved it.** Slur
pairing (`omr.resolve_slurs`), per-system reading and the system finder are all "make the
parse match the page", so all three were ported into the fork (eerovil/homr#62, #63,
#65). Once the fork was installed here (`main @ 7652330`) and re-measured on 21 printed
systems, #144 took the app's copies of the **slur repair** and the **system finder's
grouping rule** out: the old slur pass dropped nothing on the fork's output, and *Find
systems* now only asks homr (`--find-system-bounds`). A homr older than that fork is
refused for *Find systems* and its slurs go unrepaired — update it rather than reviving
the copies. **The per-system cropping stays in `omr_systems`, and #223 measured why.**
homr's own `--system-bounds` mode cuts the same padded band at the same dpi with the
same pixel rounding; the one difference is the rasteriser (pypdfium where the app uses
poppler). Re-measured on the fork after the upstream sync (`main @ 8512194`, 200 dpi,
2% pad) with `scripts/system_bounds_vs_crop.py`, on the Virta fixture's 15 systems and
B1a/B1b's 3 each: staves and bars agree on 21/21, the parses are identical on only 4,
note counts differ by at most one per system, and against B1's hand transcription
(onsets only — it writes every notehead as a C) the app's crop gets **107/120** onsets
on B1a against homr's **106**, and **88/120** on B1b both ways, with homr's crop losing
one slur on B1a system 2. The card's rule was to switch only if homr's route was at
least as good, and it is not, so nothing moved. The gap is at noise level, so this is
worth measuring again when a new scanned fixture with note-level truth exists — the
script reuses reads it already has under `--out`.

**`clean_score`'s OCR repairs are the known tension.** `fix_missing_tuplets`,
`fix_spurious_timesigs`, `fix_overfull_measures`, `add_missing_ties` and the recorded
edits in `fixes.json` all exist to make a score match its printed page, so in principle
they are the fork's too. In practice they are not moving: they predate homr, they run on
scores that never went through it — imported MusicXML, the songs already in `songs/` —
and homr has no equivalent of a human-authorised `fixes.json` entry. Recorded here as a
tension rather than as a plan, so nobody acts on the principle without the context.

**The fork follows upstream and takes all of it.** This replaces #158's rule that
upstream is "a source of ideas rather than truth", and the owner changed it on #220: we
do not pick commits out of `liebharc/homr` `main` or judge each one on our pages first —
**every upstream commit is merged, new models included**, and the fork's own commits sit
on top. A sync is one `git merge upstream/main` into the fork (so `git log` says which
upstream commit the fork is on), and it is a card like #220, started when upstream moves.
The harness still measures what a sync did to our repertoire, but **it reports; it does
not decide**: #220 took model 465 although it read three of the five fixtures worse than
model 426 had (`sammon-ryosto` 98% → 41%), and lowered their accepted levels rather than
refuse the model.

Where upstream and the fork changed the same code, the resolution takes **upstream's
version** unless the fork's is something upstream has no equivalent for. #220 is the
example both ways: the note-timing fix upstream wrote for the bug the fork had fixed
itself (df36447 against the fork's per-staff cursors) replaced the fork's, while the
fork's voice, stem and system-bounds work, which upstream has nothing like, stayed and was
wired to upstream's new second-voice tokens (`upper2`/`lower2`).

#130's warning still stands as a fact about the past — upstream `main` once measured worse
on this repertoire than a pinned release, and broke `scripts/install-homr.sh` by moving
onnxruntime into a `[cpu]` extra — which is why a sync is measured and the numbers are
published on its pull request. It is no longer a reason to stay behind.

**Upstreaming our own changes is opportunistic: no obligation, no backlog.** If a fork
commit is clean and somebody feels like sending it, good — nothing is tracked, nothing is
owed, and no decision here ever waits on upstream review.

**The harness stays in the fork.** `fixturecheck/`, `choir-bench.py`,
`choir-worktree.sh` and `choir-k8s.sh` have to run inside homr's venv against a homr
worktree, which this repo deliberately never has. They reach back here through
`CHOIR_REPO` for the fixtures and cleaned scores they judge against, which is the right
direction of dependency: the thing being measured reaches for the truth. The cost is
worth saying — that is about a third of the diverging commits, so the fork can never
again "carry nothing of its own", and running the harness needs both repositories
present.
