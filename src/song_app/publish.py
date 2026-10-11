"""Publish a song to the Cloudflare stemmanauhat site (#382, #400).

This is where a song ends: the site (eerovil/stemmanauhat-cf) draws the score
with OpenSheetMusicDisplay and plays its MIDI with a piano soundfont, so what it
needs from here is:

- the score as **MusicXML**;
- the score's **MIDI**, which the site plays;
- a **timing file** saying where in the score playback is at each moment;
- the **part names**, in score order, which the site matches to the MIDI tracks;
- the **credits**: composer, lyricist and arranger, when they are known.

No audio is rendered here (stemmanauhat-cf#36), and nothing goes to YouTube any
more (#400).

All of it is made off one prepared copy of the cleaned score
(`score_export.score.prepare`: silent parts dropped, red marks stripped, the
fermata hold, the opening tempo and the tempo changes set on the Publish
panel), so the parts in the MusicXML, the MIDI and the timing are the same parts
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

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from lxml import etree

Logger = Callable[[str], None]

CHOIRS = ("jm", "naiskuoro", "public")
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


def build_bundle(cleaned_path: str, out_dir: str, *, initial_bpm: Optional[int] = None,
                 tempo_changes: Optional[List[Dict]] = None,
                 log: Logger = _noop) -> Bundle:
    """MusicXML, MIDI, the timing and the part names, made off one prepared score."""
    from src.score_export import musescore, score

    source, dropped = score.prepare(cleaned_path, out_dir, initial_bpm=initial_bpm,
                                    tempo_changes=tempo_changes)
    if dropped:
        log(f"Leaving out {', '.join(dropped)} (no notes to sing)")
    names = musescore.part_names(etree.parse(source).getroot())
    log("Exporting the score (MusicXML), its play order and its clock (MuseScore CLI)")
    musicxml = musescore.run_musescore(source, os.path.join(out_dir, "score.musicxml"))
    mpos = musescore.run_musescore(source, os.path.join(out_dir, "score.mpos"))
    midi = musescore.run_musescore(source, os.path.join(out_dir, "score.mid"))
    clock = timing(source, mpos, midi)
    return Bundle(musicxml=musicxml, timing=clock, parts=list(names), midi=midi)


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
    """The three calls a publish makes: put and delete an R2 object, query D1."""

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
# Publishing
# ---------------------------------------------------------------------------
PUBLIC = "public"


def records(state: Optional[Dict]) -> Dict[str, Dict]:
    """The song's publish record per choir, out of its ``publish`` state.

    A song published before #384 kept one record, under ``site``.
    """
    state = state or {}
    if isinstance(state.get("sites"), dict):
        return dict(state["sites"])
    site = state.get("site")
    return {site["choir"]: site} if site and site.get("choir") else {}


def replaced(choir: str, published: Dict[str, Dict]) -> List[str]:
    """The choirs whose listing a publish for `choir` takes away.

    A song may be in the public list and one choir's list at once, so
    publishing to ``public`` takes nothing away and publishing to a choir takes
    away only the other private choirs: a move between two choirs stays a move.
    """
    if choir == PUBLIC:
        return []
    return [c for c in published if c not in (choir, PUBLIC)]


def publish(*, slug: str, title: str, choir: str, bundle: Bundle, client: Cloudflare,
            credits: Optional[Dict[str, str]] = None,
            published: Optional[Dict[str, Dict]] = None, log: Logger = _noop) -> Dict:
    """Upload `bundle` and register it for `choir`. Returns the record to keep.

    The files go under a fresh version prefix first; the D1 row is pointed at
    them last, so the site shows either the old version or the whole new one.
    `published` is the record each choir's last publish returned
    (``records``). What this choir's last publish left is taken away
    afterwards, and so is the song in any other private choir — rows and files
    — while its public listing stays (``replaced``). `credits` names the
    composer, lyricist and arranger; an empty or missing one is left out.
    """
    if choir not in CHOIRS:
        raise PublishError(f"Unknown choir {choir!r}; choose one of {', '.join(CHOIRS)}.")
    version = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    prefix = f"songs/{choir}/{slug}/{version}/"
    parts = [{"name": name} for name in bundle.parts]
    duration = bundle.timing.get("duration", 0.0)
    named = {name: (credits or {}).get(name, "").strip() for name in CREDITS}
    named = {name: text for name, text in named.items() if text}
    manifest = {"slug": slug, "title": title, "choir": choir, "version": version,
                "score": "score.musicxml", "timing": "timing.json", "midi": "score.mid",
                "parts": parts, "duration": duration, **named}

    uploads: List[Tuple[str, bytes, str]] = []
    with open(bundle.musicxml, "rb") as f:
        uploads.append(("score.musicxml", f.read(), "application/vnd.recordare.musicxml+xml"))
    with open(bundle.midi, "rb") as f:
        uploads.append(("score.mid", f.read(), "audio/midi"))
    uploads.append(("timing.json", json.dumps(bundle.timing).encode(), "application/json"))
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
    _clean_up(published or {}, record, client, log)
    log(f"Published to the {choir} list.")
    return record


def _clean_up(published: Dict[str, Dict], current: Dict, client: Cloudflare,
              log: Logger) -> None:
    """Take away what this publish replaces. A failure here costs storage, not the song."""
    try:
        if current["choir"] != PUBLIC:
            # The site's own rule (stemmanauhat-cf#20): a song is in at most one
            # private choir, and a hand-published row we hold no record of goes too.
            gone = client.query("DELETE FROM songs WHERE slug = ? AND choir NOT IN (?, ?) "
                                "RETURNING choir", [current["slug"], current["choir"], PUBLIC])
            for choir in sorted({row[0] if isinstance(row, (list, tuple)) else row.get("choir")
                                 for result in gone for row in result.get("results") or []}):
                log(f"Removed it from the {choir} list.")
        stale = [published.get(current["choir"])]
        stale += [published[c] for c in replaced(current["choir"], published)]
        for record in stale:
            for key in (record or {}).get("files") or []:
                if key not in current["files"]:
                    client.delete(key)
    except PublishError as exc:
        log(f"Could not remove the previous version's files: {exc}")
