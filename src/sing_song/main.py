"""Sing a song's parts and make its practice track; optionally publish it to the site.

    .venv/bin/python sing_song.py <slug> [--publish <choir> ...] [--out DIR] [--voice LYSE|REMY] ...
    .venv/bin/python sing_song.py <slug> --waiting [--publish <choir> ...]   # before queueing a run

One run:

1. Build the bundle (score.musicxml, score.mid, timing.json, manifest.json) from the cleaned
   score with the app's own publish code, with the tempo, tempo changes, part names and credits
   saved on the song's Publish settings (`POST /api/songs/<slug>/publish-settings`).
2. Unless --no-sing: sing every part on the bundle's own clock (unroll.py, then sing_ufr.py),
   each part by the voice its name says (S/A/M: LYSE, T/B: REMY) unless --voice names one. A
   sentence spoken on one held chord is sung a syllable a note (spoken.py).
3. The result stays in --out (default /var/tmp/practice-<slug>/): parts/<n>-<Name>.mp3, the score
   and its MIDI. With --publish <choir>: sent to the site with the app's publish code (publish.py),
   once for each --publish given, recording the singer version it was sung with (SINGER_VERSION).

The run says how it is going in songs/<slug>/.sing-run.json (progress.py), which the song app's
song list shows (#453); --waiting only writes "waiting" there, for a run about to be queued.

See docs/pdf-to-practice-track.md for the whole flow and what the machine needs.
"""
import argparse
import json
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata

from lxml import etree

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))   # this checkout: the app whose publish code builds the bundle
from src.sing_song import progress  # noqa: E402
from src.sing_song.fold import fold  # noqa: E402
from src.sing_song import places as song_places  # noqa: E402
from src.sing_song.spoken import split_spoken  # noqa: E402
from src.sing_song.ufr_pack import is_download, is_ours  # noqa: E402
from src.song_app.publish import sung_file  # noqa: E402

SINGER = os.path.join(HERE, "sing_ufr.py")
# the UFR pack's two voices (https://utaufrance.com/telechargement-du-pack-diffsinger-ufr/):
# (sing_ufr.py voice, the name the site credits)
HIGH, LOW = ("lyse_core", "LYSE"), ("remy_adult", "REMY")
VOICES = {"LYSE": HIGH, "REMY": LOW}
G2P = {"en", "de", "sv", "la", "et"}   # languages g2p.py gives sounds; the rest use the Finnish rules
# a word that looks Finnish in a song in this language: letters or pairs its own words seldom have
# (Latin's "y" is Kyrie's and German's "ä" its own; English "uu" and "wii" are scat). Checked by
# unmarked_finnish(), #67. Estonian is too close to Finnish to tell.
FINNISH_LOOK = {"la": r"[äöå]|aa|ee|oo|yy|kk", "de": r"y|aa|ii|uu|kk|å", "sv": r"aa|ee|ii|uu|yy|ää|öö|kk",
                "en": r"[äöå]|yy|kk"}
LEAD_IN_SAMPLES = 17408   # 34 frames x 512: the silence sing_ufr.py sings before time 0
GPU_ENV = {"GPU": "1", "VOC_CHUNK": "1500", "AC_CHUNK": "6000"}   # chunks that fit a 4 GB card
# scripts/install-singer.sh's venv: onnxruntime, numpy, soundfile, g2p_en, and the CUDA 11
# libraries when the machine has an NVIDIA card. Read after .env is loaded, so SING_VENV there counts.
DEFAULT_SING_VENV = "~/.local/share/musescore-choir-plugins/sing-venv"


def sing_venv():
    return os.path.expanduser(os.environ.get("SING_VENV") or DEFAULT_SING_VENV)


def log(msg):
    print(msg, flush=True)


def part_voice(name, voice=None, voicing=None, clef=None):
    """(sing_ufr.py voice, credit) for a part: `voice` (LYSE or REMY) when given; else the song's
    voicing, a men's song sung by REMY and a women's by LYSE whatever a part is called (a men's
    "Solo" was sung by LYSE, #453); else the one its name says (Sopraano, Altto, Mezzo... -> LYSE;
    Tenori, Baritoni, Basso... -> REMY); else, for a name that says no voice ("Solo"), the one its
    `clef` says (#483: treble LYSE, treble-8 or bass REMY), said in the log. With none of these the
    run stops."""
    if voice:
        return VOICES[voice]
    if voicing == "men":
        return LOW
    if voicing == "women":
        return HIGH
    words = unicodedata.normalize("NFKD", name.strip()).split()
    first = unicodedata.normalize("NFKD", name.strip())[:1].upper()
    # "Solo" says nothing of the voice ("Sopraano solo" does); "" is in every string, so a blank
    # name must not pick LYSE
    says = bool(first) and first in "SATBM" and not (words and words[0].casefold() == "solo")
    if says and first in "SAM":
        return HIGH
    if says:
        return LOW
    by_clef = CLEF_VOICES.get(clef or "")
    if by_clef:
        log(f"voice: {name!r} says no voice; its clef ({CLEF_NAMES.get(clef, clef)}) gives {by_clef[1]}"
            f" (--voice {shlex.quote(name + '=...')} to change it)")
        return by_clef
    sys.exit(f"which voice sings the part {name!r}? Name it by its voice on the Publish panel "
             "(Sopraano, Altto, Tenori, Basso, Tenori solo...), record the song's voicing, or give "
             f"--voice {shlex.quote(name + '=LYSE')} (or REMY)")


# A clef as MuseScore names it (concertClefType) -> the voice that sings it (#483). A C clef or
# percussion says nothing and is not here.
CLEF_VOICES = {"G": HIGH, "G8vb": LOW, "F": LOW, "F8vb": LOW, "F15mb": LOW}
CLEF_NAMES = {"G": "treble", "G8vb": "treble-8", "F": "bass", "F8vb": "bass-8", "F15mb": "bass-15"}


def part_clefs(cleaned, count):
    """The clef each singing part of the cleaned score opens with, top first, as MuseScore names
    it ("G", "G8vb", "F"...): its first staff's first clef, else the staff's or instrument's
    default, else "G" (MuseScore's own default). A list of `count` Nones when the score's singing
    parts are not `count`, since then they cannot be paired with the bundle's."""
    try:
        from src.score_export.score import _part_name, _staff_ids, silent_parts
        root = etree.parse(cleaned).getroot()
    except (OSError, etree.XMLSyntaxError, ImportError):
        return [None] * count
    score = root.find("Score")
    if score is None:
        return [None] * count
    silent = set(silent_parts(root))
    clefs = []
    for index, part in enumerate(score.findall("Part")):
        if _part_name(part, index) in silent:
            continue
        clef = None
        own = part.find("Staff")
        ids = _staff_ids(part)   # by number, or by position for a MuseScore 4 save
        staff = score.find(f"Staff[@id='{ids[0]}']") if ids else None
        if staff is not None:
            clef = staff.findtext(".//Clef/concertClefType")
        if not clef and own is not None:
            clef = own.findtext("defaultClef")
        clefs.append(clef or part.findtext("Instrument/clef") or "G")
    return clefs if len(clefs) == count else [None] * count


def parse_voices(given):
    """`--voice` values -> (one voice for every part or None, {part name: voice}). `LYSE` is
    every part; `Solo=REMY` one part (#483). A name may hold '='; the last one splits."""
    every, named = None, {}
    for value in given or []:
        name, eq, voice = value.rpartition("=")
        if not eq:
            name, voice = None, value
        voice = voice.strip().upper()
        if voice not in VOICES:
            raise argparse.ArgumentTypeError(f"--voice {value!r}: the voice is LYSE or REMY")
        if name is None:
            if every not in (None, voice):
                raise argparse.ArgumentTypeError(
                    f"--voice gives every part both {every} and {voice}; give one, and NAME=VOICE for the rest")
            every = voice
        elif not name.strip():
            raise argparse.ArgumentTypeError(f"--voice {value!r}: give the part's name before '='")
        else:
            named[name.strip()] = voice
    return every, named


def find_part(key, names):
    """The part number (from 1) `key` names in `names`: the one part with that exact name, else
    the one part with that name in any case, else its number. None when it names no part. A name
    that fits more than one part stops the run asking for the number (#483): two parts may both be
    called "Solo", and the first one is not necessarily the one meant."""
    exact = [i for i, name in enumerate(names, 1) if name == key]
    found = exact or [i for i, name in enumerate(names, 1) if name.casefold() == key.casefold()]
    if len(found) > 1:
        sys.exit(f"{key!r} names parts {', '.join(f'{i} {names[i - 1]!r}' for i in found)}; "
                 "give the part's number instead")
    if found:
        return found[0]
    if key.isdigit() and 1 <= int(key) <= len(names):
        return int(key)
    return None


def voices_for(parts, given, voicing, clefs, only=None):
    """The (voice, credit) of each manifest part: a `--voice NAME=` for that part first (NAME its
    name in any case, or its number), then part_voice's own order. A NAME no part has stops the
    run, so a typo is not sung past. With `only` (a part number), just that part's voice is worked
    out and the others are None, so a --try does not stop on another part's voice (#483)."""
    every, named = given
    names = [p["name"] for p in parts]
    chosen, unknown = {}, []
    for key, voice in named.items():
        i = find_part(key, names)
        if i is None:
            unknown.append(key)
        elif chosen.get(i, voice) != voice:
            sys.exit(f"--voice gives part {i} {names[i - 1]!r} both {chosen[i]} and {voice}")
        else:
            chosen[i] = voice
    if unknown:
        sys.exit(f"--voice names {', '.join(map(repr, unknown))}, which the song has no part called; "
                 f"its parts are {', '.join(f'{i} {n}' for i, n in enumerate(names, 1))}")
    return [part_voice(n, chosen.get(i) or every, voicing, clef) if only in (None, i) else None
            for i, (n, clef) in enumerate(zip(names, clefs), 1)]


def song_voicing(slug):
    """The song's voicing ("men", "women", "mixed") as its .song.json records it, or None."""
    try:
        with open(os.path.join(ROOT, "songs", slug, ".song.json"), encoding="utf-8") as f:
            return json.load(f).get("voicing")
    except (OSError, ValueError, AttributeError):
        return None


def build_bundle(song_app, slug, out, folded=None):
    """The song's bundle in `out`, built by song-app's publish code; returns (manifest, cleaned,
    groups). With `folded` (a path), the bundle is built from the cleaned score with its lettered
    divisi lines folded into their parts (fold.py, #104), written there; `groups` lists, per part,
    the cleaned score's staves it was made from (else None)."""
    sys.path.insert(0, song_app)
    cwd = os.getcwd()
    os.chdir(song_app)
    try:
        from dotenv import load_dotenv
        load_dotenv(".env")
        from src.song_app import pipeline, publish, state
        song = state.load(slug)
        cleaned = song.cleaned_path()
        if not os.path.exists(cleaned):
            cleaned = unicodedata.normalize("NFC", cleaned)   # .song.json may spell it NFD
        cleaned = os.path.abspath(cleaned)
        groups = None
        if folded:
            # a part publishing leaves out of the folded score (a click staff of rests) is left
            # out here too; the folded score numbers its staves 1, 2, ... in the groups' order
            groups = [ids for _, ids in fold(cleaned, folded)]
            sung = set(sung_staves(folded))
            groups = [ids for k, ids in enumerate(groups, 1) if str(k) in sung]
        settings = song.data.get("publish") or {}
        record = song.data.get("record") or {}
        bpm = None if pipeline.has_opening_tempo(cleaned) else \
            settings.get("bpm") or record.get("bpm") or publish.DEFAULT_BPM
        tempo_changes = settings.get("tempo_changes") or record.get("tempo_changes") or None
        with tempfile.TemporaryDirectory() as tmp:
            # as the app's own publish does (server._run_publish): the part names the Publish
            # panel gave (#402) and the staff pairing it saved
            bundle = publish.build_bundle(folded or cleaned, tmp, initial_bpm=bpm, tempo_changes=tempo_changes,
                                          part_names=folded_names(settings.get("part_names"), groups),
                                          staff_groups=None if folded else settings.get("staff_groups"),
                                          gradual=settings.get("gradual"), log=log)
            shutil.copy(bundle.musicxml, os.path.join(out, "score.musicxml"))
            shutil.copy(os.path.join(tmp, "score.mid"), os.path.join(out, "score.mid"))
            with open(os.path.join(out, "timing.json"), "w") as f:
                json.dump(bundle.timing, f)
            parts = [{"name": name, **getattr(bundle, "layout", {}).get(name, {})} for name in bundle.parts]
        # the Publish panel's credits, else the score's own, as the app's publish takes them
        credits = settings.get("credits")
        if credits is None:
            credits = publish.score_credits(folded or cleaned)
        credits = {k: v for k, v in credits.items() if v}
        manifest = {"slug": slug, "title": song.name, "parts": parts,
                    "duration": bundle.timing["duration"], **credits}
    finally:
        os.chdir(cwd)
    return manifest, cleaned, groups


def folded_names(names, groups):
    """The Publish panel's part names, one per staff of the cleaned score, cut down to the parts
    left after folding: each keeps the name of its own staff. A list of another length is passed
    on as it is, for song-app to refuse."""
    if names is None or groups is None:
        return names
    staves = sorted((i for ids in groups for i in ids), key=int)
    if len(names) != len(staves):
        return names
    return [names[staves.index(ids[0])] for ids in groups]


def run(cmd, **kw):
    """A step that must succeed; its own message when it does not."""
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        sys.exit(f"failed: {' '.join(map(str, cmd[:4]))} ...\n{(r.stdout + r.stderr).strip()[-2000:]}")
    return r


def sung_events(notes):
    """What a line sings, note by note: pitch, start, length and words (rests included)."""
    out, t = [], 0.0
    for n in notes:
        out.append((n.get("pitch"), round(t, 4), round(n["sec"], 4), n.get("syl"), bool(n.get("melisma"))))
        t += n["sec"]
    return out


def sung_words(lines):
    """[[seconds, MIDI key, syllable]] for each note the part's lines sing, the syllable as sung
    there: its own, or the one it holds or sings again (sing_ufr.py). The site sings a note
    several parts start together once only where these match (eeron-omat-taskit#110: the
    tenor's "kis" on the bass's "dum" was left out). A note both lines sing is listed once; one
    they sing on different syllables is left out, so the site never merges it: both lines are in
    the part's one MP3. Read before split_spoken(): a sentence spoken on one held chord is one
    note on the site, merged or not as a whole, so it is compared as the whole sentence ("Laulu‿
    reiän" against "Laulu‿rei'än" differ), not by its first syllable alone."""
    out = {}
    for notes in lines:
        t, last = 0.0, None
        for n in notes:
            if n.get("syl"):
                last = sung_key(n["syl"])
            if n.get("pitch") is not None and last:
                out.setdefault((round(t, 3), n["pitch"]), set()).add(last)
            t += n["sec"]
    return [[t, key, next(iter(syls))] for (t, key), syls in sorted(out.items()) if len(syls) == 1]


def write_words(path, parts):
    """The bundle's words.json: sung_words() of each part, in the manifest's order."""
    with open(path, "w") as f:
        json.dump({"version": 1, "parts": parts}, f, ensure_ascii=False, separators=(",", ":"))


def lines_differ(high, low):
    """Does the part split here? Two lines with the same pitches can still differ in rhythm or
    words; run2.py compared pitches only, which would leave such a lower line unsung."""
    return sung_events(high) != sung_events(low)


def words(notes):
    """[(word, [note indexes])] in the order sung: a word's syllables run to one whose syllabic
    is single or end."""
    out, cur = [], []
    for k, n in enumerate(notes):
        if not n.get("syl"):
            continue
        cur.append(k)
        if n.get("syllabic") in (None, "single", "end"):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [(plain("".join(notes[k]["syl"] for k in idx)), idx) for idx in out]


def sung_key(text):
    """A syllable as words.json compares it: lower case, letters and digits. Digits stay: a
    number is spoken as words before singing (spoken.py), so "Laulu‿118" and "Laulu‿119" differ."""
    return "".join(c for c in unicodedata.normalize("NFC", text.lower()) if c.isalnum())


def plain(text):
    """A word as --lang-from names it: lower case, letters only."""
    return "".join(c for c in unicodedata.normalize("NFC", text.lower()) if c.isalpha())


def parse_lang_from(specs):
    """--lang-from fi:Seimessä,hälle -> {"seimessä": "fi", "hälle": "fi"}."""
    out = {}
    for spec in specs or []:
        code, _, ws = spec.partition(":")
        if code not in G2P | {"fi"} or not ws.strip():
            sys.exit(f"--lang-from {spec!r}: give LANG:WORD[,WORD...], LANG one of fi, {', '.join(sorted(G2P))}")
        for w in ws.split(","):
            if plain(w):
                out[plain(w)] = code
    return out


def mark_languages(notes, lang, switches):
    """Each sung syllable's "lang": the song's own, until a word --lang-from names; from there on
    that word's language, until the next such word (#67: Psallite's Finnish verse between its
    Latin refrains). Each line starts in the song's own language."""
    cur = lang
    for word, idx in words(notes):
        cur = switches.get(word, cur)
        for k in idx:
            notes[k]["lang"] = cur
    return notes


def unmarked_finnish(notes, lang, switches):
    """[(bar, word)] sung in a language other than Finnish that look Finnish (FINNISH_LOOK). A
    word --lang-from names in the language it is sung in is right as it is."""
    out = []
    for word, idx in words(notes):
        code = notes[idx[0]].get("lang", lang)
        look = FINNISH_LOOK.get(code)
        if look and re.search(look, word) and switches.get(word) != code:
            out.append((notes[idx[0]].get("bar"), word))
    return out


def singer_env(python, ufr_dir, lang, gpu):
    """The environment sing_ufr.py runs in: the song's language, the voice files, and on a
    graphics card the CUDA libraries scripts/install-singer.sh put in the venv."""
    env = dict(os.environ, SONG_LANG="fi" if lang in G2P else lang, UFR_DIR=ufr_dir)
    if gpu:
        nv = nvidia_dir(python)
        libs = sorted(os.path.join(nv, d, "lib") for d in os.listdir(nv) if os.path.isdir(os.path.join(nv, d, "lib")))
        env.update(GPU_ENV, LD_LIBRARY_PATH=":".join(libs))
    return env


def nvidia_dir(python):
    """The venv's CUDA libraries (nvidia-*-cu11 wheels); None in a venv made for the processor."""
    lib = os.path.join(os.path.dirname(os.path.dirname(python)), "lib")
    for py in sorted(os.listdir(lib)) if os.path.isdir(lib) else []:
        nv = os.path.join(lib, py, "site-packages", "nvidia")
        if os.path.isdir(nv):
            return nv
    return None


def sung_staves(cleaned):
    """The staves the bundle's parts come from, in order: publishing leaves out a part with
    nothing to sing (a click staff of rests, percussion), so the same parts are left out here."""
    from src.score_export import score
    root = etree.parse(cleaned).getroot()
    silent = set(score.silent_parts(root))
    parts = root.find("Score").findall("Part")
    return [i for k, part in enumerate(parts) if score._part_name(part, k) not in silent
            for i in score._staff_ids(part)]


def sing(manifest, cleaned, bundle_dir, work, voices, lang, python, ufr_dir, gpu, lang_from=None,
         groups=None, resume=False, allow_silent=False, on_part=None, version=None, remote_only=False,
         pronounce=None, trial=None):
    """parts/<n>-<Name>.mp3 for every part, sung on the bundle's clock:

    - each line is checked against the piano before it is sung (check_onsets.py); a part whose
      notes do not start where score.mid's do stops the run, so it is never published;
    - where a part splits into two lines, both are sung and mixed at half volume each; a part
      folded from several staves (`groups`, --fold-divisi) sings each different line of each of
      them, mixed at 1/n volume each;
    - songs not in Finnish get their sounds from g2p.py (an English word's reading as the song
      page or the choir chose it, pronounce_env()), and a song in two languages changes at
      the words --lang-from names; every part is checked for Finnish-looking words left in
      another language before any part is sung;
    - a note whose syllable would be sung as no sound at all (only stops and silence, #449) stops
      the run before any line is sung, listing each one, unless allow_silent;
    - the rests where every line of the part rests are faded to silence (gate.py: the voice
      leaves a hiss that adds up over many waiting parts);
    - with resume, a line a stopped run already sang from the same notes for the same voice is
      not sung again (sung_before()), unless it was sung by another singer version;
    - with remote_only, every line is sung on REMOTE_RUNNER and a line it fails stops the run;
    - on_part(i, n, name) is told each part as one of its lines starts being sung (progress.py);
    - with trial (part number, first bar, last bar, mp3), only that part's first pass through
      those bars is sung, whole words (try_window()), into that mp3 and nowhere else: no
      parts/, no words.json (#483). Returns (seconds of music tried, seconds it took to sing)."""
    staves = sung_staves(cleaned)
    names = [p["name"] for p in manifest["parts"]]
    if groups:
        staves = [g[0] for g in groups]
    elif len(staves) != len(names):
        sys.exit(f"the score has {len(staves)} staves for {len(names)} parts")
    groups = groups or [[s] for s in staves]
    if len(groups) != len(names):
        sys.exit(f"{len(groups)} folded parts for {len(names)} parts in the bundle")
    timing = os.path.join(bundle_dir, "timing.json")
    with open(timing) as f:
        played = json.load(f).get("measures")
    bars = len(etree.parse(cleaned).getroot().find("Score").find(f"Staff[@id='{staves[0]}']").findall("Measure"))
    if played != bars:
        sys.exit(f"timing.json has {played} bars, the score {bars}")
    midi = os.path.join(bundle_dir, "score.mid")
    env = singer_env(python, ufr_dir, lang, gpu)
    os.makedirs(os.path.join(bundle_dir, "parts"), exist_ok=True)
    switches = lang_from or {}
    if switches and lang not in G2P | {"fi"}:
        sys.exit(f"--lang-from needs --lang fi or one of {', '.join(sorted(G2P))}, not {lang}")
    plan, unmarked, words, music, unchecked = [], [], [], None, []
    for i, (group, name) in enumerate(zip(groups, names), 1):
        if trial and i != trial[0]:
            continue
        lines = {}
        for k, staff in enumerate(group):
            for line in ("high", "low"):
                key = line if len(group) == 1 else f"{k + 1}{line}"
                js = os.path.join(work, f"p{i}-{key}.json")
                run([sys.executable, "-I", os.path.join(HERE, "unroll.py"), cleaned, staff, timing, js],
                    env=dict(os.environ, LINE=line))
                # before the spoken lines are cut: the piano has one note there, not one per syllable.
                # A trial checks only its own phrase, once it is known (below).
                if trial:
                    unchecked.append(js)
                else:
                    run([sys.executable, "-I", os.path.join(HERE, "check_onsets.py"), js, midi, str(i - 1), timing])
                with open(js) as f:
                    notes = json.load(f)
                # a line that sings what another of the part's lines sings is sung once, and a
                # line that never sings (only rests) is not sung at all: a folded part's own staff
                # may rest throughout while its lettered line sings
                if all(n.get("pitch") is None for n in notes):
                    continue
                if not any(not lines_differ(notes, other) for other in lines.values()):
                    lines[key] = notes
        if not lines:
            sys.exit(f"{name} has no notes to sing")
        if trial:
            window = try_window(lines.values(), trial[1], trial[2])
            if window is None:
                sys.exit(f"{name} sings no words in bars {trial[1]}-{trial[2]}")
            for js in unchecked:
                run([sys.executable, "-I", os.path.join(HERE, "check_onsets.py"), js, midi, str(i - 1), timing,
                     f"{window[0]:.4f}", f"{window[1]:.4f}"])
            lines = {k: cut(v, *window) for k, v in lines.items()}
            music = window[1] - window[0]
            log(f"trying {name}, bars {trial[1]}-{trial[2]}: {window[0]:.2f}-{window[1]:.2f} s")
        words.append(sung_words(lines.values()))
        two = len(lines) > 1
        for line in lines:
            notes = mark_languages(split_spoken(lines[line]), lang, switches)
            if not any(n.get("syl") for n in notes):
                sys.exit(f"{name} has no lyrics to sing")
            length = sum(n["sec"] for n in notes)
            if not trial and abs(length - manifest["duration"]) > 0.05:
                sys.exit(f"{name} lasts {length:.2f} s, the song {manifest['duration']} s")
            unmarked += [(name, bar, word) for bar, word in unmarked_finnish(notes, lang, switches)]
            plan.append((i, name, two, line, notes))
    if unmarked:
        sys.exit("words that look Finnish are sung in another language. Name the first Finnish word "
                 "of each stretch with --lang-from fi:WORD,... and the word where the song's own "
                 f"language comes back with --lang-from {lang}:WORD. A word below that is right as it "
                 "is: name it with the language it is sung in.\n"
                 + "\n".join(f"  {name}, bar {bar}: {word}" for name, bar, word in dict.fromkeys(unmarked)))
    if not trial:
        write_words(os.path.join(bundle_dir, "words.json"), words)
    jobs, quiet = [], []
    for i, name, two, line, notes in plan:
        js = os.path.join(work, f"p{i}-{line}.json")
        with open(js, "w") as f:
            json.dump(notes, f, ensure_ascii=False)
        for code in sorted({n["lang"] for n in notes if n.get("syl")} & G2P):
            run([python, "-I", os.path.join(HERE, "g2p.py"), code, js],
                env=dict(os.environ, UFR_DIR=ufr_dir, **(pronounce or {})))
        quiet += [(name, n["bar"], n["syl"]) for n in silent_notes(python, js, voices[i - 1], env)]
        said = f"{name}{' ' + line + ' line' if two else ''}"
        if resume and sung_before(work, i, line, voices[i - 1], version):
            log(f"{said}: sung before, kept")
            continue
        forget_sung(work, i, line)
        jobs.append((i, said, line, len(notes)))

    if quiet:
        listed = "\n".join(f"  {name}, bar {bar}: {syl}" for name, bar, syl in quiet)
        if not allow_silent:
            sys.exit(f"{len(quiet)} notes would be sung as no sound at all (only stops and silence). "
                     "Change their words, or sing them anyway with --allow-silent:\n" + listed)
        log(f"{len(quiet)} notes are sung as no sound at all (--allow-silent):\n{listed}")

    def started(i):
        if on_part:
            on_part(i, len(names), names[i - 1])

    def here(job):
        i, said, line, count = job
        log(f"singing {said} ({count} notes)")
        started(i)
        run(["nice", "-n", "19", python, "-I", SINGER, os.path.join(work, f"p{i}-{line}.json"),
             os.path.join(work, f"p{i}-{line}.wav"), "100", voices[i - 1]], env=env)
        mark_sung(work, i, line, voices[i - 1], version)

    def there(job):
        i, said, line, count = job
        log(f"singing {said} ({count} notes) on the remote runner")
        started(i)
        sing_remotely(runner, os.path.join(work, f"p{i}-{line}.json"), os.path.join(work, f"p{i}-{line}.wav"),
                      voices[i - 1], env)
        mark_sung(work, i, line, voices[i - 1], version)

    runner = (os.environ.get("REMOTE_RUNNER") or "").strip()
    began = time.monotonic()
    sing_lines(jobs, here, there, runner, remote_only)
    took = time.monotonic() - began
    for i, name in enumerate(names, 1):
        lines = [p[3] for p in plan if p[0] == i]
        if not lines:
            continue
        mp3 = trial[3] + ".part.mp3" if trial else os.path.join(bundle_dir, sung_file(i, name))
        run(part_mp3_command([os.path.join(work, f"p{i}-{line}.wav") for line in lines], mp3))
        r = run([python, "-I", os.path.join(HERE, "gate.py"), mp3]
                + [os.path.join(work, f"p{i}-{line}.json") for line in lines])
        log(r.stdout.strip())
        if trial:
            os.replace(mp3, trial[3])   # a listener never finds half a file
    return (music, took) if trial else None


def opens_word(n):
    """A sung note that starts a syllable of its own and not the middle or end of a word."""
    return n.get("pitch") is not None and bool(n.get("syl")) and n.get("syllabic") not in ("middle", "end")


def ends_word(n):
    return n.get("pitch") is not None and bool(n.get("syl")) and n.get("syllabic") not in ("begin", "middle")


def try_window(lines, first, last):
    """(start, end) in seconds of the first pass through bars first..last over the part's lines,
    widened to whole words: back to the note a word in it starts on, on past `last` until the
    word sung there and the notes held on its syllable end. None when no word is sung there."""
    window = None
    for notes in lines:
        times, t = [], 0.0
        for n in notes:
            times.append((t, t + n["sec"]))
            t += n["sec"]
        sung = [k for k, n in enumerate(notes) if n.get("pitch") is not None]
        inside = []
        for k in sung:
            if inside and notes[k]["bar"] < notes[inside[-1]]["bar"]:
                break               # a repeat jumped back: its second pass is not this one
            if first <= notes[k]["bar"] <= last:
                inside.append(k)
            elif inside:
                break               # left the bars
        if not inside or not any(notes[k].get("syl") for k in inside):
            continue
        at = sung.index(inside[0])
        while at > 0 and not opens_word(notes[sung[at]]):
            at -= 1
        start = sung[at]
        at = sung.index(inside[-1])
        while at + 1 < len(sung):
            here, nxt = notes[sung[at]], notes[sung[at + 1]]
            if nxt.get("syl") and (opens_word(nxt) or ends_word(here)):
                break               # the next note starts a word of its own
            at += 1
        end = sung[at]
        span = (times[start][0], times[end][1])
        window = span if window is None else (min(window[0], span[0]), max(window[1], span[1]))
    return window


def cut(notes, start, end):
    """The notes sounding between start and end seconds; a note reaching over either edge is
    silence for the part of it inside, so every line of a part starts at the same moment."""
    out, t = [], 0.0
    for n in notes:
        a, b = t, t + n["sec"]
        t = b
        inside = min(b, end) - max(a, start)
        if inside <= 1e-6:
            continue
        if a >= start - 1e-6 and b <= end + 1e-6:
            out.append(n)
        else:
            out.append({"pitch": None, "beats": 0, "syl": None, "sec": inside})
    return out



def sing_lines(jobs, here, there, runner, remote_only=False):
    """Sing every job: here and on the runner at once (share()), or with remote_only on the
    runner alone, where a line it cannot sing stops the run rather than being sung here."""
    if not remote_only:
        return share(jobs, here, there if runner else None, stop_remote)
    if not runner:
        sys.exit("--remote-only needs REMOTE_RUNNER in .env")

    def only_there(job):
        try:
            there(job)
        except Exception as e:
            sys.exit(f"the remote runner did not sing {job[1]}: {e}")
    return share(jobs, only_there, None, stop_remote)


def silent_notes(python, js, voice, env):
    """[{bar, syl}] of the line's notes the singer would sing as no sound at all: its own planned
    sounds (SILENT in sing_ufr.py), so nothing is sung to find them."""
    with tempfile.TemporaryDirectory() as tmp:
        found = os.path.join(tmp, "silent.json")
        run([python, "-I", SINGER, js, os.path.join(tmp, "unused.wav"), "100", voice],
            env=dict(env, SILENT=found))
        with open(found) as f:
            return json.load(f)


def sung_record(work, i, line):
    return os.path.join(work, f"p{i}-{line}.sung.json")


def mark_sung(work, i, line, voice, version=None):
    """Note that p<i>-<line>.wav is finished, with the notes, voice and singer version it was
    sung from."""
    with open(os.path.join(work, f"p{i}-{line}.json")) as f:
        notes = json.load(f)
    with open(sung_record(work, i, line), "w") as f:
        json.dump({"voice": voice, "notes": notes, "version": version}, f, ensure_ascii=False)


def forget_sung(work, i, line):
    """Before a line is sung: its WAV is about to change, so an earlier record no longer holds."""
    try:
        os.remove(sung_record(work, i, line))
    except FileNotFoundError:
        pass


def sung_before(work, i, line, voice, version=None):
    """True when p<i>-<line>.wav was finished from exactly the notes p<i>-<line>.json has now, for
    this voice, by this singer version (a line sung before the singer changed is sung again, #453).
    Other changes to the singer are not seen: --resume is for picking up a stopped run."""
    wav = os.path.join(work, f"p{i}-{line}.wav")
    try:
        with open(sung_record(work, i, line)) as f:
            done = json.load(f)
        with open(os.path.join(work, f"p{i}-{line}.json")) as f:
            notes = json.load(f)
    except (FileNotFoundError, ValueError):
        return False
    return os.path.exists(wav) and done == {"voice": voice, "notes": notes, "version": version}


def part_mp3_command(wavs, mp3):
    """ffmpeg: one line, or n mixed at 1/n volume each (two at half), less the lead-in, as a 64k
    mono MP3. The filter's output is named and mapped, so the mix is what is written, never one input."""
    ins = sum((["-i", w] for w in wavs), [])
    n = len(wavs)
    mix = "".join(f"[{k}:a]" for k in range(n)) + f"amix=inputs={n}:normalize=0,volume={1 / n:g}," \
        if n > 1 else "[0:a]"
    return ["ffmpeg", "-nostdin", "-v", "error", "-y", *ins, "-filter_complex",
            f"{mix}atrim=start_sample={LEAD_IN_SAMPLES}[part]", "-map", "[part]",
            "-ac", "1", "-b:a", "64k", mp3]


def site_client():
    """The Cloudflare client publishing uses, made once per run; stops the run without the keys."""
    from src.song_app import publish
    config = publish.Config.from_env()
    if config is None:
        sys.exit(publish.MISSING_CONFIG)
    return publish.Cloudflare(config)


def sung_choirs(client, slug, choirs):
    """The choirs among `choirs` whose live version of the song on the site is sung (#483).
    A choir that cannot be read stops the run: publishing blind could replace a sung version."""
    from src.song_app import publish
    sung = []
    for choir in choirs:
        try:
            if publish.sung_on_site(client, choir, slug):
                sung.append(choir)
        except Exception as e:   # a D1 error, a timeout, a refused key
            sys.exit(f"could not check {choir} on the site before publishing: {e}")
    return sung


def publish_to_site(slug, choir, bundle_dir, manifest, sung_by, places, cleaned, version=None,
                    client=None, piano_over_sung=False):
    """Send the bundle (with the sung MP3s when there are) to the site's `choir` list with the
    app's own publish code, and keep the record on the song as the app's Publish does, so the
    next publish from either takes this version away. Review's approval is not needed here.
    A sung publish records the singer `version` it was sung with (#453).

    A piano-only bundle raises ``publish.SungOnSite`` rather than replace a sung version on
    the site, unless `piano_over_sung` (#483); any other failure is ``publish.PublishError``."""
    from src.song_app import publish, state
    if client is None:
        client = site_client()
    song = state.load(slug)
    settings = song.data.get("publish") or {}
    sung = {}
    if manifest.get("sung"):
        for i, (part, (_, credit)) in enumerate(zip(manifest["parts"], sung_by), 1):
            sung[part["name"]] = {"mp3": os.path.join(bundle_dir, sung_file(i, part["name"])),
                                  "voice": credit}
    bundle = publish.Bundle(
        musicxml=os.path.join(bundle_dir, "score.musicxml"), midi=os.path.join(bundle_dir, "score.mid"),
        timing=json.load(open(os.path.join(bundle_dir, "timing.json"))),
        parts=[p["name"] for p in manifest["parts"]],
        layout={p["name"]: {k: p[k] for k in ("staff", "voices") if k in p} for p in manifest["parts"]
                if "staff" in p}, sung=sung,
        words=os.path.join(bundle_dir, "words.json") if sung and os.path.exists(os.path.join(bundle_dir, "words.json")) else "")
    choirs = publish.site_choirs(client)
    credits = {k: manifest[k] for k in publish.CREDITS if manifest.get(k)}

    def records_now():
        with state.song_lock(slug):
            return publish.records(state.load(slug).data.get("publish") or {})

    record = publish.publish(slug=slug, title=manifest["title"], choir=choir, bundle=bundle,
                             client=client, credits=credits, published=publish.records(settings),
                             choirs=choirs, places=(settings.get("places") or []) + places, log=log,
                             piano_over_sung=piano_over_sung, records_now=records_now)
    if not record.pop("live", True):
        log(f"{choir}: another publish replaced this one before it was recorded; its record was not saved")
        return
    record["published_against"] = state.file_fingerprint(cleaned)
    record["sung"] = bool(sung)
    if sung:
        record["singer_version"] = version
    with state.song_lock(slug):
        song = state.load(slug)
        pub = song.data.setdefault("publish", {})
        sites = publish.records(pub)
        staging = [c["id"] for c in choirs if c.get("staging")]
        for gone in publish.replaced(choir, sites, staging):
            del sites[gone]
        sites[choir] = record
        pub.pop("site", None)
        pub.update({"sites": sites, "choir": choir, "error": None})
        song.save()


def parse_places(specs):
    """--place "Ääni 1=-" -> [{"name": "Ääni 1", "id": "-"}]; "NAME=^PARENT" makes a new place."""
    out = []
    for spec in specs or []:
        name, sep, where = spec.partition("=")
        if not sep or not name.strip() or not where.strip():
            sys.exit(f"--place {spec!r}: give NAME=ID, NAME=- (no place) or NAME=^PARENT (a new place)")
        where = where.strip()
        out.append({"name": name.strip(), **({"parent": where[1:]} if where.startswith("^") else {"id": where})})
    return out


BUNDLE = ("score.musicxml", "score.mid", "timing.json", "manifest.json")   # what a run writes into --out


def parse_trial(text):
    """--try PART:A-B -> (part, first bar, last bar); the part stays a name or a number."""
    part, colon, bars = text.rpartition(":")
    first, dash, last = bars.partition("-")
    try:
        first, last = int(first), int(last if dash else first)
    except ValueError:
        part = ""
    if not colon or not part or first < 1 or last < first:
        raise argparse.ArgumentTypeError(f"--try {text!r}: give PART:A-B, e.g. Solo:12-16 or 2:5-8")
    return part, first, last


def trial_part(trial, names):
    """(part number, first, last) for a --try: the part by its name (any case) or its number."""
    part, first, last = trial
    index = find_part(part, names)
    if index is None:
        sys.exit(f"--try: no part {part!r}; the parts are " + ", ".join(f"{i} {n}" for i, n in enumerate(names, 1)))
    return index, first, last


def folded_path(out, slug, trial=None):
    """Where --fold-divisi writes the folded score: under <out>/try/ for a trial, which writes
    nothing else, so a full run's folded score stays as it was (#483)."""
    return os.path.join(out, "try", f"{slug}_folded.mscx") if trial else os.path.join(out, f"{slug}_folded.mscx")


def trial_said(mp3, music, took):
    """A trial's last line: the music's length against the time singing it took, which is how
    fast this machine (or the remote runner) sings (#483)."""
    return f"Trial: {mp3} ({music:.1f} s of music, sung in {took:.1f} s)"


def clear_bundle(out):
    """Take away what an earlier run wrote into `out` (the bundle and parts/), and nothing else:
    work/ stays for --resume, and --out may be a folder with other things in it."""
    for name in BUNDLE:
        if os.path.isfile(os.path.join(out, name)):
            os.remove(os.path.join(out, name))
    parts = os.path.join(out, "parts")
    if os.path.isdir(parts):
        for name in os.listdir(parts):
            if name.endswith(".mp3"):
                os.remove(os.path.join(parts, name))


def pronounce_env(slug, choirs):
    """What g2p.py reads to choose English readings (eeron-omat-taskit#144): SONG_PRONOUNCE, the
    song page's choices (songs/<slug>/pronounce.json, src/sing_song/places.py), and CHOIR_WORDS,
    the choirs/<choir>.json of each choir the song is published to. One run sings once for all
    its choirs, so they must give every word alike."""
    for choir in choirs:
        if not choir or choir in (".", "..") or choir.startswith(".") or "/" in choir or "\\" in choir:
            sys.exit(f"{choir!r} is not a choir: give its id on the site")
    paths = [os.path.join(ROOT, "choirs", f"{c}.json") for c in choirs]
    maps = {c: choir_words(p) if os.path.isfile(p) else {} for c, p in zip(choirs, paths)}
    if len({json.dumps(m, sort_keys=True) for m in maps.values()}) > 1:
        sys.exit(f"choirs {', '.join(choirs)} spell different words in choirs/<choir>.json: "
                 "publish to each in its own run")
    return {"SONG_PRONOUNCE": os.path.join(ROOT, "songs", slug, song_places.FILE),
            "CHOIR_WORDS": os.pathsep.join(p for p in paths if os.path.isfile(p))}


def choir_words(path):
    """A choir file's word -> sound-alike spelling map, as g2p.py reads it."""
    with open(path) as f:
        d = json.load(f)
    return d["pronounce"] if isinstance(d.get("pronounce"), dict) else d


def check_slug(slug):
    """A song's folder name under songs/, not a path."""
    if not slug or slug in (".", "..") or slug.startswith(".") or "/" in slug or "\\" in slug:
        sys.exit(f"{slug!r} is not a song: give its folder name under songs/")


# REMOTE_RUNNER (.env): another machine may sing lines while this one sings others. Run as
# `<runner> sing-line <line.json> <out.wav> <voice>` with the singer's settings in the
# environment; exit 0 with the WAV written means sung, anything else sings the line here.
REMOTE_LINE_LIMIT = 1800   # a first line may wait for the runner to set itself up
REMOTE_STOP_S = 30         # how long a stopped runner may take to end its job over there
# what the runner is doing right now: stop_remote() kills it, so a cancelled run leaves
# nothing running over there (#86 review of #90 in eeron-omat-taskit)
REMOTE, REMOTE_LOCK, STOPPING = set(), threading.Lock(), threading.Event()


def sing_remotely(runner, js, wav, voice, env):
    """One line on the remote runner; raises when it did not sing it, with the reason."""
    if os.path.exists(wav):   # a WAV an earlier run left must not pass for this line's
        os.remove(wav)
    with REMOTE_LOCK:
        if STOPPING.is_set():
            raise RuntimeError("the run is stopping")
        p = subprocess.Popen(shlex.split(runner) + ["sing-line", js, wav, voice], env=env, text=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             start_new_session=True)
        REMOTE.add(p)
    try:
        out, _ = p.communicate(timeout=REMOTE_LINE_LIMIT)
    except subprocess.TimeoutExpired:
        end_runner(p)
        p.communicate()
        raise RuntimeError(f"no answer in {REMOTE_LINE_LIMIT} s") from None
    finally:
        with REMOTE_LOCK:
            REMOTE.discard(p)
    if p.returncode or not os.path.exists(wav):
        last = (out or "").strip().splitlines()
        raise RuntimeError(last[-1] if last else f"exit {p.returncode}")


def end_runner(p):
    """Ask the runner to stop (SIGTERM: it ends its job over there, which runs detached, and
    lets the card go), and kill it only if it has not within REMOTE_STOP_S."""
    for sig, wait in ((signal.SIGTERM, REMOTE_STOP_S), (signal.SIGKILL, None)):
        try:
            os.killpg(p.pid, sig)
        except ProcessLookupError:
            return
        try:
            p.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def stop_remote():
    """No more remote lines, and end the one running (all its processes)."""
    with REMOTE_LOCK:
        STOPPING.set()
        running = list(REMOTE)
    for p in running:
        end_runner(p)


def share(jobs, here, there=None, stop=None):
    """Run here(job) for every job, and with `there`, there(job) for some of them at the same
    time: each takes the next job when it is free, so the faster machine sings more. When a
    job fails there, it is sung here instead and `there` takes no more. A failure here stops
    the run. Returns {job: "here" or "there"}. When this side fails or is cancelled, stop()
    ends what `there` is doing before it is waited for, so nothing outlives the run."""
    todo, done, failed = queue.Queue(), {}, []
    for job in jobs:
        todo.put(job)

    def take(sing, where):
        while not failed and not STOPPING.is_set():
            try:
                job = todo.get_nowait()
            except queue.Empty:
                return
            try:
                sing(job)
                done[job] = where
            except BaseException as e:   # run() stops with SystemExit
                if where == "here":
                    failed.append(e)
                    return
                log(f"the remote runner did not sing it ({e}): singing it here, and the rest here too")
                todo.put(job)
                return

    helper = threading.Thread(target=take, args=(there, "there")) if there else None
    if helper:
        helper.start()
    try:
        take(here, "here")
        if failed and stop:
            stop()
        if helper:
            helper.join()
    except BaseException:   # cancelled while waiting for the runner
        if stop:
            stop()
        if helper:
            helper.join()
        raise
    take(here, "here")   # what the runner gave back after this side had finished
    if failed:
        raise failed[0]
    return done


def stop_on_term(signum, frame):
    """A cancelled run (SIGTERM, as agentdeck job cancel sends) ends like an error."""
    sys.exit(128 + signum)


def main():
    signal.signal(signal.SIGTERM, stop_on_term)
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("slug")
    ap.add_argument("--publish", metavar="CHOIR", action="append", default=[],
                    help="send it to this choir's list on the site (needs the Cloudflare keys in .env); "
                         "repeat it for each choir the song is on")
    ap.add_argument("--waiting", action="store_true",
                    help="only mark the song as waiting for a run in the song app's list, and exit: "
                         "run it just before queueing the run")
    ap.add_argument("--no-sing", action="store_true", help="piano only")
    ap.add_argument("--piano-over-sung", action="store_true",
                    help="with --no-sing --publish: replace a sung version on the site with piano only; "
                         "without it such a choir is refused")
    ap.add_argument("--out", help="where the result goes (default /var/tmp/practice-<slug>)")
    ap.add_argument("--voice", action="append", metavar="[PART=]LYSE|REMY",
                    help="the voice for one part (Solo=REMY, repeatable), or for every part (LYSE), when "
                         "the part names do not say (S/A/M: LYSE, T/B: REMY); a part that says no voice "
                         "otherwise takes it from its clef (treble LYSE, treble-8 or bass REMY)")
    ap.add_argument("--cpu", action="store_true", help="sing on the processor even when the singer has a GPU")
    ap.add_argument("--remote-only", action="store_true",
                    help="sing every line on the remote runner (REMOTE_RUNNER: eero-pc's GPU on Eero's host), "
                         "none on this machine; a line it cannot sing stops the run")
    ap.add_argument("--lang", default="fi", help="the words' language: fi, or en, de, sv, la, et (g2p.py)")
    ap.add_argument("--lang-from", action="append", metavar="LANG:WORD[,WORD...]",
                    help="a song in two languages: from each named word on, sing in LANG (repeatable)")
    ap.add_argument("--place", action="append", default=[], metavar="NAME=ID",
                    help="where a part name the site's voice tree does not know goes: an id, - for no place, "
                         "or ^PARENT for a new place under one")
    ap.add_argument("--fold-divisi", action="store_true",
                    help="put lettered divisi lines (S1b, S1c, ...) into their parts as further voices, "
                         "every line still sung")
    ap.add_argument("--resume", action="store_true",
                    help="keep the lines an earlier run with the same --out already sang from the same notes "
                         "with the same singer version (after a stopped run)")
    ap.add_argument("--allow-silent", action="store_true",
                    help="sing notes whose syllable has no vowel and no held sound (only stops), which are "
                         "heard as silence; without it such notes stop the run before anything is sung")
    ap.add_argument("--try", dest="trial", metavar="PART:A-B",
                    help="sing one phrase to hear it: the part (its name or number) in bars A to B, its "
                         "first pass, whole words, into <out>/try/; nothing else is written or published")
    a = ap.parse_args()
    if a.trial:
        try:
            a.trial = parse_trial(a.trial)
        except argparse.ArgumentTypeError as e:
            ap.error(str(e))
        for flag, given in (("--publish", a.publish), ("--resume", a.resume), ("--no-sing", a.no_sing),
                            ("--waiting", a.waiting)):
            if given:
                ap.error(f"--try sings one phrase to listen to; it does not go with {flag}")
    if a.piano_over_sung and not (a.no_sing and a.publish):
        ap.error("--piano-over-sung goes with --no-sing and --publish")
    try:
        a.voices = parse_voices(a.voice)
    except argparse.ArgumentTypeError as e:
        ap.error(str(e))

    check_slug(a.slug)
    song_dir = os.path.join(ROOT, "songs", a.slug)   # the app's songs/ (state.song_dir)
    if a.waiting:
        if not os.path.isdir(song_dir):
            sys.exit(f"no song {a.slug!r} under songs/")
        progress.mark_waiting(song_dir, a.publish)
        log(f"{a.slug}: waiting")
        return
    # a phrase to listen to (--try) keeps no run record: the song list is not told of it
    run_record = progress.Run(song_dir, a.publish, progress.singer_version(), remote=a.remote_only,
                              sing=not a.no_sing, kept=not a.trial)
    run_record.step("bundle")
    try:
        sing_and_publish(a, run_record)
    except SystemExit as e:
        if run_record.finished:   # it already said why (a choir refused, #483)
            raise
        if e.code not in (None, 0):
            run_record.finish(failure(e.code))
        else:
            run_record.finish()
        raise
    except BaseException as e:
        run_record.finish(f"{type(e).__name__}: {e}")
        raise
    run_record.finish()


def failure(code):
    """What a run that stopped says about why: its message, or how it was stopped."""
    if isinstance(code, int):
        if code > 128 and code - 128 == signal.SIGTERM:
            return "cancelled"
        return f"stopped with exit status {code}"
    return str(code).strip()[-500:]


def sing_and_publish(a, run_record):
    # inside the run, so a song marked --waiting reads as failed with the reason, not waiting
    if a.remote_only and not a.no_sing and not (os.environ.get("REMOTE_RUNNER") or "").strip():
        sys.exit("--remote-only needs REMOTE_RUNNER in .env")
    out = os.path.abspath(a.out or f"/var/tmp/practice-{a.slug}")
    places = parse_places(a.place)
    venv = sing_venv()
    python = os.path.join(venv, "bin", "python")
    ufr_dir = os.environ.get("UFR_DIR", "")
    if not a.no_sing:
        if not os.path.exists(python):
            sys.exit(f"no singer at {venv}: run scripts/install-singer.sh")
        if ufr_dir and not is_ours(ufr_dir) and is_download(ufr_dir):
            sys.exit(f"UFR_DIR ({ufr_dir}) is the UFR pack as it downloads, for OpenUtau: run "
                     f"scripts/install-singer.sh {shlex.quote(ufr_dir)} to build the singer's folder from it")
        if not is_ours(ufr_dir):
            sys.exit("download the UFR DiffSinger pack from https://utaufrance.com/telechargement-du-pack-diffsinger-ufr/ "
                     "and run scripts/install-singer.sh <the .zip>; it sets UFR_DIR in .env")
        pronounce = pronounce_env(a.slug, list(dict.fromkeys(a.publish)))
    choirs = list(dict.fromkeys(a.publish))
    client = site_client() if choirs else None
    if choirs and a.no_sing and not a.piano_over_sung:
        # Before anything is built: a piano-only run would replace a sung version (#483).
        sung = sung_choirs(client, a.slug, choirs)
        if sung:
            # the record says what became of every choir asked for, as publish_choirs() does
            for choir in choirs:
                run_record.choir_outcome(choir, "refused-sung" if choir in sung else "not-tried")
            stop(run_record, f"{', '.join(sung)} {'has' if len(sung) == 1 else 'have'} a sung version on "
                 f"the site; this run would replace {'it' if len(sung) == 1 else 'them'} with piano only. "
                 "Run again with --piano-over-sung to do that, or leave "
                 f"{'that choir' if len(sung) == 1 else 'those choirs'} out of --publish.")
    bundle_dir = os.path.join(out, "try", "bundle") if a.trial else out
    os.makedirs(bundle_dir, exist_ok=True)
    clear_bundle(bundle_dir)   # a trial leaves an earlier full run's bundle and parts/ alone
    folded = folded_path(out, a.slug, a.trial) if a.fold_divisi else None
    manifest, cleaned, groups = build_bundle(ROOT, a.slug, bundle_dir, folded)
    log(f"bundle: {bundle_dir} ({manifest['duration']} s, parts {[p['name'] for p in manifest['parts']]})")
    sung_by = []
    if not a.no_sing:
        # a reading chosen at a place a score edit took away is said, not dropped in silence
        for c in song_places.stale(song_places.load(os.path.join(ROOT, "songs", a.slug)), cleaned):
            log(f"pronunciation: the reading chosen for {c.get('word')!r} at bar {c.get('bar')} "
                f"(time {c.get('n')} there) has no such word there any more; choose it again on the song page")
        voicing = song_voicing(a.slug)
        work = os.path.join(out, "try", "work") if a.trial else os.path.join(out, "work")
        os.makedirs(work, exist_ok=True)
        trial = None
        if a.trial:
            # the part first: a trial needs only its own part's voice (#483)
            index, first, last = trial_part(a.trial, [p["name"] for p in manifest["parts"]])
            name = manifest["parts"][index - 1]["name"]
            mp3 = os.path.join(out, "try", os.path.basename(sung_file(index, name))[:-len(".mp3")]
                               + f"-bars-{first}-{last}.mp3")
            trial = (index, first, last, mp3)
        sung_by = voices_for(manifest["parts"], a.voices, voicing,
                             part_clefs(cleaned, len(manifest["parts"])), only=trial and trial[0])
        gpu = not a.cpu and nvidia_dir(python) is not None
        log("singing every line on the remote runner" if a.remote_only
            else f"singing on the {'graphics card' if gpu else 'processor'}")
        tried = sing(manifest, cleaned, bundle_dir, work, [v and v[0] for v in sung_by], a.lang, python, ufr_dir, gpu,
             parse_lang_from(a.lang_from), groups, a.resume, a.allow_silent,
             on_part=lambda i, n, name: run_record.step("singing", part=i, parts=n, part_name=name),
             version=run_record.record["version"], remote_only=a.remote_only,
             pronounce=pronounce, trial=trial)
        if trial:
            log(trial_said(trial[3], *tried))
            return
        manifest["sung"] = True
        for part, (_, credit) in zip(manifest["parts"], sung_by):
            part["voice"] = credit
    with open(os.path.join(bundle_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, ensure_ascii=False)
    publish_choirs(a, run_record, choirs, client, bundle_dir, manifest, sung_by, places, cleaned)
    log(f"practice track: {bundle_dir}")


EXIT_REFUSED = 3   # a choir was not replaced: its sung version stays (#483)


def stop(run_record, message, code=EXIT_REFUSED):
    """End the run with `message` on stderr and in the run record, and exit with `code`."""
    print(message, file=sys.stderr, flush=True)
    run_record.finish(message)
    raise SystemExit(code)


def publish_choirs(a, run_record, choirs, client, bundle_dir, manifest, sung_by, places, cleaned):
    """Publish to each choir in turn. A choir whose sung version a piano-only run would replace
    is refused and the others still go (#483); any other failure stops here, and the run says
    which choirs it did not try and how to try them."""
    from src.song_app import publish
    for choir in choirs:
        run_record.choir_outcome(choir, "not-tried")
    refused, done = [], []
    for k, choir in enumerate(choirs, 1):
        run_record.step("publishing", choir=choir, choir_index=k, choir_count=len(choirs))
        try:
            publish_to_site(a.slug, choir, bundle_dir, manifest, sung_by, places, cleaned,
                            run_record.record["version"], client=client,
                            piano_over_sung=a.piano_over_sung)
        except publish.SungOnSite as e:
            log(f"{choir}: {e}")
            run_record.choir_outcome(choir, "refused-sung")
            refused.append(choir)
            continue
        except publish.PublishError as e:
            run_record.choir_outcome(choir, "failed")
            rest = choirs[k:]
            message = f"publish to {choir} failed: {e}"
            if rest:
                message += (f". Not tried: {', '.join(rest)}; publish them with "
                            + " ".join(f"--publish {c}" for c in [choir] + rest) + ".")
            sys.exit(message)
        run_record.choir_outcome(choir, "published")
        done.append(choir)
    if refused:
        message = (f"{', '.join(refused)} {'has' if len(refused) == 1 else 'have'} a sung version on the "
                   "site and " + ("was" if len(refused) == 1 else "were") + " not replaced.")
        if done:
            message += f" {', '.join(done)} {'was' if len(done) == 1 else 'were'} published."
        message += (" To replace " + ("it" if len(refused) == 1 else "them") + " with piano only, run "
                    "again with --no-sing --piano-over-sung " + " ".join(f"--publish {c}" for c in refused) + ".")
        stop(run_record, message)


if __name__ == "__main__":
    main()
