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

Since #484 a version may have changed only some languages (``SINGER_CHANGES``, read by
``progress.singer_changes``), and a record says the ``language`` it was sung in, so an English
change leaves the Finnish songs up to date. A record with no language was sung before that, as
Finnish unless told otherwise; a song whose saved language differs from what was sung is pending,
and so is one with neither, whose words do not look Finnish (``language_check``). That last check
reads the cleaned score, so its answer is kept in ``songs/.resing-language.json`` by the score's
fingerprint.

Runs are agents' ``sing_song.py`` jobs; the app only shows them, off each song's
``.sing-run.json`` (``src/sing_song/progress.py``).
"""
import glob
import json
import logging
import os
import tempfile
import time
from typing import Dict, List, Optional

from src.sing_song import language_check, languages, progress

from . import publish, state

SITE_FILE = ".site-songs.json"
LANGUAGE_FILE = ".resing-language.json"
STALE_TMP = 3600   # seconds after which a temp file left by a killed write is removed
log = logging.getLogger(__name__)
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
                _write_json(songs_dir, SITE_FILE, listed)
            except OSError:
                pass   # costs the offline fallback, not this answer
            return dict(listed, error=None)
    if saved:
        return dict(saved, error=error)
    return {"songs": [], "staging": [], "at": None, "error": error}


def _write_json(songs_dir: str, name: str, data) -> None:
    """Write `songs_dir/name` whole, by rename from a temp file of its own, so two writers at
    once cannot mix their halves; the last one stands. Temp files a killed write left behind
    (older than STALE_TMP) are removed, or the nightly songs backup would commit them."""
    os.makedirs(songs_dir, exist_ok=True)
    for old in glob.glob(os.path.join(songs_dir, name + ".*.tmp")):
        try:
            if time.time() - os.path.getmtime(old) > STALE_TMP:
                os.remove(old)
        except OSError:
            pass
    fd, tmp = tempfile.mkstemp(prefix=name + ".", suffix=".tmp", dir=songs_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, os.path.join(songs_dir, name))
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def up_to_date(record: Dict, row: Dict, version: int, changes: Optional[Dict] = None,
               saved_language: Optional[str] = None, words_look_finnish=None) -> bool:
    """Whether a choir's publish `record` is the site's `row` sung as the current singer would.

    `changes` is ``progress.singer_changes()``; without it every version changed every song.
    `words_look_finnish` is called only when nothing else has decided."""
    sung_with = record.get("singer_version")
    if record.get("prefix") != row.get("prefix") or not isinstance(sung_with, int):
        return False
    language = record.get("language") or "fi"
    # a variant changes with its base: a version naming "la" re-sings a "la-de" song too (#484)
    family = set(languages.chain(language)) or {language}
    for v in range(sung_with + 1, version + 1):
        said = (changes or {}).get(v, "*")
        named = {said} if isinstance(said, str) else set(said)
        if "*" in named or family & named:
            return False
    if saved_language:
        return saved_language == language
    if record.get("language"):
        return True
    return bool(words_look_finnish and words_look_finnish())


def song_status(rows: List[Dict], records: Dict[str, Dict], version: int,
                read_at: Optional[float] = None, changes: Optional[Dict] = None,
                saved_language: Optional[str] = None, words_look_finnish=None) -> Dict:
    """``{"pending": [choirs], "current": [choirs]}`` for one song's site rows and its publish
    records (``publish.records``). A record published after the site was read (`read_at`) is
    newer than the saved read, so it stands for that choir's row: a song just re-sung shows as
    up to date at once rather than once the read is renewed. `changes`, `saved_language` and
    `words_look_finnish` are ``up_to_date``'s (#484); without `words_look_finnish` a song sung
    before records kept a language counts as Finnish."""
    if words_look_finnish is None:
        words_look_finnish = lambda: True   # noqa: E731
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
        fresh = up_to_date(record, row, version, changes, saved_language, words_look_finnish)
        (current if fresh else pending).append(row["choir"])
    return {"pending": sorted(pending), "current": sorted(current)}


class WordsCheck:
    """Whether each song's sung words look Finnish (``language_check.looks_foreign`` on its cleaned
    score), kept in ``songs/.resing-language.json`` as ``{slug: {"fingerprint", "finnish"}}`` so a
    score is read again only once it changed. A score that cannot be read, or has no words, counts
    as not Finnish: it is listed for a re-sing rather than passed. A cache that cannot be read is
    started afresh, said in the log."""

    def __init__(self, songs_dir: str):
        self.songs_dir = songs_dir
        self.changed = False
        try:
            with open(os.path.join(songs_dir, LANGUAGE_FILE), encoding="utf-8") as fh:
                self.saved = json.load(fh)
            if not isinstance(self.saved, dict):
                raise ValueError("not an object")
        except FileNotFoundError:
            self.saved = {}
        except (OSError, ValueError) as exc:
            log.warning("%s could not be read (%s); checking the words again", LANGUAGE_FILE, exc)
            self.saved, self.changed = {}, True

    def finnish(self, song: "state.Song") -> bool:
        cleaned = song.cleaned_path()
        fingerprint = state.file_fingerprint(cleaned) if cleaned else None
        kept = self.saved.get(song.slug)
        if isinstance(kept, dict) and kept.get("fingerprint") == fingerprint \
                and isinstance(kept.get("finnish"), bool):
            return kept["finnish"]
        try:
            words = language_check.score_words(cleaned) if fingerprint else []
            finnish = bool(words) and not language_check.looks_foreign(words)
        except Exception as exc:   # noqa: BLE001 - any unreadable score is "not known to be Finnish"
            log.warning("could not read the words of %s: %s", song.slug, exc)
            finnish = False
        self.saved[song.slug] = {"fingerprint": fingerprint, "finnish": finnish}
        self.changed = True
        return finnish

    def write(self) -> None:
        if not self.changed:
            return
        try:
            _write_json(self.songs_dir, LANGUAGE_FILE, self.saved)
        except OSError as exc:
            log.warning("could not write %s: %s", LANGUAGE_FILE, exc)


def overview(songs: List["state.Song"], listed: Dict, version: int,
             changes: Optional[Dict] = None, songs_dir: Optional[str] = None) -> Dict:
    """What the song list shows: per song, the choirs it is pending in, those it is up to date
    in, and its last run; plus the totals."""
    words = WordsCheck(songs_dir or state.SONGS_DIR)
    staging = set(listed.get("staging") or [])
    by_slug: Dict[str, List[Dict]] = {}
    for row in listed.get("songs") or []:
        if row.get("choir") not in staging:
            by_slug.setdefault(row.get("slug"), []).append(row)
    out = {}
    for song in songs:
        records = {c: r for c, r in publish.records(song.data.get("publish")).items() if c not in staging}
        status = song_status(by_slug.get(song.slug, []), records, version, listed.get("at"),
                             changes, song.language, lambda song=song: words.finnish(song))
        status["run"] = progress.read(state.song_dir(song.slug))
        if status["pending"] or status["current"] or status["run"]:
            out[song.slug] = status
    words.write()
    return {
        "version": version,
        "site": {"error": listed.get("error"), "at": listed.get("at")},
        "pending": sum(1 for s in out.values() if s["pending"]),
        "running": sum(1 for s in out.values()
                       if s["run"] and s["run"].get("state") in ("waiting",) + progress.RUNNING),
        "songs": out,
    }
