"""The bounds editor's routes: read, correct, store, crop.

Boundaries are proposed by an AI and corrected by a person, so the contract that
matters is that a correction round-trips and that the server -- not the browser --
decides indices and measure ranges.
"""
import os
import shutil

import pytest

pytest.importorskip("PIL")
pytest.importorskip("httpx")
if not shutil.which("pdftoppm"):
    pytest.skip("pdftoppm (poppler) is not installed", allow_module_level=True)

from fastapi.testclient import TestClient

from src.song_app import pdf_systems, server, state

FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "fixtures", "virta-venhetta-vie",
)
SLUG = "virta-venhetta-vie"


@pytest.fixture
def client(tmp_path, monkeypatch):
    songs = tmp_path / "songs"
    dest = songs / SLUG
    dest.mkdir(parents=True)
    for stage in ("00-registered", "10-cleaned"):
        for name in os.listdir(os.path.join(FIXTURE, stage)):
            shutil.copyfile(os.path.join(FIXTURE, stage, name), dest / name)
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    # The score preview is not under test; keep MuseScore out of it.
    monkeypatch.setenv("MUSESCORE_CLI_PATH", str(tmp_path / "no-such-musescore"))
    return TestClient(server.app)


def test_bounds_come_back_with_the_score_count_to_check_against(client):
    r = client.get(f"/api/songs/{SLUG}/bounds")
    assert r.status_code == 200
    data = r.json()
    assert data["pages"] == 4
    assert len(data["systems"]) == 15
    assert data["declared"] == 15                 # so the editor can say "matches"
    assert data["systems"][0]["measure_start"] == 1


def test_a_correction_round_trips_and_is_reindexed_by_the_server(client):
    bands = client.get(f"/api/songs/{SLUG}/bounds").json()["systems"]
    edited = [{"page": b["page"], "top": b["top"], "bottom": b["bottom"]} for b in bands]
    edited[0]["top"] = 0.05                        # drag the first boundary up
    edited.reverse()                               # and hand them back out of order

    saved = client.put(f"/api/songs/{SLUG}/bounds", json={"systems": edited}).json()["systems"]
    assert [b["index"] for b in saved] == list(range(1, 16))     # server re-indexed
    assert saved[0]["top"] == 0.05
    assert saved[0]["measure_start"] == 1                        # still labelled

    again = client.get(f"/api/songs/{SLUG}/bounds").json()["systems"]
    assert again == saved                                        # persisted


def test_dropping_a_system_stops_the_measure_labelling(client):
    """15 bands label; 14 must not, or lyrics land on the wrong measures."""
    bands = client.get(f"/api/songs/{SLUG}/bounds").json()["systems"]
    fewer = [{"page": b["page"], "top": b["top"], "bottom": b["bottom"]} for b in bands[:-1]]
    saved = client.put(f"/api/songs/{SLUG}/bounds", json={"systems": fewer}).json()["systems"]
    assert len(saved) == 14
    assert all(b["measure_start"] == 0 for b in saved)


def test_bad_input_is_refused_rather_than_stored(client):
    before = client.get(f"/api/songs/{SLUG}/bounds").json()["systems"]
    assert client.put(f"/api/songs/{SLUG}/bounds", json={}).status_code == 400
    assert client.put(f"/api/songs/{SLUG}/bounds",
                      json={"systems": [{"page": 1}]}).status_code == 400
    assert client.get(f"/api/songs/{SLUG}/bounds").json()["systems"] == before


def test_pages_and_system_crops_are_served_as_images(client):
    page = client.get(f"/api/songs/{SLUG}/page/1?dpi=100")
    assert page.status_code == 200 and page.headers["content-type"] == "image/png"
    assert client.get(f"/api/songs/{SLUG}/page/9").status_code == 404

    crop = client.get(f"/api/songs/{SLUG}/system/8?dpi=100")
    assert crop.status_code == 200 and crop.headers["content-type"] == "image/png"
    assert client.get(f"/api/songs/{SLUG}/system/99").status_code == 404


def test_the_grid_overlay_is_available_for_reading_bounds_off(client):
    plain = client.get(f"/api/songs/{SLUG}/page/1?dpi=100").content
    grid = client.get(f"/api/songs/{SLUG}/page/1?dpi=100&grid=true").content
    assert grid != plain and len(grid) > 0


def test_the_lyric_grid_asks_per_printed_system_not_per_score(client):
    """Normal-mode cleaning strips layout breaks, so the score has no systems left.

    Without the bounds the editor offers one cell per part for all 52 measures,
    which is not an editor. With them it asks system by system, as printed.
    """
    grid = client.get(f"/api/songs/{SLUG}/lyric-grid").json()
    assert len(grid["systems"]) == 15
    assert [p["name"] for p in grid["parts"]] == ["T1", "T2", "B1", "B2"]
    assert grid["systems"][0]["start"] == 1 and grid["systems"][0]["end"] == 3

    from src.song_app import state
    os.remove(os.path.join(state.SONGS_DIR, SLUG, pdf_systems.BOUNDS_FILE))
    bare = client.get(f"/api/songs/{SLUG}/lyric-grid").json()
    assert len(bare["systems"]) == 1
    assert bare["systems"][0]["end"] == 52


def test_unlabelled_bounds_are_not_used_for_the_lyric_grid(client):
    """Bounds whose count disagrees with the score carry no measure numbers, so
    they cannot say which measures a cell covers and must not be used."""
    bands = client.get(f"/api/songs/{SLUG}/bounds").json()["systems"]
    fewer = [{"page": b["page"], "top": b["top"], "bottom": b["bottom"]} for b in bands[:-1]]
    client.put(f"/api/songs/{SLUG}/bounds", json={"systems": fewer})

    grid = client.get(f"/api/songs/{SLUG}/lyric-grid").json()
    assert len(grid["systems"]) == 1        # fell back to the score
