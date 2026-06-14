# `song` — GUI design

A design doc for a unified app over the choir-track toolkit.

> **Status: implemented** (`src/song_app/`, launched by `./song.py`). This doc is
> the rationale and state model; see CLAUDE.md for the as-built module map.

## The problem

Today the workflow is a chain of separate CLIs (`clean_score`, `lyric_txt`,
`record_stemmanauha`) plus manual MuseScore editing plus an LLM round-trip for
lyrics plus QML plugins. The user is the **integration layer**: they remember the
command order, the flags, the file paths; they juggle terminal ↔ MuseScore ↔ an
LLM; and they have to *discover* OCR errors themselves (e.g. the m18 lost-notes
case — nothing flagged it).

## Thesis

> **The tool should own the checklist, not the user's memory.**

The work is irreducibly a *loop with manual fixups* — OCR is unreliable, so a
human will always fix some measures and some lyrics. The goal isn't a magic
one-button pipeline; it's a **single, state-aware app** that always knows what
song you're on, what stage it's at, and what the *human* needs to do next.

The single biggest concrete win of a GUI: **the PDF is always on screen, next to
the thing you're working on.** The old pain was bouncing to the PDF to read
measures; everything else hangs off fixing that.

## Decisions locked

- **Form factor:** local web app. Python backend (FastAPI) serving a browser UI;
  PDF rendering via pdf.js; shells out to the existing scripts + AppleScripts.
- **PDF is copied** into `songs/<name>/` → the song folder is self-contained and
  powers the side-by-side viewer.
- **Mode is a checkbox** ("Staves change parts per system / per-system mode") at
  song creation, editable later. No fragile auto-detection.
- **No automatic LLM.** Users won't have an API key. The lyrics stage is a
  *permanent* manual copy-paste round-trip — the app makes it frictionless but
  never calls an AI itself.
- **No new musical logic.** The app is a thin frontend over the existing
  `clean_score` / `lyric_txt` / `record_stemmanauha` and the existing
  AppleScripts. The only genuinely new code is the app shell + a health check.

## Object model

One concept: a **Song** — a folder `songs/<slug>/` with a state file
`.song.json`. The folder is a **slug** (`laulun-aika`); the human name ("Laulun
aika") lives in the state file and is what's shown everywhere in the UI. The
state file *is* the UX; the user never tracks any of this in their head.

> Note: this changes today's convention of human-named folders
> (`songs/Laulun aika/`). New songs get slug folders; the display name is
> decoupled from the path.

```jsonc
// songs/laulun-aika/.song.json
{
  "name": "Laulun aika",                // human display name
  "slug": "laulun-aika",                // folder name
  "stage": "fix",                       // current stage (see Stages)
  "mode": "per-system",                 // or "normal" — from the checkbox
  "sources": { "pdf": "Laulun aika.pdf", "xml": "Laulun-aika-2.xml" },
  "cleaned": "Laulun aika_cleaned.mscx",
  "cleaned_fingerprint": "sha1:…",      // detects manual edits
  "health": {
    "checked_against": "sha1:…",        // which score version this reflects
    "issues": [
      { "id": "m18-ticks", "measure": 18, "staff": "T2",
        "kind": "malformed-measure", "detail": "voice 2 ticks 1200/1920",
        "status": "open" }              // open | fixed | dismissed
    ]
  },
  "lyrics": { "json": "laulun_aika.json", "imported_against": "sha1:…",
              "warnings": ["m16-19: 11 syllables / 9 slots"] },
  "record": { "exported": false, "youtube_id": null }
}
```

## Stages (the state machine)

Each stage = automatic work + a gate that reports back. Stages are *mostly*
linear; `fix` and `lyrics` form a loop (see below).

| Stage | Auto | Gate (advances when…) | Reports |
|---|---|---|---|
| **register** | copy PDF+XML in, ask name + mode | files present | — |
| **clean** | run split / per-system rebuild | `_cleaned.mscx` produced | parts found |
| **fix** | run **health check** | no `open` issues remain | punch list w/ measure #s; opens MuseScore |
| **lyrics** | guide copy-paste → import → validate | no syllable warnings (or dismissed) | mismatch list w/ measure ranges |
| **review** | open final score | user confirms | — |
| **record** | export / record / upload | media exists | links, YouTube id |

### The non-linear bit: fix ⇄ lyrics

Syllable *overflow* in `lyrics` (e.g. 11 vs 9) is usually a *note* problem (the
m18 lost notes), not a lyric problem. So lyric validation can **re-open the fix
stage** and point at the measure. The UI should express "lyrics say m18 is short
on notes — back to fixing."

## Screens

### 1. Library
Song cards with status badges + a progress rail. `+ New song`.

### 2. New song
Name · drop **PDF** · drop **XML** · ☐ *per-system mode* → Create. PDF copied in.

### 3. Workspace (the main screen)
Persistent 3-part layout; the PDF in the middle **follows whatever you click on
the right**:

```
┌────────┬───────────────────────┬──────────────────────┐
│ STAGES │      PDF VIEWER        │   STAGE PANEL        │
│ Clean  │  (jumps to the         │ (changes per stage)  │
│ ▸Fix   │   measure/system in    │                      │
│ Lyrics │   focus on the right)  │                      │
│ Review │                        │                      │
│ Record │                        │                      │
└────────┴───────────────────────┴──────────────────────┘
```

## Per-stage panels — what the GUI uniquely unlocks

- **Clean.** The per-system prompts become a **grid form** (rows = systems,
  columns = staves, cells = part names), pre-filled from `.persystem_cache.json`
  — which becomes a *visible, editable table* instead of sequential terminal
  questions. Saving the grid re-runs the rebuild.

- **Fix.** Punch list as a table (measure · staff · issue · [dismiss]). Click a
  row → PDF jumps there + `[Open in MuseScore]`. Save in MuseScore → the app sees
  the file change and **re-scans automatically**, ticking off fixed rows. Each
  issue is **dismissable** (health check is heuristic; e.g. the false-positive
  slur on m22) so it stops nagging.

- **Lyrics** (permanent manual copy-paste round-trip):
  1. `[Copy prompt]` — copies `lyric_json_prompt.txt` to the clipboard.
  2. PDF on screen + `[Reveal PDF in Finder]` → user drags it into their own
     ChatGPT/Claude tab.
  3. Paste returned JSON into a box → **validate on paste** (syllable/note
     counts; do the part names exist?) → warnings inline, each linking to its
     measure.
  4. `[Import]`.

- **Review / Record.** Buttons with streamed progress; YouTube link when done.

## Backend architecture

FastAPI over the existing scripts. `./song` starts the server and opens the
browser — same single door.

```
GET  /                          → Library
GET  /song/{name}               → Workspace
POST /song                      → create (name, pdf, xml, per_system); copies PDF in
POST /song/{name}/clean         → run clean_score        ┐
PUT  /song/{name}/systems       → save grid → cache → re-clean │ long tasks run in
POST /song/{name}/lyrics        → import JSON             │ background, stream
POST /song/{name}/record        → export/record/upload   ┘ logs over the WS
GET  /song/{name}/health        → punch list
POST /song/{name}/health/{id}/dismiss
POST /song/{name}/open-score    → `open -a "MuseScore 3" …_cleaned.mscx` (local)
WS   /song/{name}/events        → file-watch + task progress push
GET  /song/{name}/pdf           → serve the copied PDF
GET  /song/{name}/state         → .song.json
```

Two backend mechanisms carry the "magic":
1. **File watcher** (watchdog) on `_cleaned.mscx`: user saves in MuseScore →
   re-run health scan → push updated punch list. ("Never ask *are you done*.")
2. **Background tasks streaming logs** over the WS for clean/record, so the UI
   shows progress instead of a frozen button.

Single user, single local process — no auth, no concurrency concerns.

## Hazards the design must prevent

These are real traps in the current scripts; the state machine has to guard them:

1. **Re-cleaning clobbers hand-fixes.** Once `_cleaned.mscx` is manually edited,
   `clean` is done forever. Re-running must **never** silently re-run
   `clean_score` over the edited file. Re-clean is explicit-only and shows a
   confirm dialog: *"This discards your hand edits."* The Clean button is
   otherwise disabled past that stage.
2. **Lyric re-import vs. manual lyric tweaks.** `--replace` wipes verse-1 lyrics.
   Warn before re-importing onto a score whose fingerprint changed since the last
   import.

## The one hard technical problem: locating a measure in the PDF

The premise is "click m18 → PDF jumps to m18", but **the PDF has no
measure→coordinate map.** It's the original engraving; the OCR'd XML doesn't
reliably retain printed layout. Pixel-accurate jumping would need PDF layout
analysis (OCR bounding boxes) we don't currently produce.

What we *do* know: in per-system mode, `find_systems` gives **system →
measure-range**, so we can locate a measure to its *system*, not a pixel.

- **v1 (recommended):** side-by-side PDF with **page/system-level** navigation.
  "Go to m18" opens the page containing m18's system; the user's eye does the
  last 2 cm. Removes ~95% of the old pain (no more terminal crops).
- **Stretch (deferred):** capture OCR bounding boxes at import → real
  highlight-the-measure. Big effort.

## Reuse vs. new

- **Pure reuse:** `clean_score`, `lyric_txt` import, `record_stemmanauha`, the
  per-system cache, the AppleScripts, the QML plugins.
- **New (small):** the `.song.json` state machine + Library/Workspace UI; the
  **health check** (validation only — the malformed-tick scan already exists in
  `corrupted_measures` / `missing_tuplets`; surface it instead of silently
  fixing).
- **New (big):** none. (LLM vision is explicitly out.)

## Decided

1. **Health-check finding set (v1):** malformed-tick measures, >2-voice
   anomalies, syllable/note-count mismatches. Missing slurs stay manual
   (undetectable); missing notes surface indirectly via syllable overflow.
2. **PDF locate fidelity:** page/system-level only. No bounding-box highlighting.
3. **Song folder naming:** slug folders (`laulun-aika`); human display name in
   `.song.json`.
