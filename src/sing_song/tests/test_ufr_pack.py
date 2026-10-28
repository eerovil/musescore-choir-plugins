import os
import zipfile

import pytest

from src.sing_song import ufr_pack

# the UFR V1.0 download's files the singer reads, and some it does not, below UFR-V1.0/UFR-V1.0/
PACK = {
    "0_CORE/dsacoustic/millefeuille_v001.onnx": "acoustic",
    "0_CORE/dsacoustic/millefeuille_v001.phonemes.json": "{}",
    "0_CORE/dsacoustic/millefeuille_v001.languages.json": "{}",
    "0_CORE/dsacoustic/dsconfig.yaml": "unused",
    "0_CORE/dsvocoder/potluck/potluck_vocoder_v100.onnx": "potluck",
    "0_CORE/dsvocoder/nsf/pc_nsf_hifigan_44.1k_hop512_128bin_2025.02.onnx": "unused",
    "0_CORE/dsdur/millefeuille_v001.dur.onnx": "dur",
    "0_CORE/dsdur/millefeuille_v001.linguistic.onnx": "ling",
    "0_CORE/dspitch/millefeuille_v001.pitch.onnx": "unused",
    "UFRV01_LYSE/dsacoustic/millefeuille_v001.lyse_core.emb": "lyse",
    "UFRV01_LYSE/dsdur/embs/millefeuille_v001.lyse_core.emb": "lyse dur",
    "UFRV01_LYSE/dspitch/embs/millefeuille_v001.lyse_core.emb": "unused",
    "UFRV02_REMY/dsacoustic/millefeuille_v001.remy_adult.emb": "remy",
    "UFRV02_REMY/dsdur/embs/millefeuille_v001.remy_adult.emb": "remy dur",
    "UFRV02_REMY/dsdur/dsdict-en.yaml": "unused",
    "0_PHONEMIZERS/DIFF_PL_phonemiser.dll": "unused",
}
OURS = {
    "core/acoustic.onnx": "acoustic", "core/phonemes.json": "{}", "core/languages.json": "{}",
    "core/potluck.onnx": "potluck",
    "dsdur/millefeuille_v001.dur.onnx": "dur", "dsdur/millefeuille_v001.linguistic.onnx": "ling",
    "dsdur/millefeuille_v001.lyse_core.emb": "lyse dur", "dsdur/millefeuille_v001.remy_adult.emb": "remy dur",
    "tiger/lyse_core.emb": "lyse", "tiger/remy_adult.emb": "remy",
}


def tree(path):
    return {str(p.relative_to(path)): p.read_text() for p in path.rglob("*") if p.is_file()}


def as_zip(tmp_path, files):
    z = tmp_path / "UFR-V1.0.zip"
    with zipfile.ZipFile(z, "w") as w:
        for name, text in files.items():
            w.writestr(f"UFR-V1.0/UFR-V1.0/{name}", text)
    return z


def test_the_zip_as_it_downloads_becomes_the_singers_folder(tmp_path):
    out = tmp_path / "ufr"
    ufr_pack.build(str(as_zip(tmp_path, PACK)), str(out))
    assert tree(out) == OURS
    assert ufr_pack.is_ours(str(out))


def test_the_unzipped_folder_too_and_it_is_recognised(tmp_path):
    for name, text in PACK.items():
        (tmp_path / "dl" / "UFR-V1.0" / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "dl" / "UFR-V1.0" / name).write_text(text)
    assert ufr_pack.is_download(str(tmp_path / "dl")) and not ufr_pack.is_ours(str(tmp_path / "dl"))
    ufr_pack.build(str(tmp_path / "dl"), str(tmp_path / "ufr"))
    assert tree(tmp_path / "ufr") == OURS


def test_a_rebuild_replaces_the_old_folder_whole(tmp_path):
    out = tmp_path / "ufr"
    (out / "core").mkdir(parents=True)
    (out / "core" / "phonemes.json").write_text("old")
    (out / "tiger").mkdir()
    (out / "tiger" / "gone.emb").write_text("old")
    ufr_pack.build(str(as_zip(tmp_path, PACK)), str(out))
    assert tree(out) == OURS


def test_it_never_replaces_a_folder_that_is_not_a_voice_folder(tmp_path):
    out = tmp_path / "home"
    out.mkdir()
    (out / "notes.txt").write_text("mine")
    with pytest.raises(SystemExit, match="holds something else"):
        ufr_pack.build(str(as_zip(tmp_path, PACK)), str(out))
    assert tree(out) == {"notes.txt": "mine"}


def test_a_pack_without_a_voice_the_singer_needs_is_refused(tmp_path):
    files = {n: t for n, t in PACK.items() if "remy_adult" not in n}
    with pytest.raises(SystemExit, match="tiger/remy_adult.emb"):
        ufr_pack.build(str(as_zip(tmp_path, files)), str(tmp_path / "ufr"))
    assert not (tmp_path / "ufr").exists()


def test_not_the_pack_at_all(tmp_path):
    with pytest.raises(SystemExit, match="no 0_CORE"):
        ufr_pack.build(str(as_zip(tmp_path, {"readme.txt": "x"})), str(tmp_path / "ufr"))


def test_a_folder_beside_it_named_like_a_scratch_folder_is_left_alone(tmp_path):
    (tmp_path / "ufr.new").mkdir()
    (tmp_path / "ufr.new" / "mine.txt").write_text("mine")
    ufr_pack.build(str(as_zip(tmp_path, PACK)), str(tmp_path / "ufr"))
    assert tree(tmp_path / "ufr.new") == {"mine.txt": "mine"}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["UFR-V1.0.zip", "ufr", "ufr.new"]


def test_a_failed_swap_keeps_the_old_voices(tmp_path, monkeypatch):
    out = tmp_path / "ufr"
    (out / "core").mkdir(parents=True)
    (out / "core" / "phonemes.json").write_text("old")
    real = ufr_pack.os.replace

    def replace(a, b):
        if str(a).endswith("new"):
            raise OSError("disk full")
        real(a, b)
    monkeypatch.setattr(ufr_pack.os, "replace", replace)
    with pytest.raises(OSError):
        ufr_pack.build(str(as_zip(tmp_path, PACK)), str(out))
    assert tree(out) == {"core/phonemes.json": "old"}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["UFR-V1.0.zip", "ufr"]


def test_a_failed_copy_keeps_the_old_voices(tmp_path, monkeypatch):
    out = tmp_path / "ufr"
    (out / "core").mkdir(parents=True)
    (out / "core" / "phonemes.json").write_text("old")
    monkeypatch.setattr(ufr_pack.shutil, "copyfileobj", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        ufr_pack.build(str(as_zip(tmp_path, PACK)), str(out))
    assert tree(out) == {"core/phonemes.json": "old"}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["UFR-V1.0.zip", "ufr"]


def test_a_failed_restore_keeps_the_old_voices_and_says_where(tmp_path, monkeypatch):
    out = tmp_path / "ufr"
    (out / "core").mkdir(parents=True)
    (out / "core" / "phonemes.json").write_text("old")
    real = ufr_pack.os.replace

    def replace(a, b):
        if str(b) == str(out):   # neither the new voices nor the old ones go back in
            raise OSError("disk gone")
        real(a, b)
    monkeypatch.setattr(ufr_pack.os, "replace", replace)
    with pytest.raises(SystemExit) as e:
        ufr_pack.build(str(as_zip(tmp_path, PACK)), str(out))
    kept = [p for p in tmp_path.iterdir() if p.name.startswith(".ufr-old-")]
    assert len(kept) == 1 and str(kept[0] / "voices") in str(e.value)
    assert tree(kept[0] / "voices") == {"core/phonemes.json": "old"}
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".ufr-") and p not in kept]


def test_set_env_keeps_the_files_permissions_and_leaves_its_neighbours(tmp_path):
    env = tmp_path / ".env"
    env.write_text('A="1"\nUFR_DIR=""\nTOKEN="secret"\n')
    env.chmod(0o600)
    (tmp_path / ".env.new").write_text("mine")
    old_mask = os.umask(0o022)
    try:
        ufr_pack.set_env(str(env), "UFR_DIR", "/v/ufr")
    finally:
        os.umask(old_mask)
    assert env.read_text() == 'A="1"\nUFR_DIR="/v/ufr"\nTOKEN="secret"\n'
    assert env.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / ".env.new").read_text() == "mine"
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env", ".env.new"]


def test_set_env_adds_the_line_when_there_is_none(tmp_path):
    env = tmp_path / ".env"
    env.write_text('A="1"\n')
    ufr_pack.set_env(str(env), "UFR_DIR", "/v/ufr")
    assert env.read_text() == 'A="1"\nUFR_DIR="/v/ufr"\n'
