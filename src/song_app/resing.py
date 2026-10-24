"""Which songs still need a re-sing after the singer changed, and how each run is going (#453).

The singer carries a version (``SINGER_VERSION`` in ``src/sing_song/sing_ufr.py``), bumped
whenever the singing rules change, and a sung publish records the version it was sung with
(``singer_version`` on the song's publish record for that choir). A song is **pending** in a
choir when the site lists it there with sung parts and the listing is not one this app published
with the current version: no record, a record for another upload (``prefix``), or an older
version. Re-singing a song means publishing it to each choir it is pending in.

Which songs are sung is the site's to say, not ``.song.json``'s: most were sung and published
before ``sing_song`` kept records here, so the site's ``songs`` table is read (its parts say
``"sung": true``) and kept in ``songs/.site-songs.json`` for when Cloudflare cannot be reached.
The site's test list (a ``staging`` choir) is never pending.

Runs are agents' ``sing_song.py`` jobs; the app only shows them, off each song's
``.sing-run.json`` (``src/sing_song/progress.py``).
"""
import json
import os
import time
from typing import Dict, List, Optional

from src.sing_song import progress

from . import publish, state

SITE_FILE = ".site-songs.json"
MAX_AGE = 120   # seconds a saved read of the site is used before reading it again


def _rows(result) -> List[Dict]:
    rows = []
    for part in result or []:
        for row in part.get("results") or []:
            if isinstance(row, dict):
                rows.append(row)
    return rows


def sung_parts(parts) -> bool:
    """True when a site row's ``parts`` (JSON text or a list) has a sung part."""
    if isinstance(parts, str):
        try:
            parts = json.loads(parts)
        except ValueError:
            return False
    return any(isinstance(p, dict) and p.get("sung") for p in parts or [])


def read_site(client: "publish.Cloudflare") -> Dict:
    """``{"songs": [{"choir", "slug", "prefix", "sung"}], "staging": [choir ids]}`` off the site."""
    songs = [{"choir": r.get("choir"), "slug": r.get("slug"), "prefix": r.get("prefix"),
              "sung": sung_parts(r.get("parts"))}
             for r in _rows(client.query("SELECT choir, slug, prefix, parts FROM songs"))]
    staging = [c["id"] for c in publish.site_choirs(client) if c.get("staging")]
    return {"songs": songs, "staging": staging}


def site(config: Optional["publish.Config"], songs_dir: str, client=None,
         max_age: float = MAX_AGE, now: Optional[float] = None) -> Dict:
    """The site's songs, read again once the saved read is older than `max_age`; with
    ``error`` and ``at`` saying when the saved one is all there is."""
    now = time.time() if now is None else now
    path = os.path.join(songs_dir, SITE_FILE)
    saved = None
    try:
        with open(path, encoding="utf-8") as fh:
            saved = json.load(fh)
    except (OSError, ValueError):
        pass
    if saved and now - saved.get("at", 0) < max_age:
        return dict(saved, error=None)
    if config is None:
        error = publish.MISSING_CONFIG
    else:
        try:
            listed = dict(read_site(client or publish.Cloudflare(config)), at=now)
        except (publish.PublishError, ValueError, OSError) as exc:
            error = str(exc)
        else:
            try:
                os.makedirs(songs_dir, exist_ok=True)
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(listed, fh, ensure_ascii=False)
                os.replace(tmp, path)
            except OSError:
                pass   # costs the offline fallback, not this answer
            return dict(listed, error=None)
    if saved:
        return dict(saved, error=error)
    return {"songs": [], "staging": [], "at": None, "error": error}


def song_status(rows: List[Dict], records: Dict[str, Dict], version: int,
                read_at: Optional[float] = None) -> Dict:
    """``{"pending": [choirs], "current": [choirs]}`` for one song's site rows and its publish
    records (``publish.records``). A record published after the site was read (`read_at`) is
    newer than the saved read, so it stands for that choir's row: a song just re-sung shows as
    up to date at once rather than once the read is renewed."""
    rows = {row["choir"]: row for row in rows}
    if read_at is not None:
        for choir, record in records.items():
            if (record.get("at") or 0) > read_at:
                rows[choir] = {"choir": choir, "prefix": record.get("prefix"), "sung": bool(record.get("sung"))}
    pending, current = [], []
    for row in rows.values():
        if not row.get("sung"):
            continue
        record = records.get(row["choir"]) or {}
        up_to_date = (record.get("prefix") == row.get("prefix")
                      and isinstance(record.get("singer_version"), int)
                      and record["singer_version"] >= version)
        (current if up_to_date else pending).append(row["choir"])
    return {"pending": sorted(pending), "current": sorted(current)}


def overview(songs: List["state.Song"], listed: Dict, version: int) -> Dict:
    """What the song list shows: per song, the choirs it is pending in, those it is up to date
    in, and its last run; plus the totals."""
    staging = set(listed.get("staging") or [])
    by_slug: Dict[str, List[Dict]] = {}
    for row in listed.get("songs") or []:
        if row.get("choir") not in staging:
            by_slug.setdefault(row.get("slug"), []).append(row)
    out = {}
    for song in songs:
        records = {c: r for c, r in publish.records(song.data.get("publish")).items() if c not in staging}
        status = song_status(by_slug.get(song.slug, []), records, version, listed.get("at"))
        status["run"] = progress.read(state.song_dir(song.slug))
        if status["pending"] or status["current"] or status["run"]:
            out[song.slug] = status
    return {
        "version": version,
        "site": {"error": listed.get("error"), "at": listed.get("at")},
        "pending": sum(1 for s in out.values() if s["pending"]),
        "running": sum(1 for s in out.values()
                       if s["run"] and s["run"].get("state") in ("waiting",) + progress.RUNNING),
        "songs": out,
    }
