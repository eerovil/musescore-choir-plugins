"""Add words.json to the sung songs already on the site (eeron-omat-taskit#110).

    .venv/bin/python -m src.sing_song.add_words [--slug <slug>]... [--dry-run]

sing_song.py now writes words.json into every sung bundle: what each part sings, note by
note, so the site sings a note several parts start together once only where they sing the same
syllable. A song sung before it has none. For each sung version on the site (each `prefix` in
D1, once even when several lists share it) this reads the parts from song-app's cleaned score
on the site's own clock (its live timing.json), checks every line's notes start where the live
score.mid's do (check_onsets.py), and puts words.json beside the live files. A part gets its
words only when song-app's score, exported again, has every note of the live score.musicxml with
the same bar, beat, length, pitch, voice and words; any other part lists none, so the site never merges its
notes. Each song takes a MuseScore export (DISPLAY must be set). Cloudflare comes from .env, as the app's publish takes it. Nothing is sung
and no new version is made.

A song whose score changed since it was sung, or whose parts are not one staff each (folded
with --fold-divisi, or published with --staff-ids), is skipped and listed: it gets its file when
it is next published. --dry-run checks every song and uploads nothing.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import unicodedata

from lxml import etree

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
from src.sing_song.main import build_bundle, log, sung_key, sung_words, write_words  # noqa: E402


def sung_versions(cf, slugs=()):
    """[(prefix, slug, parts)] of every sung version on the site, each prefix once; `parts` as
    the row lists them (name, and staff and voices where parts share a staff)."""
    # every row, read as JSON: the site's script writes "sung":true, the app's publish "sung": true
    rows = cf.query("SELECT prefix, slug, parts FROM songs ORDER BY slug, prefix")
    out = {}
    for r in (row for part in rows for row in part.get("results") or []):   # the app client's D1 reply
        parts = json.loads(r["parts"] or "[]")
        if any(p.get("sung") is True for p in parts) and (not slugs or r["slug"] in slugs):
            out.setdefault(r["prefix"], (r["slug"], parts))
    return [(prefix, slug, parts) for prefix, (slug, parts) in out.items()]


def cleaned_score(slug):
    """The song's cleaned score in song-app, as build_bundle finds it; None when it has no such song."""
    from src.song_app import state
    song = state.load(slug)
    if song is None:
        return None
    cleaned = song.cleaned_path()
    if not os.path.exists(cleaned):
        cleaned = unicodedata.normalize("NFC", cleaned)   # .song.json may spell it NFD
    return os.path.abspath(cleaned) if os.path.exists(cleaned) else None


def note_lines(musicxml):
    """{(staff, voice): [(bar, beat, length, pitch, lyrics)]}: every note and rest of a MusicXML
    score, staff by staff (the file's parts, 0-based) and voice by voice, in order. `pitch` is
    the note's step, alter and octave (None for a rest), `lyrics` its (verse, syllable) pairs."""
    out = {}
    for staff, part in enumerate(etree.fromstring(musicxml).iter("part")):
        for bar in part.iter("measure"):
            pos = start = 0
            for el in bar:
                if el.tag in ("backup", "forward"):
                    pos += (-1 if el.tag == "backup" else 1) * int(el.findtext("duration") or 0)
                if el.tag != "note":
                    continue
                length = int(el.findtext("duration") or 0)
                if el.find("chord") is None:
                    start = pos
                    if el.find("grace") is None:
                        pos += length
                p = el.find("pitch")
                pitch = None if p is None else (p.findtext("step"), p.findtext("alter") or "0", p.findtext("octave"))
                words = tuple((ly.get("number") or "1", sung_key("".join(t.text or "" for t in ly.iter("text"))))
                              for ly in el.iter("lyric"))
                out.setdefault((staff, el.findtext("voice") or "1"), []).append(
                    (bar.get("number"), start, length, pitch, words))
    return out


def has_words(events):
    return any(any(w for _, w in e[4]) for e in events or [])


def verified_parts(live, fresh, parts):
    """Which parts' words are the ones they were sung with: [bool] per part. `live` is the
    published score.musicxml (exported from the score the parts were sung from), `fresh` the
    same export of song-app's score now. A part counts only when every note of each of its voices
    is the same in both, at the same bar and beat, with the same length, pitch and words: checking
    note starts (check_onsets.py) and words apart would let fixed, moved or swapped words, or
    voices that swapped pitches, through (#110 review). On its own staff every voice counts; a
    part sharing a staff needs its own voices to have words in the live file, else (the export
    keeps one voice's words) it cannot be checked and does not count."""
    old, new = note_lines(live), note_lines(fresh)
    shared = any("staff" in p for p in parts)
    out = []
    for i, p in enumerate(parts):
        staff = p.get("staff") if shared else i
        if p.get("voices"):
            keys = {(staff, str(v)) for v in p["voices"]}
            ok = all(has_words(old.get(k)) for k in keys)
        else:
            keys = {k for k in old.keys() | new.keys() if k[0] == staff}
            ok = any(has_words(old.get(k)) for k in keys)
        out.append(staff is not None and ok and all(old.get(k) == new.get(k) for k in keys))
    return out


def words_for(cleaned, names, timing, midi, work):
    """Each part's sung_words() from the cleaned score on the live clock, or the reason it cannot
    be trusted (a string)."""
    score = etree.parse(cleaned).getroot().find("Score")
    staves = [s.get("id") for s in score.findall("Staff") if s.find("Measure") is not None]
    if len(staves) != len(names):
        return f"{len(staves)} staves for {len(names)} parts (folded or --staff-ids)"
    with open(timing) as f:
        played = json.load(f).get("measures")
    bars = len(score.find(f"Staff[@id='{staves[0]}']").findall("Measure"))
    if played != bars:
        return f"timing.json has {played} bars, the score {bars}"
    words = []
    for i, staff in enumerate(staves):
        lines = []
        for line in ("high", "low"):
            js = os.path.join(work, f"p{i + 1}-{line}.json")
            r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "unroll.py"), cleaned, staff, timing, js],
                               env=dict(os.environ, LINE=line), capture_output=True, text=True)
            if r.returncode:
                return f"{names[i]}: unroll.py failed: {(r.stdout + r.stderr).strip()[-300:]}"
            r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "check_onsets.py"), js, midi, str(i), timing],
                               capture_output=True, text=True)
            if r.returncode:
                return f"{names[i]}: the score no longer matches the live piano ({(r.stdout + r.stderr).strip().splitlines()[0]})"
            with open(js) as f:
                lines.append(json.load(f))
        words.append(sung_words(lines))
    return words


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slug", action="append", default=[], help="only this song (may be given more than once)")
    ap.add_argument("--dry-run", action="store_true", help="check every song, upload nothing")
    a = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    from src.song_app import publish
    config = publish.Config.from_env()
    if config is None:
        sys.exit(publish.MISSING_CONFIG)
    cf = publish.Cloudflare(config)
    versions = sung_versions(cf, set(a.slug))
    log(f"{len(versions)} sung versions on the site")
    added, skipped, partial = 0, [], []
    for prefix, slug, parts in versions:
        cleaned = cleaned_score(slug)
        if not cleaned:
            skipped.append((prefix, "not in song-app"))
            continue
        with tempfile.TemporaryDirectory() as work:
            timing, midi = os.path.join(work, "timing.json"), os.path.join(work, "score.mid")
            try:
                for name, path in (("timing.json", timing), ("score.mid", midi)):
                    with open(path, "wb") as f:
                        f.write(cf.get(prefix + name))
            except Exception as e:   # PublishError: Cloudflare said no
                skipped.append((prefix, f"cannot read its files: {e}"))
                continue
            try:
                live = cf.get(prefix + "score.musicxml")
            except Exception as e:   # PublishError: Cloudflare said no
                skipped.append((prefix, f"cannot read its score: {e}"))
                continue
            names = [p["name"] for p in parts]
            words = words_for(cleaned, names, timing, midi, work)
            if isinstance(words, str):
                skipped.append((prefix, words))
                continue
            fresh = os.path.join(work, "fresh")
            os.makedirs(fresh)
            build_bundle(ROOT, slug, fresh)
            with open(os.path.join(fresh, "score.musicxml"), "rb") as f:
                ok = verified_parts(live, f.read(), parts)
            if not any(ok):
                skipped.append((prefix, "no part's words could be checked against the singing"))
                continue
            # a part not checked lists nothing: the site sings its every note
            words = [w if good else [] for w, good in zip(words, ok)]
            if not all(ok):
                partial.append((prefix, [n for n, good in zip(names, ok) if not good]))
            out = os.path.join(work, "words.json")
            write_words(out, words)
            if not a.dry_run:
                with open(out, "rb") as f:
                    cf.put(prefix + "words.json", f.read(), "application/json")
        added += 1
        log(f"{'checked' if a.dry_run else 'added'} {prefix}words.json")
    log(f"{added} {'would get' if a.dry_run else 'got'} words.json; {len(skipped)} skipped")
    for prefix, why in skipped:
        log(f"  skipped {prefix}: {why}")
    for prefix, names in partial:
        log(f"  {prefix}: no words for {', '.join(names)} (not checked)")


if __name__ == "__main__":
    main()
