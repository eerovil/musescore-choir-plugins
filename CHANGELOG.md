# Changelog

What changed, newest first. The project has no version numbers; changes are
grouped by the day they were merged, and each line names its pull request
(`#123`) on GitHub. Changes that only touch tests, measurements or the notes for
developers are listed under *Behind the scenes*.

## Unreleased

- Answering a problem in the Fix stage no longer throws the list to its bottom:
  the next card moves up into the place of the one answered. (#329)
- A problem card in the Fix stage also says which staff of the printed system the
  part is on, and which voice of that staff ("Bar 3 of 7 · staff 2 of 4, only
  voice"). A per-system song shows it after its next clean. (#310)
- homr's other readings of an unsure bar are offered only on a part printed on
  the staff homr read, so the same notes an octave away on another singer's staff
  no longer pick up the choices. (#310)
- When a repeat ends, the Fix stage asks whether the page prints "1." and "2."
  brackets there and how many bars the "1." covers, with one tap per length. homr
  does not read the brackets, and without them the practice track played the "1."
  bars on both passes. (#319)
- Each part video uploaded to YouTube says in its description which staff the part
  is sung from (`stemmanauha-staff: 2/5`), so the practice site can zoom a phone to
  the right staff even when two parts share one. `backfill_staff_lines.py` adds the
  line to videos uploaded before this. (#323)
- After a YouTube upload, or deleting the uploaded videos, the app asks the
  stemmanauhat site to refresh its video list straight away, so a new song shows
  there within minutes instead of hours. Needs `STEMMANAUHAT_DISPATCH_TOKEN` in
  `.env`. (#321)
- Scrolling videos follow D.C. and D.S. jumps (al Fine, al Coda) instead of refusing
  the score: the bars play in MuseScore's order and the scroll jumps back to the
  segno or the start and forward to the coda. (#314)
- A slur or tie that runs over a line break is kept when the scan joins the
  systems, instead of being lost. When only one half of a slur was read, or the
  next line prints a different number of staves, the note is marked `⚠ slur?` to
  check against the page. Needs a homr that keeps slur ends at the system edge.
  (#318)
- A scrolling video whose repeat jumps back across a time-signature change
  (a 7/4 bar repeating to a 4/4 one where a part rests) renders in time instead
  of being refused as out of sync. (#313)
- A start-repeat sign homr read on only some staves of a printed system is now kept:
  it used to vanish from the score, so the practice track repeated the wrong bars.
  When a repeat ends and the scan found no start for it, the Fix stage asks where
  the page prints the start, with one tap per printed system. (#316)
- A problem card in the Fix stage says which bar of the printed line it is
  about ("Bar 2 of 4 in this line"), so the bar no longer has to be counted off
  the picture. (#311)
- When a lyric line has more syllables than notes, every note still gets its own
  syllable and only the extra ones go on the last note; extra `_` are dropped. It
  used to pile the whole bar onto its first note. (#308)
- The Fix stage lists every problem once, one card per bar and part, with its
  choices beside the page: homr's other lengths for an unsure bar, homr's other
  pitches for an unsure note, and for a slur the scan ran between two singers, the
  slur in either, both or neither. One tap applies the answer and keeps it for the
  next clean. (#290)
- An unsure bar's choices are now whole bars, lengths and pitches already put
  together, so picking one never undoes another. homr's second reading of the bar
  (the one behind a `notes?` mark) is always one of them, labelled "second
  reading". Six show first and the rest are behind "More". (#295)

## 2026-10-07

- The score viewer says when it is waiting: a score being built shows a running
  count, a rebuilt one keeps the old picture under "Updating…", and a failed build
  says why. In Compare and Scan vs page the engraved systems wait in their place
  and arrive top to bottom instead of popping in at random. (#303)
- The Record panel's Preview button now saves every setting (tempo, quality,
  margins, shared staves, NVIDIA encoding) and starts drawing the preview at once,
  with a progress bar and a seconds counter. A new Save settings button saves them
  without previewing. (#302)
- Re-cleaning no longer fails after the per-system grid is answered again. A
  reading picked in the Fix panel now follows its notes to whichever part they
  land in, and is dropped (with a line in the log) if no part sings them any
  more. (#292)
- In the per-system grid, a part named like `S1b` now sings `S1`'s notes (and
  words) in every bar where it has none of its own, instead of resting. (#293)
- A bar a part rests through shows its rest in the middle of the bar after cleaning,
  as MuseScore draws a bar rest, instead of at the start of the bar. (#298)

## 2026-10-06

- A scanned song moves on to Clean as soon as every system is read; there is no
  longer a "This reading is right" button to press. Reading a system again no
  longer sends a song that is further along back to Scan. (#282)
- Cleaning no longer ties notes the page prints separately. A tie was copied from
  another voice whenever it held the same pitch on the same beats; now that voice
  must also sing the same rhythm, so a repeated figure under a held note keeps its
  notes (and its syllables). (#284)
- The health check no longer counts a grace note's length into its bar, so a bar
  with grace notes is not reported as overfull. (#283)
- A warning on a scanned bar is one short word ("⚠ accidental?"), and the note it is
  about is red, so the spot is easy to find in MuseScore. A red note left behind is
  played black in the practice video. (#280)
- A scanned song marks the first bar of a system for checking when a note the
  previous system ended with a printed accidental starts it again without one: a
  note tied over the line break keeps its accidental, and the scan cannot tell.
  (#279)
- A scanned song no longer shows a meter change at every system break when the
  page prints its meter once and carries it (2/2 read back as 4/4). (#277)
- A meter change printed at the start of a system is kept even when the new bars
  are the same length as the old ones (2/2 to 4/4). (#278)

## 2026-10-05

- Scanning a PDF whose pages are stored sideways and turned upright now reads each
  printed system instead of the bottom of one and the top of the next. (#273)
- When homr is unsure how long a bar's notes are, the Fix stage shows its likeliest
  readings drawn under the page; tap the one the page prints and it goes onto the
  score and survives a re-clean. Needs an updated homr (Install / Update homr).
  (#271)
- Scanning marks in red every bar homr is probably wrong about, so it gets checked
  against the page; a scan takes twice as long for it. (#270)
- Per-system cleaning no longer leaves tenors read off a plain treble staff an
  octave too high, and scanned songs no longer share one set of grid answers. (#266)

## 2026-10-04

- The README is now about the web app, with screenshots; the command-line tools
  and MuseScore plugins moved to TOOLS.md, and this changelog was added. (#264)
- On a phone, the stage list moves into a ☰ drawer, the bottom bar stays on
  screen, and the score zooms itself (pinch, or − / + / Fit). (#263)
- The homr install box moved from the Library page to the Scan panel, where homr
  is used. (#262)
- The header has an always-visible **Reload** button. (#259)
- Page images cut from a PDF follow the PDF they came from, so a replaced PDF is
  not shown with old pictures. (#256)
- A health re-check started by the file watcher no longer overwrites a lyric import
  that saved at the same moment. (#254)
- Lyric slots: a slur that starts on a tied-into note, or on a slur end, still
  takes a syllable. (#253)
- **Install or update homr from the Library page**, with its log shown while it
  runs; scans wait while it installs. (#250)
- Scrolling video: **two parts can share a staff** in the picture (the Record
  panel's *Shared staves*). (#248)
- Scanned songs label their printed systems with their bar numbers. (#244)
- New song: leaving the name blank uses the file's own name. (#242)
- Clean takes out slurs that jump from one singer to another and bars longer than
  their time signature, and marks each changed bar in red until a person deletes
  the mark. (#239)
- Clean gives both voices a rest the page prints once for the two of them, and
  resets bars MuseScore 3 would call corrupted; the Review stage says whether the
  file opens in MuseScore. (#237)
- **Find systems** reads the page itself in under a second, without homr; *Ask
  homr* is still there when homr is installed. (#236)
- Scan panel: read every system again with one press. (#232)
- Scanning follows the newest homr: its own note positions are ignored and the
  title is not read. (#224)
- The app's own copies of slur repair and system finding were removed; homr does
  both now. (#222)
- Behind the scenes: kept the app's own system cropping after measuring homr's
  (#226); added Talviuni to the reviewed reference songs (#229, #233); developer
  notes (#218, #228).

## 2026-09-22

- **Download the cleaned score and upload a fixed one back**, so a score can be
  fixed in MuseScore on another computer or from a phone. (#217)
- Behind the scenes: corrected a measurement claim in the developer notes. (#215)

## 2026-09-12

- Find systems uses homr's supported printed-system command. (#214)

## 2026-09-06

- Every scanned system records which homr read it, and the Scan panel shows it.
  (#159)
- A whole-bar rest that shares a voice with notes is moved to a voice of its own,
  and the move is written into the Fix list for a person to check. (#165)
- Review and Fix say outright when a scan is too damaged to be worth repairing,
  instead of only counting problems. (#171)
- Scanned systems keep each note on the beat homr read it on. (#180)
- The time signature of a scanned score is worked out from the bars, since homr
  can only guess the top number. (#184)
- A singer keeps their part across the join between two scanned systems. (#189)
- Behind the scenes: measurements and corrections to them (#163, #194, #198, #199,
  #201), and where an OMR fix belongs (#176).

## 2026-09-05

- Behind the scenes: wrote down which repository an OMR fix belongs in. (#143)

## 2026-09-04

- A scanned band gets only the staves its own system prints. (#140)
- Notation such as clefs and keys carries across joined systems. (#139)
- Behind the scenes: judge a scan against songs the choir has already sung. (#138)

## 2026-09-03

- A system that was read again can be seen again in the comparison. (#136)
- Behind the scenes: recorded where homr runs fastest on this host. (#137)

## 2026-09-02

- **A song can be started from its PDF alone.** (#128)
- A system that read fine can be read again. (#132)
- Run a homr branch from a local checkout, chosen per scan, and name exactly which
  homr each choice is. (#131, #133, #134)
- The app proposes the printed-system bands instead of only letting you draw
  them. (#135)

## 2026-09-01

- **The Scan stage**: read a score off its PDF one printed system at a time with
  homr, see each system next to the page, and approve the result. (#108, #109,
  #111, #119, #121)
- Slurs homr invented are dropped. (#114)
- A score where most bars set their own length gets one health finding that
  counts the odd bars, instead of none. (#126)
- The by-system lyric editor keeps empty slots. (#89)
- **Record a missing slur from the Fix panel.** (#90)
- System crops follow the score they came from. (#91)
- The video render waits its turn behind other heavy jobs on the host. (#102)
- Behind the scenes: committed public-domain benchmark pages. (#120)

## 2026-08-30

- The video numbers the bar at the start of each printed system, not every bar.
  (#79)

## 2026-08-28

- On a phone, the bottom bar stays clear of the browser's toolbar. (#55)

## 2026-08-26

- The scroll preview can play **synchronized audio**, off until asked for.
  (#67, #70)
- Redesigned Review and Record for phones; the preview moved into the viewer.
  (#68)
- Videos render much faster: each tile is drawn from only the music it shows.
  (#71)
- Bar widths in the video change gradually, so the scroll does not lurch. (#73)
- The video's bottom margin starts at 5%, and the framing chosen in a preview is
  remembered. (#75, #76)
- A lit notehead keeps a clean edge. (#65)
- Behind the scenes: the full test suite is left to CI. (#77)

## 2026-08-25

- **Preview the scrolling video in the browser before rendering**, using the
  render's own pixels. (#61, #62)
- Video margins are adjustable. (#50)
- The note in a tempo mark ("♩ = 80") is drawn instead of showing as a box. (#59)
- A recorded fix can be a plain sentence. (#57)
- A word split by a barline stays one word. (#45)
- **Installable on a phone's home screen** (PWA). (#41, #43)
- Songs link to AgentDeck chats, and the button says why it cannot act when it
  cannot. (#51, #53)
- Rendering refreshes, audio progress and reload state are clearer. (#39)
- Behind the scenes: CI on every pull request (#36); the live app's address
  (#44); glyph alignment check (#49).

## 2026-08-24

- The browser no longer serves stale app code or old videos. (#35)
- Render progress is shown, and 4K renders are faster. (#27)
- Choose a tempo when the score has none. (#30)
- Scrolling video: a beat marker, rests highlighted, and whole-bar rests no longer
  pull the focus. (#12, #25, #32)
- A song's recorded fixes are applied every time it is cleaned. (#10)
- A **New issue** button in the header. (#24)
- The PDF viewer scrolls on a phone. (#16)
- Clearer lyric hyphen guidance. (#33)
- Behind the scenes: reliable test suite (#14), easier pipeline checks (#18),
  agent worktrees no longer block the deploy (#19), a background-render fix (#21).

## 2026-08-23

- **Scrolling practice videos rendered from the score**, now the Record stage's
  default. (#7)
- Per-system scores are repaired like any other, and two singers written as one
  chord are split. (#9)
- Lyric import returns its warnings as data the app can point at. (#5)
- The per-system grid understands `-` (this staff is silent from here). (#4)
- Behind the scenes: per-system answers behind one interface (#3); a browser test
  for clean → lyrics (#6); new-machine setup notes (#8).

## Before August 2026

The project started in 2020 as a set of MuseScore 3 plugins (`export.qml`,
`voice2.qml`, lyric copy and replace, the rest-track spacer), grew Python scripts
for splitting voices, fixing lyrics and screen-recording practice videos, and then
the web app that ties them together. That history is in `git log`.
