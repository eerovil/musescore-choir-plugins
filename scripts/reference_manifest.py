"""Resolve and verify the files approved as OMR references."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

MANIFEST = Path("fixtures/omr-songs.json")
SONGS = Path("songs")


@dataclass(frozen=True)
class ReferenceFiles:
    pdf: Path
    cleaned: Path


def manifest() -> dict:
    return json.loads(MANIFEST.read_text())["songs"]


def _verified(slug: str, entry: dict, kind: str) -> Path:
    path = SONGS / slug / entry[kind]
    if not path.is_file():
        raise FileNotFoundError(f"{slug}: reviewed {kind} is missing: {path}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    expected = entry[f"{kind}_sha256"]
    if actual != expected:
        raise ValueError(
            f"{slug}: reviewed {kind} changed: {path} has {actual}, expected "
            f"{expected}; regenerate fixtures/omr-songs.json and review it again"
        )
    return path


def reference_files(slug: str) -> ReferenceFiles:
    """Return the exact reviewed files, refusing stale manifest entries."""
    entry = manifest()[slug]
    return ReferenceFiles(
        pdf=_verified(slug, entry, "pdf"),
        cleaned=_verified(slug, entry, "cleaned"),
    )
