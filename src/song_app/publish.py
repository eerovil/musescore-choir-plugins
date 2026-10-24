"""Publish a song to the Cloudflare stemmanauhat site (#382, #400).

This is where a song ends: the site (eerovil/stemmanauhat-cf) draws the score
with OpenSheetMusicDisplay and plays its MIDI with a piano soundfont, so what it
needs from here is:

- the score as **MusicXML**;
- the score's **MIDI**, which the site plays;
- a **timing file** saying where in the score playback is at each moment;
- the **part names**, in score order, which the site matches to the MIDI tracks
  (as corrected on the Publish panel, #402: cleaning names parts by guessing
  from pitch);
- the **credits**: composer, lyricist and arranger, when they are known.

No audio is rendered here (stemmanauhat-cf#36), and nothing goes to YouTube any
more (#400). ``sing_song.py --publish`` sends each part's sung MP3 along too
(``Bundle.sung``, #439); the app's own Publish button stays piano only.

All of it is made off one prepared copy of the cleaned score
(`score_export.score.prepare`: silent parts dropped, red marks stripped, the
fermata hold, the opening tempo, the tempo changes and the part names set on
the Publish panel), so the parts in the MusicXML, the MIDI and the timing are the same parts
on the same clock. The clock is MuseScore's own: bar order from its ``.mpos``
export (which follows repeats and D.C./D.S. jumps the way playback does) and
seconds from its MIDI tempo map, which carries the fermata stretches. The two
are checked against each other before anything is uploaded.

The files go to the site's R2 bucket under a fresh version prefix, and only
then is the song's row in the site's D1 database pointed at them, so a reader
never sees half a song. Both go through Cloudflare's REST API with one API
token kept in this host's ``.env``. See ``docs/stemmanauhat-site.md`` for the
formats the site reads.
"""

from __future__ import annotations

import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from lxml import etree

Logger = Callable[[str], None]

TIMING_VERSION = 1
# How far MuseScore's .mpos bar starts may sit from the MIDI clock before the
# timing is refused. .mpos is written in whole milliseconds.
TIMING_TOLERANCE = 0.01
API = "https://api.cloudflare.com/client/v4"
TIMEOUT = 120
TRIES = 3

# What a score without an opening tempo plays at until someone sets one.
DEFAULT_BPM = 80

# Who wrote the song. The site's song table has a column for each (its migration
# 0004), and the manifest carries the ones that are set.
CREDITS = ("composer", "lyricist", "arranger")

UPSERT = ("INSERT INTO songs (choir, slug, title, prefix, parts, duration, published_at, "
          "composer, lyricist, arranger) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
          "ON CONFLICT (choir, slug) DO UPDATE SET "
          "title = excluded.title, prefix = excluded.prefix, parts = excluded.parts, "
          "duration = excluded.duration, published_at = excluded.published_at, "
          "composer = excluded.composer, lyricist = excluded.lyricist, "
          "arranger = excluded.arranger")


UNSAFE_IN_FILE = re.compile(r"[/\\?#%]")


def sung_file(index: int, name: str) -> str:
    """parts/<n>-<Name>.mp3, the sung MP3 of part `index` (from 1): a part name may hold a
    character a file name or a URL cannot (``S/A``), which becomes ``_``. The site reads the
    name off the manifest's ``file``, and sing_song writes its MP3 under the same one."""
    return f"parts/{index}-{UNSAFE_IN_FILE.sub('_', name)}.mp3"


def _noop(_msg: str) -> None:
    pass


class PublishError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# The timing file
# ---------------------------------------------------------------------------
def bar_lengths(mscx_path: str) -> List[Fraction]:
    """Each printed bar's length in quarter notes, read off the first staff.

    A bar's ``len`` (an irregular bar: a pickup, a cut ending) wins over the time
    signature in force, as it does in MuseScore.
    """
    root = etree.parse(mscx_path).getroot()
    staff = root.find(".//Score/Staff")
    if staff is None:
        raise PublishError("The score has no staves.")
    sig = Fraction(4)
    lengths = []
    for measure in staff.findall("Measure"):
        timesig = measure.find(".//TimeSig")
        if timesig is not None:
            sig = Fraction(int(timesig.findtext("sigN")) * 4, int(timesig.findtext("sigD")))
        own = measure.get("len")
        lengths.append(Fraction(own) * 4 if own else sig)
    return lengths


def _beat_unit(mscx_path: str) -> List[Fraction]:
    """Each printed bar's beat, in quarters: a quarter in 4/4, an eighth in 6/8."""
    root = etree.parse(mscx_path).getroot()
    staff = root.find(".//Score/Staff")
    unit = Fraction(1)
    units = []
    for measure in staff.findall("Measure"):
        timesig = measure.find(".//TimeSig")
        if timesig is not None:
            unit = Fraction(4, int(timesig.findtext("sigD")))
        units.append(unit)
    return units


def timing(mscx_path: str, mpos_path: str, midi_path: str) -> Dict:
    """Playback time -> score position, on MuseScore's clock.

    ``points`` is a list of ``[seconds, measure, beat]``: ``measure`` is the
    0-based index of the bar in the MusicXML, ``beat`` is quarter notes from that
    bar's start. A point stands at every beat of every bar played, wherever the
    tempo changes, and at each bar's end, so the position at any moment is a
    straight line between the two points around it when both name the same bar.
    A repeat or a jump shows as the next point naming an earlier (or a much
    later) bar.
    """
    from src.score_export.clock import TempoMap, read_mpos

    lengths = bar_lengths(mscx_path)
    units = _beat_unit(mscx_path)
    count, order = read_mpos(mpos_path)
    if count != len(lengths):
        raise PublishError(f"MuseScore counts {count} bars and the score {len(lengths)}; "
                           "the timing cannot be trusted.")
    tempo = TempoMap.from_midi(midi_path)
    changes = [Fraction(q).limit_denominator(960) for q in tempo.changes]
    starts_ms = [int(e.get("position"))
                 for e in etree.parse(mpos_path).getroot().findall(".//events/event")]

    points: List[List] = []
    q = Fraction(0)
    for played, bar in enumerate(order):
        length = lengths[bar]
        seconds = tempo.seconds(float(q))
        if abs(seconds - starts_ms[played] / 1000) > TIMING_TOLERANCE:
            raise PublishError(
                f"Bar {bar + 1} (played {played + 1}.) starts at {starts_ms[played]} ms "
                f"in MuseScore's play order but at {seconds * 1000:.0f} ms on its MIDI "
                "clock; the timing cannot be trusted.")
        offsets = set()
        beat = Fraction(0)
        while beat < length:
            offsets.add(beat)
            beat += units[bar]
        offsets.update(c - q for c in changes if q < c < q + length)
        offsets.add(length)
        for offset in sorted(offsets):
            points.append([round(tempo.seconds(float(q + offset)), 3), bar,
                           round(float(offset), 4)])
        q += length
    return {"version": TIMING_VERSION, "measures": count,
            "duration": points[-1][0] if points else 0.0, "points": points}


# ---------------------------------------------------------------------------
# The files
# ---------------------------------------------------------------------------
@dataclass
class Bundle:
    musicxml: str
    timing: Dict
    parts: List[str] = field(default_factory=list)  # part names, in score order
    midi: str = ""
    # Where each part is drawn when some share a staff (#420): name -> {"staff":
    # 0-based MusicXML part, "voices": [MusicXML voice numbers]}. Empty: one each.
    layout: Dict[str, Dict] = field(default_factory=dict)
    # The sung MP3 of each part, when sing_song sang them (#439): name -> {"mp3":
    # path here, "voice": the voice credited on the site}. Empty: piano only.
    sung: Dict[str, Dict] = field(default_factory=dict)
    # words.json beside the sung MP3s (sing_song, eeron-omat-taskit#110): each part's notes with
    # the syllable sung on each, so the site sings a shared note once only on the same syllable.
    words: str = ""


def score_credits(mscx_path: str) -> Dict[str, str]:
    """The composer, lyricist and arranger the score's own metaTags name, the ones set."""
    try:
        score = etree.parse(mscx_path).getroot().find("Score")
    except (OSError, etree.XMLSyntaxError):
        return {}
    if score is None:
        return {}
    tags = {m.get("name"): " ".join((m.text or "").split()) for m in score.findall("metaTag")}
    return {name: tags[name] for name in CREDITS if tags.get(name)}


def score_parts(mscx_path: str) -> List[str]:
    """The names the score's singing parts have now, top staff first: what a
    publish calls them unless the Publish panel names them (#402)."""
    from src.score_export import score
    try:
        return score.singing_part_names(etree.parse(mscx_path).getroot())
    except (OSError, etree.XMLSyntaxError):
        return []


def part_count_message(count: int) -> str:
    return f"The score now has {count} parts; check the part names on the Publish panel."


def _published_root(cleaned_path: str, part_names: Optional[List[str]]) -> etree._Element:
    """The score as a publish names it: silent parts out, the saved names on."""
    from src.score_export import score
    root = etree.parse(cleaned_path).getroot()
    score.drop_parts(root, score.silent_parts(root))
    if part_names is not None and len(part_names) == len(score.singing_part_names(root)):
        score.rename_parts(root, part_names)
    return root


def staff_group_proposal(cleaned_path: str,
                         part_names: Optional[List[str]] = None) -> Dict:
    """The parts that would share a staff on the site, worked out from the page
    (#420): {"groups": [[upper, lower], ...], "source": ..., "parts": n}. A pair
    whose parts sing too many voices between them to share a staff is left out
    and named under "skipped"."""
    from src.score_export import score, staff_groups
    try:
        root = _published_root(cleaned_path, part_names)
    except (OSError, etree.XMLSyntaxError):
        return {"groups": [], "source": "", "parts": 0, "skipped": []}
    groups, source = staff_groups.proposal(root)
    fits = [list(g) for g in groups if score.can_share(root, *g)]
    return {"groups": fits, "source": source,
            "parts": len(score.singing_part_names(root)),
            "skipped": [list(g) for g in groups if list(g) not in fits]}


def check_staff_groups(cleaned_path: str, groups: Sequence[Sequence[str]],
                       part_names: Optional[List[str]] = None) -> None:
    """Refuse a hand-set pairing the score cannot draw: a part it does not have,
    one named twice, or two parts singing more voices than one staff holds."""
    from src.score_export import score, staff_groups
    root = _published_root(cleaned_path, part_names)
    names = score.singing_part_names(root)
    try:
        staff_groups.validate_groups(groups, names)
    except ValueError as exc:
        raise PublishError(f"Shared staves: {exc}") from None
    for upper, lower in groups:
        if not score.can_share(root, upper, lower):
            raise PublishError(
                f"Shared staves: {upper} and {lower} sing two voices of their own "
                "somewhere, more than one staff can hold for both.")


def build_bundle(cleaned_path: str, out_dir: str, *, initial_bpm: Optional[int] = None,
                 tempo_changes: Optional[List[Dict]] = None,
                 part_names: Optional[List[str]] = None,
                 staff_groups: Optional[Sequence[Sequence[str]]] = None,
                 log: Logger = _noop) -> Bundle:
    """MusicXML, MIDI, the timing and the part names, made off one prepared score.

    `part_names` are the names saved on the Publish panel, by position (#402). A
    score that has since been cleaned into another number of parts is refused
    rather than named by guesswork.

    `staff_groups` are the parts that share a staff in the MusicXML (#420); None
    works them out from the page when there are more than five singing parts, and
    an empty list shares none. Only the picture changes: the MIDI and the timing
    come off the unmerged copy, so the site still mixes one track per part.
    """
    from src.score_export import musescore, score

    if part_names is not None:
        count = len(score_parts(cleaned_path))
        if count != len(part_names):
            raise PublishError(part_count_message(count))
    source, dropped = score.prepare(cleaned_path, out_dir, initial_bpm=initial_bpm,
                                    tempo_changes=tempo_changes, part_names=part_names)
    if dropped:
        log(f"Leaving out {', '.join(dropped)} (no notes to sing)")
    names = musescore.part_names(etree.parse(source).getroot())

    if staff_groups is None:
        proposal = staff_group_proposal(source)
        groups = proposal["groups"]
        for upper, lower in proposal["skipped"]:
            log(f"{upper} and {lower} keep a staff each: between them they sing more "
                "voices than one staff holds.")
        how = {"page": "from the page", "page, per system": "from the page",
               "part names": "guessed from the part names"}.get(proposal["source"], "")
    else:
        groups = [list(g) for g in staff_groups]
        check_staff_groups(source, groups)
        how = "as set on the Publish panel"
    picture, layout = source, {}
    if groups:
        log("Shared staves: " + ", ".join("/".join(g) for g in groups) + f" ({how})")
        picture, layout = score.merged_copy(source, out_dir, groups)

    log("Exporting the score (MusicXML), its play order and its clock (MuseScore CLI)")
    musicxml = musescore.run_musescore(picture, os.path.join(out_dir, "score.musicxml"))
    with open(musicxml, "rb") as f:
        hidden, count = hide_tempo_marks(f.read())
    if count:
        log(f"Hiding {count} tempo mark(s) on the site")
        with open(musicxml, "wb") as f:
            f.write(hidden)
    mpos = musescore.run_musescore(source, os.path.join(out_dir, "score.mpos"))
    midi = musescore.run_musescore(source, os.path.join(out_dir, "score.mid"))
    clock = timing(source, mpos, midi)
    if groups:
        bars = _musicxml_bars(musicxml)
        if bars != len(bar_lengths(source)):
            raise PublishError(f"The score with shared staves has {bars} bars and the "
                               f"score it was made from {len(bar_lengths(source))}.")
    return Bundle(musicxml=musicxml, timing=clock, parts=list(names), midi=midi,
                  layout=layout)


def _musicxml_bars(path: str) -> int:
    part = etree.parse(path).getroot().find("part")
    return len(part.findall("measure")) if part is not None else 0


# ---------------------------------------------------------------------------
# Tempo marks (#409)
# ---------------------------------------------------------------------------
def hide_tempo_marks(musicxml: bytes) -> Tuple[bytes, int]:
    """The MusicXML with every tempo mark set not to print, and how many were hidden.

    The site's score drawer (OpenSheetMusicDisplay) puts a tempo mark above the
    staff without making room for it, so it lands on the words of the system
    above (#409). The marks are hidden rather than deleted: the speed stays in
    the file, and the site plays the MIDI anyway. MuseScore writes the marks
    ``prepare`` adds as visible whatever the score says, so the export is where
    this has to happen. A mark is a ``<direction>`` carrying ``<sound tempo>``;
    its ``<metronome>`` and its words ("Adagio", "Tempo I") both go.
    A file with nothing to hide comes back as it was, byte for byte.
    """
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
    tree = etree.parse(io.BytesIO(musicxml), parser)
    count = 0
    for direction in tree.iter("direction"):
        if not any(sound.get("tempo") for sound in direction.findall("sound")):
            continue
        shown = [mark for mark in direction.iter("metronome", "words")
                 if mark.get("print-object") != "no"]
        for mark in shown:
            mark.set("print-object", "no")
        count += bool(shown)
    if not count:
        return musicxml, 0
    out = io.BytesIO()
    tree.write(out, xml_declaration=True, encoding=tree.docinfo.encoding or "UTF-8")
    return out.getvalue(), count


# ---------------------------------------------------------------------------
# Cloudflare
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Config:
    account_id: str
    api_token: str
    bucket: str
    database_id: str

    @classmethod
    def from_env(cls) -> Optional["Config"]:
        values = {key: (os.environ.get(env) or "").strip() for key, env in (
            ("account_id", "CLOUDFLARE_ACCOUNT_ID"),
            ("api_token", "CLOUDFLARE_API_TOKEN"),
            ("bucket", "STEMMANAUHAT_R2_BUCKET"),
            ("database_id", "STEMMANAUHAT_D1_DATABASE_ID"))}
        values["bucket"] = values["bucket"] or "stemmanauhat"
        if not all(values.values()):
            return None
        return cls(**values)


MISSING_CONFIG = ("Publishing is off: set CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN and "
                  "STEMMANAUHAT_D1_DATABASE_ID (and STEMMANAUHAT_R2_BUCKET if it is not "
                  "\"stemmanauhat\") in .env.")


class Cloudflare:
    """What a publish asks of Cloudflare: read, list, put and delete R2 objects, query D1."""

    def __init__(self, config: Config, opener=None):
        self.config = config
        self._open = opener or urllib.request.urlopen

    def _call(self, method: str, path: str, data: bytes = None,
              content_type: str = "application/json") -> bytes:
        url = f"{API}/accounts/{self.config.account_id}{path}"
        last = None
        for attempt in range(TRIES):
            request = urllib.request.Request(url, data=data, method=method, headers={
                "Authorization": f"Bearer {self.config.api_token}",
                "Content-Type": content_type})
            try:
                with self._open(request, timeout=TIMEOUT) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                body = exc.read() or b""
                last = f"{method} {path}: Cloudflare said {exc.code} {_errors(body)}"
                if exc.code != 429 and exc.code < 500:
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = f"{method} {path}: {exc}"
            if attempt + 1 < TRIES:
                time.sleep(2 ** attempt)
        raise PublishError(last)

    def _object(self, key: str) -> str:
        return (f"/r2/buckets/{urllib.parse.quote(self.config.bucket)}/objects/"
                f"{urllib.parse.quote(key, safe='/')}")

    def get(self, key: str) -> bytes:
        return self._call("GET", self._object(key))

    def list(self, prefix: str) -> List[Dict]:
        """The R2 objects under `prefix`: each a dict with ``key`` and ``http_metadata``."""
        objects: List[Dict] = []
        cursor = None
        while True:
            query = {"prefix": prefix, **({"cursor": cursor} if cursor else {})}
            body = self._call("GET", f"/r2/buckets/{urllib.parse.quote(self.config.bucket)}"
                                     f"/objects?{urllib.parse.urlencode(query)}")
            reply = json.loads(body or b"{}")
            objects += reply.get("result") or []
            info = reply.get("result_info") or {}
            cursor = info.get("cursor")
            if not (info.get("is_truncated") and cursor):
                return objects

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._call("PUT", self._object(key), data, content_type)

    def delete(self, key: str) -> None:
        self._call("DELETE", self._object(key))

    def query(self, sql: str, params: Sequence = ()) -> List[Dict]:
        body = self._call("POST", f"/d1/database/{self.config.database_id}/query",
                          json.dumps({"sql": sql, "params": list(params)}).encode())
        reply = json.loads(body or b"{}")
        if not reply.get("success", False):
            raise PublishError(f"D1 refused the query: {_errors(body)}")
        return reply.get("result") or []


def _errors(body: bytes) -> str:
    try:
        errors = json.loads(body or b"{}").get("errors") or []
        return "; ".join(e.get("message", str(e)) for e in errors)
    except (ValueError, AttributeError):
        return body[:200].decode(errors="replace")


# ---------------------------------------------------------------------------
# The site's choirs
# ---------------------------------------------------------------------------
# Each copy of the site has its own choirs, in its D1 `choirs` table (made by its
# setup script), so the list is read from there rather than written down here
# (#408): a list in this file was one choir director's choirs, and anybody else
# had to edit code to publish at all. `public` is the one id every site has
# (its migration 0001), and is the open demo list.
PUBLIC = "public"
CHOIRS_FILE = ".choirs.json"


def site_choirs(client: "Cloudflare") -> List[Dict]:
    """The site's choirs, ``[{"id", "name", "public", "staging"}]``, the private ones first.

    ``staging`` marks the site's test list (stemmanauhat-cf#73): a song is tried
    out there before it moves to a choir. A site older than that column has none.
    """
    try:
        result = client.query("SELECT id, name, public, staging FROM choirs "
                              "ORDER BY public, name")
    except PublishError as exc:
        if "staging" not in str(exc):
            raise
        result = client.query("SELECT id, name, public, 0 FROM choirs ORDER BY public, name")
    choirs = []
    for part in result:
        for row in part.get("results") or []:
            if isinstance(row, dict):
                row = tuple(row.values())
            choir, name, public, staging = row
            choirs.append({"id": choir, "name": name or choir, "public": bool(public),
                           "staging": bool(staging)})
    return choirs


def choirs(config: Optional[Config], songs_dir: str,
           client: Optional["Cloudflare"] = None) -> Dict:
    """The choirs the Publish panel offers, and where the list came from.

    Read from the site every time, and kept in ``songs/.choirs.json`` so the panel
    still has a list while Cloudflare cannot be reached; the reply then says it
    is the saved one (``saved_at``) and why (``error``).
    """
    if config is None:
        return {"choirs": [], "error": MISSING_CONFIG, "saved_at": None}
    path = os.path.join(songs_dir, CHOIRS_FILE)
    try:
        listed = site_choirs(client or Cloudflare(config))
    except (PublishError, ValueError) as exc:
        try:
            with open(path, encoding="utf-8") as fh:
                saved = json.load(fh)
            return {"choirs": saved["choirs"], "error": str(exc), "saved_at": saved.get("at")}
        except (OSError, ValueError, KeyError):
            return {"choirs": [], "error": str(exc), "saved_at": None}
    try:
        os.makedirs(songs_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"choirs": listed, "at": time.time()}, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError:
        pass  # a list not saved costs the offline fallback, not this answer
    return {"choirs": listed, "error": None, "saved_at": None}


# ---------------------------------------------------------------------------
# The site's voice tree (stemmanauhat-cf#107)
# ---------------------------------------------------------------------------
# The site carries a singer's part from song to song with a tree of voices in
# D1: `voices` (id, parent, name_fi, name_en, names) up to four levels deep, and
# `unplaced_parts`, names in no place on purpose (a solo, a trio). A part name
# in neither is refused before anything goes up, and the person publishing says
# where it goes (#430): where a part belongs is their call, never a guess. The
# rules are the site's own (`knowsName`, `treePlan` in its publish-songs.mjs).
MAX_DEPTH = 4
NO_PLACE = "-"


def _norm(name: str) -> str:
    """Names are the same when they differ only in letter case and spaces."""
    return re.sub(r"\s+", "", name).lower()


@dataclass
class VoiceTree:
    voices: List[Dict]   # {"id", "parent", "name", "names"}, in the site's order
    unplaced: List[str]


def _rows(result: List[Dict], columns: Sequence[str]) -> List[Dict]:
    rows = []
    for part in result:
        for row in part.get("results") or []:
            rows.append(dict(row) if isinstance(row, dict) else dict(zip(columns, row)))
    return rows


def voice_tree(client: "Cloudflare") -> Optional[VoiceTree]:
    """The site's voice tree, or None for a site that has none yet (before its migration 0009)."""
    try:
        voices = _rows(client.query("SELECT id, parent, name_fi, names FROM voices"),
                       ("id", "parent", "name_fi", "names"))
        unplaced = _rows(client.query("SELECT name FROM unplaced_parts"), ("name",))
    except PublishError as exc:
        if "no such table" in str(exc):
            return None
        raise
    return VoiceTree(
        voices=[{"id": v["id"], "parent": v["parent"], "name": v["name_fi"],
                 "names": json.loads(v["names"] or "[]")} for v in voices],
        unplaced=[r["name"] for r in unplaced])


def _depth(voices: Sequence[Dict], vid: str) -> float:
    """How many levels down `vid` is (1 at the top); infinite when its parents are missing or loop."""
    by_id = {v["id"]: v for v in voices}
    seen = set()
    voice = by_id.get(vid)
    while voice:
        if voice["id"] in seen:
            return float("inf")
        seen.add(voice["id"])
        if voice["parent"] is None:
            return len(seen)
        voice = by_id.get(voice["parent"])
    return float("inf")


def knows_name(tree: VoiceTree, name: str) -> bool:
    """Whether the site can place `name`: a place's name, a name in no place, or a
    split part ("S1b") of a name above the fourth level."""
    key = _norm(name)

    def at(k):
        return next((v for v in tree.voices if any(_norm(n) == k for n in v["names"])), None)
    if at(key) or any(_norm(n) == key for n in tree.unplaced):
        return True
    above = at(key[:-1]) if len(key) > 1 and key.endswith("b") else None
    return above is not None and _depth(tree.voices, above["id"]) < MAX_DEPTH


def unknown_names(tree: VoiceTree, names: Sequence[str]) -> List[str]:
    """The part names the tree does not know, once each, as the song spells them."""
    out: List[str] = []
    for name in names:
        if not knows_name(tree, name) and _norm(name) not in {_norm(n) for n in out}:
            out.append(name)
    return out


def tree_places(tree: VoiceTree) -> List[Dict]:
    """The places for the Publish panel to offer, each under its parent, with its depth."""
    children: Dict[Optional[str], List[Dict]] = {}
    ids = {v["id"] for v in tree.voices}
    for voice in tree.voices:
        children.setdefault(voice["parent"] if voice["parent"] in ids else None, []).append(voice)
    out: List[Dict] = []

    def walk(parent, depth):
        for voice in children.get(parent, []):
            if any(o["id"] == voice["id"] for o in out):
                continue  # a loop in hand-edited rows
            out.append({"id": voice["id"], "name": voice["name"], "depth": depth,
                        "can_hold": depth < MAX_DEPTH})
            walk(voice["id"], depth + 1)
    walk(None, 1)
    return out


def _said(place: Dict) -> str:
    """One place choice in words, for the publish log."""
    if place.get("id") == NO_PLACE:
        return f"{place['name']} in no place"
    if place.get("parent") is not None:
        return f"{place['name']} a new place under {place['parent']}"
    return f"{place['name']} → {place.get('id')}"


def place_statements(tree: VoiceTree, names: Sequence[str],
                     places: Sequence[Dict]) -> List[Tuple[str, List]]:
    """The D1 statements that put every unknown part name where the person
    publishing said, in order. `places` is ``[{"name", "id"}]`` for a name of an
    existing place, ``[{"name", "parent"}]`` for a new place under one, or
    ``[{"name", "id": "-"}]`` for no place. Refuses, naming them, when an unknown
    name has no place given or a place cannot be used.
    """
    unknown = {_norm(n): n for n in unknown_names(tree, names)}
    ids = {v["id"] for v in tree.voices}
    statements: List[Tuple[str, List]] = []
    for place in places or []:
        name = str(place.get("name") or "").strip()
        if _norm(name) not in unknown:
            continue  # a choice kept from before, for a name the tree knows now or the song lost
        del unknown[_norm(name)]
        vid, parent = place.get("id"), place.get("parent")
        if vid == NO_PLACE:
            statements.append(("INSERT OR IGNORE INTO unplaced_parts (name) VALUES (?)", [name]))
        elif parent is not None:
            if parent not in ids or _depth(tree.voices, parent) >= MAX_DEPTH:
                raise PublishError(f"Part {name}: there is no place {parent} to put a new "
                                   "place under, or it is at the fourth level.")
            # The new place's id is the name it is made for, made unique; it shows
            # as that name in both languages until renamed on the site.
            new_id, n = name, 2
            while new_id in ids:
                new_id, n = f"{name} {n}", n + 1
            ids.add(new_id)
            statements.append(("INSERT INTO voices (id, parent, name_fi, name_en, names) "
                               "VALUES (?, ?, ?, ?, ?)",
                               [new_id, parent, name, name, json.dumps([name], ensure_ascii=False)]))
        elif vid in ids:
            statements.append(("UPDATE voices SET names = json_insert(names, '$[#]', ?) "
                               "WHERE id = ?", [name, vid]))
        else:
            raise PublishError(f"Part {name}: the site has no place {vid}.")
    if unknown:
        raise PublishError("Say where each new part name goes on the site: "
                           + ", ".join(unknown.values()) + ".")
    return statements


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------


def records(state: Optional[Dict]) -> Dict[str, Dict]:
    """The song's publish record per choir, out of its ``publish`` state.

    A song published before #384 kept one record, under ``site``.
    """
    state = state or {}
    if isinstance(state.get("sites"), dict):
        return dict(state["sites"])
    site = state.get("site")
    return {site["choir"]: site} if site and site.get("choir") else {}


def replaced(choir: str, published: Dict[str, Dict],
             staging: Sequence[str] = ()) -> List[str]:
    """The choirs whose listing a publish for `choir` takes away.

    A song may be in any number of lists at once (#450: Eero's mixed choirs share
    songs, and the one-private-choir rule of #384 took a song off ``joy`` when it
    was published to ``joy-alumnit``), so publishing takes away only the test list
    (`staging`, #422): a fix tried out there leaves the song on its choirs' lists,
    and publishing to a choir takes it off the test list, since it is done.
    Taking a song off a choir is done on the site.
    """
    if choir == PUBLIC or choir in staging:
        return []
    return [c for c in published if c in staging and c != choir]


def publish(*, slug: str, title: str, choir: str, bundle: Bundle, client: Cloudflare,
            credits: Optional[Dict[str, str]] = None,
            published: Optional[Dict[str, Dict]] = None,
            choirs: Optional[Sequence[Dict]] = None, places: Sequence[Dict] = (),
            log: Logger = _noop) -> Dict:
    """Upload `bundle` and register it for `choir`. Returns the record to keep.

    The files go under a fresh version prefix first; the D1 row is pointed at
    them last, so the site shows either the old version or the whole new one.
    `published` is the record each choir's last publish returned
    (``records``). What this choir's last publish left is taken away
    afterwards, and so is the song on the test list, row and files
    (``replaced``); every other choir's listing stays (#450), and publishing to
    a test list takes nothing away. `credits` names the composer, lyricist and arranger; an
    empty or missing one is left out. `choirs` is the site's choirs
    (``site_choirs``) when the caller has just read them; without it they are
    read here. `places` says where each part name the site's voice tree does
    not know goes (``place_statements``); those rows are written before
    anything goes up, as the site's own publish script does.
    """
    listed = list(choirs) if choirs is not None else site_choirs(client)
    known = [c["id"] for c in listed]
    staging = [c["id"] for c in listed if c.get("staging")]
    if choir not in known:
        raise PublishError(f"Unknown choir {choir!r}; the site has {', '.join(known) or 'none'}.")
    version = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    prefix = f"songs/{choir}/{slug}/{version}/"
    parts = [{"name": name, **bundle.layout.get(name, {})} for name in bundle.parts]
    if bundle.sung and set(bundle.sung) != set(bundle.parts):
        raise PublishError("Every part needs its sung MP3, or none: "
                           + ", ".join(n for n in bundle.parts if n not in bundle.sung) + ".")
    sung_files = []
    for index, part in enumerate(parts, 1):
        if bundle.sung:
            # The site plays parts/<n>-<Name>.mp3 as the part's "Laulu" (stemmanauhat-cf#36).
            name = sung_file(index, part["name"])
            sung_files.append((name, bundle.sung[part["name"]]["mp3"]))
            part.update(file=name, sung=True)
            if bundle.sung[part["name"]].get("voice"):
                part["voice"] = bundle.sung[part["name"]]["voice"]
    duration = bundle.timing.get("duration", 0.0)
    named = {name: (credits or {}).get(name, "").strip() for name in CREDITS}
    named = {name: text for name, text in named.items() if text}
    manifest = {"slug": slug, "title": title, "choir": choir, "version": version,
                "score": "score.musicxml", "timing": "timing.json", "midi": "score.mid",
                "parts": parts, "duration": duration, **named}

    tree = voice_tree(client)
    if tree is not None:
        unknown = {_norm(n) for n in unknown_names(tree, bundle.parts)}
        for sql, params in place_statements(tree, bundle.parts, places):
            client.query(sql, params)
        if unknown:
            log("Voice tree: " + ", ".join(_said(p) for p in places
                                           if _norm(str(p.get("name") or "")) in unknown))

    uploads: List[Tuple[str, bytes, str]] = []
    with open(bundle.musicxml, "rb") as f:
        uploads.append(("score.musicxml", f.read(), "application/vnd.recordare.musicxml+xml"))
    with open(bundle.midi, "rb") as f:
        uploads.append(("score.mid", f.read(), "audio/midi"))
    uploads.append(("timing.json", json.dumps(bundle.timing).encode(), "application/json"))
    for name, path in sung_files:
        with open(path, "rb") as f:
            uploads.append((name, f.read(), "audio/mpeg"))
    if bundle.sung and bundle.words:
        with open(bundle.words, "rb") as f:
            uploads.append(("words.json", f.read(), "application/json"))
    uploads.append(("manifest.json", json.dumps(manifest, ensure_ascii=False).encode(),
                    "application/json"))

    for index, (name, data, kind) in enumerate(uploads, 1):
        log(f"Uploading {name} ({len(data) // 1024} KB) — {index}/{len(uploads)}")
        client.put(prefix + name, data, kind)

    log(f"Registering the song for {choir}")
    published_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    client.query(UPSERT, [choir, slug, title, prefix, json.dumps(parts, ensure_ascii=False),
                          duration, published_at, *(named.get(name) for name in CREDITS)])

    record = {"choir": choir, "slug": slug, "version": version, "prefix": prefix, "at": time.time(),
              "files": [prefix + name for name, _data, _kind in uploads],
              "parts": [p["name"] for p in parts], "duration": duration, "credits": named}
    _clean_up(published or {}, record, client, log, staging)
    log(f"Published to the {choir} list.")
    return record


def _clean_up(published: Dict[str, Dict], current: Dict, client: Cloudflare,
              log: Logger, staging: Sequence[str] = ()) -> None:
    """Take away what this publish replaces. A failure here costs storage, not the song."""
    try:
        tests = [c for c in staging if c != current["choir"]]
        if current["choir"] != PUBLIC and current["choir"] not in staging and tests:
            # A choir's publish takes the song off the test list (#422), a row we hold
            # no record of too; every other choir's row stays (#450).
            gone = client.query("DELETE FROM songs WHERE slug = ? AND choir IN (%s) RETURNING choir"
                                % ", ".join("?" * len(tests)), [current["slug"], *tests])
            for choir in sorted({row[0] if isinstance(row, (list, tuple)) else row.get("choir")
                                 for result in gone for row in result.get("results") or []}):
                log(f"Removed it from the {choir} list.")
        stale = [published.get(current["choir"])]
        stale += [published[c] for c in replaced(current["choir"], published, staging)]
        for record in stale:
            for key in (record or {}).get("files") or []:
                if key not in current["files"]:
                    client.delete(key)
    except PublishError as exc:
        log(f"Could not remove the previous version's files: {exc}")


# ---------------------------------------------------------------------------
# Songs already on the site (#409)
# ---------------------------------------------------------------------------
# What a copied file is served as when R2 does not say.
_TYPES = {".musicxml": "application/vnd.recordare.musicxml+xml", ".mid": "audio/midi",
          ".json": "application/json", ".mp3": "audio/mpeg"}


def hide_published_tempo_marks(client: Cloudflare, *, songs_dir: Optional[str] = None,
                               dry_run: bool = False, log: Logger = _noop) -> List[Dict]:
    """Give every song on the site whose score shows tempo marks a version that hides them.

    The site serves a version's files as immutable for a year, so a file cannot be
    changed where it stands: each such song's files are copied as they are into a
    fresh version prefix, the score with its tempo marks hidden and the manifest
    with its new version, and the song's row is pointed there. Nothing is
    re-exported from today's score, which may have moved on since it was
    published. The old version stays in R2. A publish record in `songs_dir` that
    names the old prefix is moved to the new one, so the next publish cleans up
    the right files. Returns what was (or, with `dry_run`, would be) changed.
    """
    rows = [row for result in client.query("SELECT choir, slug, prefix FROM songs "
                                           "ORDER BY choir, slug")
            for row in result.get("results") or []]
    changed = []
    for row in rows:
        choir, slug, old = (row["choir"], row["slug"], row["prefix"]) \
            if isinstance(row, dict) else row
        try:
            manifest = json.loads(client.get(old + "manifest.json"))
        except PublishError:
            manifest = {}
        score_name = manifest.get("score") or "score.musicxml"
        try:
            score, count = hide_tempo_marks(client.get(old + score_name))
        except (PublishError, etree.XMLSyntaxError) as exc:
            log(f"{choir}/{slug}: skipped, its score could not be read ({exc})")
            continue
        if not count:
            continue
        version = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        new = f"{old.rstrip('/').rsplit('/', 1)[0]}/{version}/"
        if new == old:
            raise PublishError(f"{choir}/{slug} was published this second; run it again")
        change = {"choir": choir, "slug": slug, "old": old, "new": new, "version": version,
                  "hidden": count}
        changed.append(change)
        log(f"{choir}/{slug}: {count} tempo mark(s) to hide, {old} -> {new}")
        if dry_run:
            continue
        files = []
        for obj in client.list(old):
            name = obj["key"][len(old):]
            if name == score_name:
                data = score
            elif name == "manifest.json" and manifest:
                data = json.dumps({**manifest, "version": version}, ensure_ascii=False).encode()
            else:
                data = client.get(obj["key"])
            kind = ((obj.get("http_metadata") or {}).get("contentType")
                    or _TYPES.get(os.path.splitext(name)[1], "application/octet-stream"))
            client.put(new + name, data, kind)
            files.append(new + name)
        client.query("UPDATE songs SET prefix = ? WHERE choir = ? AND slug = ? AND prefix = ?",
                     [new, choir, slug, old])
        change["files"] = files
        if songs_dir:
            _move_records(songs_dir, change, log)
    return changed


def _move_records(songs_dir: str, change: Dict, log: Logger) -> None:
    """Point the publish records that name the old version at the new one.

    Only those records change; the rest of each ``.song.json`` is written back
    as it was read."""
    for entry in sorted(os.listdir(songs_dir)):
        path = os.path.join(songs_dir, entry, ".song.json")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        pub = data.get("publish") or {}
        found = [r for r in list((pub.get("sites") or {}).values()) + [pub.get("site")]
                 if isinstance(r, dict) and r.get("prefix") == change["old"]]
        for record in found:
            record.update(prefix=change["new"], version=change["version"],
                          files=list(change["files"]))
        if found:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
            log(f"Moved {entry}'s publish record to {change['version']}")
