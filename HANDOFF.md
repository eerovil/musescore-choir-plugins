# Handoff — August 2026

State of the workflow after taking two songs through it end to end. Durable
knowledge lives in `CLAUDE.md` (how the code works) and
`fixtures/virta-venhetta-vie/STEPS.md` (how that song was produced, wrong turns
included). This file is the session-specific part: where things stand and what is
open.

## Where the pipeline stands

`(xml, pdf)` → register → convert → split → repair → health runs unattended. Two
songs reach stage `review` with no health issues and no lyric mismatches:

| song | in | notes |
|---|---|---|
| `fixtures/virta-venhetta-vie` | repo | the prototyping fixture; `reset.sh` drops it into `songs/` |
| `songs/sortunut-aani-2` | working dir only | Sibelius/Breitkopf, in copyright — **do not commit** |

Two steps still need a person, by design: reading the printed system boundaries
off the page (proposed by an AI, corrected by dragging in the **Systems** tab),
and reviewing the result against the scan (**Compare** tab).

**`record` and `upload` have never been run** by any of this work. They are the
untested half.

## What "clean" does not mean

Every check here compares the score against itself, so all of them can be
satisfied by a self-consistent wrong answer — this happened, see STEPS.md step 5.
Reaching `review` means the machine has nothing left to say, not that the song is
right. The words, their alignment, the slurs that did not break the arithmetic,
and any wrong note that still fills its bar are all unverified.

## Open threads

- **Your existing male-choir practice tracks may be an octave high.** Scores whose
  edition omits the `8` under the treble clef were read at face value, so the tenor
  line sounds an octave above where it is sung. `songs/Sortunut` is one: its
  cleaned score ranges 66–82 where it should be 54–70. Creating the song again with
  **Men** selected fixes it, but the recordings have to be redone.
- **`songs/virta-scratch`** is a scratch copy from testing the bounds editor, now
  at stage `upload` with recorded media in it. Delete it whenever.
- **Women's voicing with an F-clef staff** falls back to calling it Alto. No real
  score has hit this; it wants a rule rather than a fallback if one ever does.
- **One unexplained measurement.** An early diagnostic showed the page scrolling at
  4068px against a 720px window; the layout was rebuilt to make that impossible,
  but the original reading was never reproduced.
- **A browser test failed twice and was never reproduced** —
  `test_system_boundaries_can_be_dragged_and_saved`. Its most plausible cause was
  removed (it waited on one page image while later ones triggered redraws), but
  that is not established.

## Things worth knowing before changing anything

- **A song cleaned by an older pipeline keeps its old defects** until it is
  re-cleaned. `songs/Sortunut` still carries damage that `fix_overfull_measures`
  would now repair.
- **Renders are checked, not assumed.** Line breaks alone do not preserve the
  printed layout — at full staff size a wide system is split anyway — so the render
  tries several staff sizes and keeps the largest that yields the expected number
  of systems. Lyrics widen spacing, so a score that fits without them may not with.
- **`fixes.json` is strict.** A recorded score edit that no longer matches raises
  rather than being skipped, so a pipeline change that moves a note fails the build
  instead of quietly leaving the defect.
- **The playbook in CLAUDE.md ("Reading a scanned score") was expensive to learn.**
  Crop before concluding; let the slot arithmetic check the reading; read the XML
  before blaming the OCR; and remember that self-consistency is not correctness.

## Suggested next steps

1. **Exercise `record` and `upload`** on `sortunut-aani-2` or the fixture. It is the
   only stretch of the workflow nothing here has touched, and the scroll-video
   renderer is now the Record stage's default.
2. **Re-do any male-choir song** whose tracks are an octave high.
3. **A third song**, ideally one that is neither homophonic (Sortunut) nor cleanly
   imitative (Virta) — a per-system score would test the least-exercised path.
