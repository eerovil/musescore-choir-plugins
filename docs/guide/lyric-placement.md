# Lyric placement

Part of this repository's agent guide; the index is [CLAUDE.md](../../CLAUDE.md). Moved out of it word for word (#513).

## Lyric placement (`src/clean_score/lyric_txt.py`)

The most intricate module, and the single owner of lyric placement: format
normalization, target routing, chord eligibility, syllable distribution, XML
placement and the diagnostics that fall out of it. It round-trips lyrics between
`.mscx` and a plain text or JSON format, designed so an LLM can fix lyrics against
the original score (e.g. a PDF pasted into the chat) without breaking syllable
alignment. The prompt files `lyric_json_prompt.txt` / `lyrics_txt_prompt.txt` drive
that.

Its interface — all three callers (the CLI file adapters, the AI-JSON paste, the
song app's manual editor) go through these, and nothing else is public:

```python
export_lyrics(root) -> str                      # the TXT projection of the score
place_lyrics(root, source, fmt=, replace=, split=, systems=) -> LyricImport
editor_grid(root, systems=) -> EditorGrid       # parts x printed systems, prefilled
slot_counts(root) -> {staff: {measure: n}}      # notes that take a syllable
syllable_slots(root, staff, measure) -> [bool]  # the same, note by note
lyric_parts(root) -> [EditorPart]               # the parts that carry words
blocks_from_boxes(grid, verse, boxes) -> [block]  # the by-system editor's changed boxes (#397)
printed_staves(root, start, end)                # a system's printed staves: all, singing
blocks_from_cells(grid, cells) -> [block]       # the older editor's cells as lyric JSON
export_file(...) / import_file(...) -> LyricImport      # the .txt/.json adapters
```

The last two are added by this pull request, for the Fix panel's slur recorder.
`syllable_slots` is `slot_counts` told per note rather than per bar, and it has to
live here rather than be read off the chords: a note in the *middle* of a slur
carries no marker of its own, so eligibility is stateful along the staff and cannot
be decided by looking at one chord. Both counts now come off a single pass
(`_eligibility_per_measure`), so the per-note answer and the per-bar one cannot
disagree. `lyric_parts` is the part list `editor_grid` already built inline — track
names minus the click/spacer staff — pulled out because more than the lyric editor
now needs to offer a person a list of parts to point at.

`source` is TXT, JSON text, or already-parsed JSON blocks (`fmt` overrides the
sniff). **Diagnostics are returned, never printed**: `LyricImport.mismatches` is a
list of `Mismatch(kind, message, measure_start, measure_end, staff_ids, syllables,
slots, verse, voice)` — kinds `too_many` / `too_few` / `no_systems` / `no_system_for_line` /
`block_count` / `part_rests` (a line naming a part with no notes in its system, #482) /
`unknown_part` (a `"parts"` name no part, or more than one, carries) / `two_lines` (a
named line and a `staff_number` line on one part; the named one is used)
— plus `filled_measure_starts` for nulls inferred from the printed
systems. `to_dict()` puts them on the wire for the browser; the CLI wrapper prints
`Warning: <message>` itself, so terminal output is unchanged.

- **Eligibility** (`_walk_voice`): per voice, so a second voice is counted on its own
  notes with the same rules (#397). A note gets a syllable token unless it is a slur/tie
  *continuation* (not the first note of the slur/tie) — those get no token. Rests get
  no token. The TXT export is verse 1 of voice 1; the editor and the JSON reach every
  verse and voice 2.
- **TXT format**: `# Measure N` headers, then `staffId [syllableCount]: tok1 tok2 ...`
  Tokens are space-separated; hyphens join syllables of a word (`il-man`);
  trailing hyphen = the word continues, and that holds between **any** two tokens,
  not only across a barline; a **leading** hyphen says the same thing looking
  backwards, so a line carried over from the previous system may be written
  `-hil-le`. `_` = eligible note with no lyric; after a word ending in `-` it is a
  note the word is held over, so `e-lei- _ _ son` keeps `son` as the word's end (#393;
  a `_` used to end the word, and Kyrie's `son`s came back as words of their own). Syllabic state
  (begin/middle/end/single) is reconstructed from hyphenation on import: a syllable
  is `middle` when a word runs both into and out of it, and reading that as `end`
  splits one word in two. It did — `lai-ne-hil-le` was stored as `lai-ne-hil` plus a
  stray `le`, because the continuation rule was applied only to the first token of a
  measure. The syllables still land on the right notes, so no health check, test or
  mismatch count noticed; what showed it was the by-system editor displaying
  `hil le` where the imported JSON said `hil-le`. See `test_lyric_hyphenation.py`.
  (Alignment is per-measure — the token counter resets at each
  barline — so a missing slur/tie only misaligns within its own measure, not the rest
  of the line.)
- **JSON format**: line-by-line; tokens are *distributed across measures* using
  actual chord counts from the score (`_get_chord_counts_per_measure`). The
  PDF-derived format has a `lyrics` array of `{text, staff_number, position,
  verse, voice, parts}` (`verse` and `voice` default to 1). `staff_number` is the printed staff (top=1); `position` is
  `above`/`below`. These are mapped to **output staff ids** via the
  `lyricsStaffMap` metaTag that `clean_score` writes (`_read_lyrics_staff_map`):
  a printed staff that split into two voices gets the line on both voices when
  only one position appears *in that block* (unison), or split upper/lower when
  both positions appear (divisi is decided **per block**, not globally). An
  explicit `parts` on a lyric overrides the staff_number/position mapping (manual fix
  for the ~inevitable LLM errors). `parts` accepts output staff **ids** *and/or part
  **names*** (`["T1","T2"]`, also a scalar `part`); names resolve via the score's
  trackNames (`_read_part_name_map`). Names are the robust override — immune to
  printed-staff order (e.g. an ossia T3 printed on top), which staff_number cannot
  handle. The current `lyric_json_prompt.txt` has the LLM emit `"parts": []` (empty)
  in **every** lyric so manual overriding is just dropping ids/names into the existing
  array; empty → auto-map by staff_number/position. (An empty list is falsy, so the
  `if parts:` check falls through to the staff_number path — same as omitting it.) Legacy numeric/`_DEFAULT_PART_TO_STAFF` part keys still work. `--split`
  duplicates a part into two staves. When a lyric has no `parts`, import falls back to
  staff_number/position: for `--per-system` scores the printed numbering shifts per
  system, so it uses the per-system `lyricsSystemMap` (`_read_lyrics_system_map`) for the
  block's `measure_start`, else the single `lyricsStaffMap`. Resolution priority per
  lyric: explicit `parts` (ids/names) → staff_number+position via system/staff map.
  A null `measure_start` (the LLM emits null when no measure number is printed at the
  start of a line) is auto-filled by `_fill_missing_measure_starts` (reported as `filled_measure_starts`): blocks are one
  per printed system in order, so each null block takes the start measure of the
  system at its position (`per_system.system_ranges`), skipping a system the map marks
  `rest` (#482: it prints no words) unless there is one block for every printed system,
  rest ones included; a `staff_number` line in a `rest` system is not placed and comes
  back as `part_rests`; explicit values are left alone, and a
  block-count vs system-count mismatch is warned (so the user verifies alignment).
- Import is in-place on the tree and **changes only what it names** (#392, #397):
  a JSON line is one part, voice and verse from its block's `measure_start`, and fills
  its own system (`measure_end`, the caller's `systems`, or the next block), running on
  into the next system only while that one's box stays empty — not named and no words
  in the score, or emptied by this import. Bars it rewrites lose their old words of
  that verse and voice, including on ineligible (spanner-continuation) notes; every
  other verse, voice, part and bar keeps its words. A word a bar starts mid-way is
  joined to the last syllable before it, placed or already there. Where a staff's voice
  2 has words, voice 1's words in that system go above the staff (`<placement>above`,
  `_place_two_voice_words`). `--replace` / `clear_existing=True` still wipes all
  verse-1 lyrics first (for MusicXML imports with garbled OCR lyrics). A score with no
  verse 1 anywhere (words stored as verse 2) is read from verse 2 for verse 1.

When editing `lyric_txt.py`, the export and import paths must stay symmetric — the
test `test_lyric_txt_spanner.py` asserts export→import round-trips back to the
original XML. The `il-man il-ki-rii-vi-` case (where a word's syllables span a
measure boundary) is covered by the two `measure_14` regression tests; the
syllable distribution in `json_lines_to_by_measure` must keep them green.
