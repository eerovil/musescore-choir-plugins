import pytest


@pytest.fixture(autouse=True)
def _locks_in_tmp(tmp_path_factory, monkeypatch):
    """A run takes the song's lock and the graphics card's (#510); tests keep both off the host's
    real files, where a live run would see them."""
    where = tmp_path_factory.mktemp("locks")
    monkeypatch.setenv("SING_STATE_DIR", str(where / "sing"))
    monkeypatch.setenv("GPU_CARD_LOCK", str(where / "card.lock"))
