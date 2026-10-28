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
