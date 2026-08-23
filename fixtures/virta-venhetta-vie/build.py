#!/usr/bin/env python3
"""Rebuild the fixture's stage snapshots from 00-registered.

Run from the repo root:  .venv/bin/python fixtures/virta-venhetta-vie/build.py

Reproduces exactly what the web app does for the clean and lyrics stages
(src/song_app/server.py), so each snapshot is a real app state, not a hand-made
one.  Needs MUSESCORE_CLI_PATH (the MusicXML -> .mscx conversion).  The lyric
text itself is not generated here: 20-lyrics/lyrics.json is a committed
artifact (see STEPS.md, step 3).
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from src.song_app import health, pipeline, state  # noqa: E402

FIXTURE = os.path.dirname(os.path.abspath(__file__))
SLUG = "virta-venhetta-vie"


def _same(a: str, b: str) -> bool:
    return (os.path.exists(a) and os.path.exists(b)
            and open(a, "rb").read() == open(b, "rb").read())


def snapshot(song_dir: str, dest: str, bases: list) -> None:
    """Write dest as an *overlay*: only the files this stage adds or changes.

    Stages layer on top of each other (see reset.sh), so the 745 KB PDF and the
    OCR XML are stored once, in 00-registered, instead of in every snapshot.
    """
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    skip = ("media", ".recording.lock")
    kept = []
    for entry in sorted(os.listdir(song_dir)):
        if entry in skip or entry.endswith(".nolyrics.mscx"):
            continue
        src = os.path.join(song_dir, entry)
        if os.path.isdir(src):
            continue
        if any(_same(src, os.path.join(b, entry)) for b in bases):
            continue
        shutil.copyfile(src, os.path.join(dest, entry))
        kept.append(entry)
    print(f"  -> {os.path.relpath(dest, FIXTURE)}: {', '.join(kept)}")


def main() -> int:
    work = tempfile.mkdtemp(prefix="fixture-build-")
    song_dir = os.path.join(work, SLUG)
    shutil.copytree(os.path.join(FIXTURE, "00-registered"), song_dir)
    state.SONGS_DIR = work

    song = state.load(SLUG)
    if song is None:
        print("00-registered has no .song.json", file=sys.stderr)
        return 1

    # --- Stage: clean (server.py api_clean) -------------------------------
    print("clean:")
    xml = song.source_path("xml")
    cleaned, _ = pipeline.run_clean(xml, song.dir, per_system=(song.mode == "per-system"), log=print)
    song.data["cleaned"] = os.path.relpath(cleaned, song.dir)
    song.data["cleaned_fingerprint"] = state.file_fingerprint(cleaned)
    found = health.scan(cleaned)
    song.data["health"] = {
        "checked_against": song.data["cleaned_fingerprint"],
        "issues": health.merge_issues(found, []),
    }
    song.set_stage("fix")
    song.save()
    print(f"  {len(found)} health issue(s): {[i['id'] for i in found]}")

    # System boundaries are read off the scan by an AI and corrected by hand, so
    # like lyrics.json they are a committed artifact rather than something this
    # script can regenerate. Carry them through into the snapshot.
    src_bounds = os.path.join(FIXTURE, "10-cleaned", ".systems.json")
    if os.path.exists(src_bounds):
        shutil.copyfile(src_bounds, song.path(".systems.json"))
        print("  carried .systems.json (15 systems)")
    registered = os.path.join(FIXTURE, "00-registered")
    snapshot(song_dir, os.path.join(FIXTURE, "10-cleaned"), [registered])

    # --- Stage: lyrics (server.py api_lyrics) -----------------------------
    print("lyrics:")
    src_json = os.path.join(FIXTURE, "20-lyrics", "lyrics.json")
    if not os.path.exists(src_json):
        print("  no 20-lyrics/lyrics.json - stopping after the clean snapshot")
        return 0
    json_path = song.path("lyrics.json")
    shutil.copyfile(src_json, json_path)
    result = pipeline.run_lyric_import(json_path, cleaned, replace=True)
    song.data["lyrics"] = {
        "json": "lyrics.json",
        "imported_against": state.file_fingerprint(cleaned),
        "warnings": [m.to_dict() for m in result.mismatches],
    }
    song.data["cleaned_fingerprint"] = state.file_fingerprint(cleaned)
    if result.ok:
        song.set_stage("review")
    song.save()
    names = {1: "T1", 2: "T2", 3: "B1", 4: "B2"}
    print(f"  ok={result.ok}, {len(result.mismatches)} mismatch(es)")
    for m in sorted(result.mismatches, key=lambda x: (x.measure_start, x.staff_ids)):
        who = "/".join(names.get(s, str(s)) for s in m.staff_ids)
        print(f"    [{m.kind}] m{m.measure_start}-{m.measure_end} {who}: "
              f"{m.syllables} syllables for {m.slots} slots")
    snapshot(song_dir, os.path.join(FIXTURE, "20-lyrics"),
             [registered, os.path.join(FIXTURE, "10-cleaned")])

    shutil.rmtree(work)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
