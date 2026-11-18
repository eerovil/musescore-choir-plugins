"""#510: Sing and publish and Try start sing_song.py as a systemd user unit off a snapshot of the
code. A fake systemd-run and systemctl on PATH stand in for the user manager."""
import json
import os
import shutil
import stat
import sys

import pytest
from lxml import etree

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from src.sing_song import progress
from src.song_app import job_state, publish, server, sing_run, state

FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "fixtures", "virta-venhetta-vie",
)
SLUG = "virta-venhetta-vie"

FAKE = """#!{python}
import json, os, sys
calls = os.environ["FAKE_CALLS"]
with open(calls, "a") as f:
    f.write(json.dumps([os.path.basename(sys.argv[0])] + sys.argv[1:]) + "\\n")
args = sys.argv[1:]
active = os.path.join(os.path.dirname(calls), "active")
if os.path.basename(sys.argv[0]) == "systemd-run":
    if os.environ.get("FAKE_RUN_FAILS"):
        sys.stderr.write("Unit already exists\\n"); sys.exit(1)
    unit = args[args.index("--unit") + 1]
    open(os.path.join(active, unit), "w").close()
    sys.exit(0)
if args[1] == "is-active":
    up = os.path.exists(os.path.join(active, args[2]))
    print("active" if up else "inactive"); sys.exit(0 if up else 3)
if args[1] == "stop":
    try: os.remove(os.path.join(active, args[2]))
    except FileNotFoundError: pass
    sys.exit(0)
if args[1] == "show-environment":
    print("DISPLAY=:0\\nXAUTHORITY=/run/user/1000/xauth\\nHOME=/home/x"); sys.exit(0)
sys.exit(0)
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    songs = tmp_path / "songs"
    dest = songs / SLUG
    dest.mkdir(parents=True)
    for stage in ("00-registered", "10-cleaned"):
        for name in os.listdir(os.path.join(FIXTURE, stage)):
            shutil.copyfile(os.path.join(FIXTURE, stage, name), dest / name)
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "fake" / "active").mkdir(parents=True)
    for name in ("systemd-run", "systemctl"):
        path = bin_dir / name
        path.write_text(FAKE.format(python=sys.executable))
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CALLS", str(tmp_path / "fake" / "calls"))
    monkeypatch.setenv("SING_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(sing_run, "CODE_DIR", str(tmp_path / "code"))
    monkeypatch.setattr(sing_run, "head", lambda: "abc123")
    snaps = []
    monkeypatch.setattr(sing_run, "_snapshot", lambda sha: snaps.append(sha) or str(tmp_path / "code" / sha))
    monkeypatch.setattr(publish.Config, "from_env", classmethod(lambda cls: object()))
    monkeypatch.setattr(publish, "choirs", lambda *a: {"error": "", "choirs": [{"id": "testi"}]})
    c = TestClient(server.app)
    c.tmp, c.dir, c.snaps = tmp_path, dest, snaps
    return c


def calls(c, tool=None):
    path = c.tmp / "fake" / "calls"
    lines = [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []
    return [l for l in lines if tool is None or l[0] == tool]


def finish(c, unit):
    os.remove(c.tmp / "fake" / "active" / unit)


def test_sing_starts_a_unit_off_the_snapshot_and_writes_app_json_after(client):
    r = client.post(f"/api/songs/{SLUG}/sing", json={"choir": "testi"})
    assert r.status_code == 200, r.text
    (argv,) = [a for a in calls(client, "systemd-run")]
    assert argv[argv.index("--unit") + 1] == f"sing-{SLUG}"
    assert "--collect" in argv and "RuntimeMaxSec=3h" in argv
    snap = str(client.tmp / "code" / "abc123")
    assert f"--working-directory={snap}" in argv
    assert "--setenv=DISPLAY=:0" in argv and "--setenv=XAUTHORITY=/run/user/1000/xauth" in argv
    assert not any(a.startswith("--setenv=HOME") for a in argv)
    run_id = r.json()["run_id"]
    assert r.json()["state"] == "starting" and r.json()["choir"] == "testi"   # read as a status
    assert f"--setenv=SING_RUN_ID={run_id}" in argv
    # the unit reads the same song lock and state files as the app
    assert f"--setenv=SING_STATE_DIR={client.tmp / 'state'}" in argv
    tail = argv[argv.index("--") + 1:]
    assert tail[2:] == [os.path.join(snap, "sing_song.py"), SLUG, "--publish", "testi"]
    log = os.path.join(progress.state_dir(SLUG), "run.log")
    assert f"StandardOutput=truncate:{log}" in argv and f"StandardError=append:{log}" in argv
    app = json.load(open(os.path.join(progress.state_dir(SLUG), "app.json")))
    assert app["commit"] == "abc123" and app["choir"] == "testi" and app["run_id"] == run_id
    assert not os.path.exists(client.dir / progress.RUN_FILE)   # the app writes nothing in songs/
    assert client.get(f"/api/songs/{SLUG}/sing").json()["state"] == "starting"


def test_a_second_start_a_cli_run_and_a_busy_job_are_409(client):
    song = state.load(SLUG)
    song.data["review"] = {"approved_against": state.file_fingerprint(song.cleaned_path())}
    song.save()
    assert client.post(f"/api/songs/{SLUG}/sing", json={"choir": "testi"}).status_code == 200
    r = client.post(f"/api/songs/{SLUG}/sing", json={"choir": "testi"})
    assert r.status_code == 409 and "already singing" in r.text
    finish(client, f"sing-{SLUG}")
    fd = progress.run_lock(SLUG)   # an agent's sing_song.py run holds the song
    try:
        r = client.post(f"/api/songs/{SLUG}/sing", json={"choir": "testi"})
        assert r.status_code == 409 and "Another sing_song run" in r.text
    finally:
        os.close(fd)
    song = state.load(SLUG)
    state.start_job(song, "clean", server.WRITERS)
    try:
        assert client.post(f"/api/songs/{SLUG}/sing", json={"choir": "testi"}).status_code == 409
    finally:
        job_state.finish(song.dir, "clean")
    assert len(calls(client, "systemd-run")) == 1


def test_publish_is_refused_while_the_song_sings(client, monkeypatch):
    monkeypatch.setattr(publish, "choirs", lambda *a: {"error": "", "choirs": [{"id": "testi"}]})
    song = state.load(SLUG)
    song.data["review"] = {"approved_against": state.file_fingerprint(song.cleaned_path())}
    song.save()
    assert client.post(f"/api/songs/{SLUG}/sing", json={"choir": "testi"}).status_code == 200
    r = client.post(f"/api/songs/{SLUG}/publish", json={"choir": "testi", "settings": {}})
    assert r.status_code == 409 and "already singing" in r.text


def test_bad_input_and_a_failed_start(client, monkeypatch):
    assert client.post(f"/api/songs/{SLUG}/sing", json={}).status_code == 400
    assert client.post(f"/api/songs/{SLUG}/sing", json={"choir": "a b"}).status_code == 400
    monkeypatch.setenv("FAKE_RUN_FAILS", "1")
    r = client.post(f"/api/songs/{SLUG}/sing", json={"choir": "testi"})
    assert r.status_code == 502 and "Unit already exists" in r.text
    assert not os.path.exists(os.path.join(progress.state_dir(SLUG), "app.json"))
    assert client.get(f"/api/songs/{SLUG}/sing").json() == {"state": "none"}


def test_sing_checks_the_choir_on_the_site_before_starting(client, monkeypatch):
    r = client.post(f"/api/songs/{SLUG}/sing", json={"choir": "jm"})   # not on the site
    assert r.status_code == 400 and "testi" in r.text
    monkeypatch.setattr(publish, "choirs", lambda *a: {"error": "offline", "choirs": []})
    r = client.post(f"/api/songs/{SLUG}/sing", json={"choir": "testi"})
    assert r.status_code == 502 and "offline" in r.text
    assert calls(client, "systemd-run") == []
    assert client.get(f"/api/songs/{SLUG}/sing").json() == {"state": "none"}


def test_status_follows_the_record_of_this_run_only(client):
    run_id = client.post(f"/api/songs/{SLUG}/sing", json={"choir": "testi"}).json()["run_id"]
    progress.write(str(client.dir), {"state": "singing", "run_id": "someone-else", "pid": os.getpid()})
    assert client.get(f"/api/songs/{SLUG}/sing").json()["state"] == "starting"
    progress.write(str(client.dir), {"state": "singing", "run_id": run_id, "pid": os.getpid(),
                                     "part": 2, "parts": 4})
    st = client.get(f"/api/songs/{SLUG}/sing").json()
    assert st["state"] == "singing" and st["run"]["part"] == 2
    progress.write(str(client.dir), {"state": "done", "run_id": run_id, "choirs": ["testi"], "finished": 1})
    finish(client, f"sing-{SLUG}")
    assert client.get(f"/api/songs/{SLUG}/sing").json()["state"] == "done"


def test_a_unit_gone_without_a_record_is_failed_with_the_log(client):
    client.post(f"/api/songs/{SLUG}/sing", json={"choir": "testi"})
    with open(os.path.join(progress.state_dir(SLUG), "run.log"), "w") as f:
        f.write("starting\nTraceback: boom\n")
    finish(client, f"sing-{SLUG}")
    st = client.get(f"/api/songs/{SLUG}/sing").json()
    assert st["state"] == "failed" and st["log"][-1] == "Traceback: boom"


def test_stop_stops_the_unit(client):
    client.post(f"/api/songs/{SLUG}/sing", json={"choir": "testi"})
    r = client.post(f"/api/songs/{SLUG}/sing/stop")
    assert r.status_code == 200
    assert ["systemctl", "--user", "stop", f"sing-{SLUG}"] in calls(client, "systemctl")
    assert r.json()["state"] == "failed"


def test_a_remote_runner_in_the_checkout_is_pointed_into_the_snapshot(client, monkeypatch):
    monkeypatch.setenv("REMOTE_RUNNER", os.path.join(sing_run.CHECKOUT, "scripts", "ssh-runner.sh"))
    client.post(f"/api/songs/{SLUG}/sing", json={"choir": "testi"})
    (argv,) = calls(client, "systemd-run")
    snap = str(client.tmp / "code" / "abc123")
    assert f"--setenv=REMOTE_RUNNER={os.path.join(snap, 'scripts', 'ssh-runner.sh')}" in argv
    assert sing_run._remote_runner(snap) == os.path.join(snap, "scripts", "ssh-runner.sh")
    monkeypatch.setenv("REMOTE_RUNNER", "/usr/local/bin/elsewhere")
    assert sing_run._remote_runner(snap) is None
    monkeypatch.setenv("REMOTE_RUNNER", "")
    assert sing_run._remote_runner(snap) is None


def test_try_sings_into_its_own_folder_and_serves_the_mp3(client):
    r = client.post(f"/api/songs/{SLUG}/sing/try", json={"part": "T1", "first": 3, "last": 4})
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["state"] == "singing" and st["part"] == "T1"
    (argv,) = calls(client, "systemd-run")
    assert argv[argv.index("--unit") + 1] == f"sing-{SLUG}-try"
    folder = os.path.join(progress.state_dir(SLUG), "try", st["id"])
    assert argv[-5:] == [SLUG, "--try", "T1:3-4", "--out", folder]
    assert client.post(f"/api/songs/{SLUG}/sing/try",
                       json={"part": "T1", "first": 3, "last": 4}).status_code == 409
    with open(os.path.join(folder, "log"), "w") as f:
        f.write("waiting for the graphics card\n")
    assert client.get(f"/api/songs/{SLUG}/sing/try").json()["state"] == "waiting"
    with open(os.path.join(folder, "log"), "a") as f:
        f.write("got the graphics card\n")
    assert client.get(f"/api/songs/{SLUG}/sing/try").json()["state"] == "singing"
    os.makedirs(os.path.join(folder, "try"))
    with open(os.path.join(folder, "try", "1-T1-bars-3-4.mp3"), "wb") as f:
        f.write(b"ID3fake")
    with open(os.path.join(folder, "log"), "a") as f:
        f.write(f"Trial: {folder}/try/1-T1-bars-3-4.mp3 (4.5 s of music, sung in 12.0 s)\n")
    finish(client, f"sing-{SLUG}-try")
    st = client.get(f"/api/songs/{SLUG}/sing/try").json()
    assert st["state"] == "done" and st["mp3"] == "1-T1-bars-3-4.mp3"
    assert st["music"] == 4.5 and st["took"] == 12.0
    mp3 = client.get(f"/api/songs/{SLUG}/sing/try/1-T1-bars-3-4.mp3")
    assert mp3.status_code == 200 and mp3.content == b"ID3fake"
    assert mp3.headers["content-type"] == "audio/mpeg"
    for bad in ("..%2Flog.mp3", ".hidden.mp3", "log", "x.wav"):
        assert client.get(f"/api/songs/{SLUG}/sing/try/{bad}").status_code == 404
    assert not os.path.exists(client.dir / ".sing-try")


def test_try_refuses_bad_input_and_says_a_failed_one(client):
    for body in ({}, {"part": "T1"}, {"part": "T1:x", "first": 1}, {"part": "T1", "first": 5, "last": 2},
                 {"part": "T1", "first": 0}):
        assert client.post(f"/api/songs/{SLUG}/sing/try", json=body).status_code == 400, body
    st = client.post(f"/api/songs/{SLUG}/sing/try", json={"part": "B2", "first": 9}).json()
    folder = os.path.join(progress.state_dir(SLUG), "try", st["id"])
    with open(os.path.join(folder, "log"), "w") as f:
        f.write("no part named B2\n")
    finish(client, f"sing-{SLUG}-try")
    st = client.get(f"/api/songs/{SLUG}/sing/try").json()
    assert st["state"] == "failed" and st["log"] == ["no part named B2"] and st["last"] == 9


def test_only_the_last_three_tries_are_kept(client):
    ids = []
    for n in range(5):
        st = client.post(f"/api/songs/{SLUG}/sing/try", json={"part": "T1", "first": n + 1}).json()
        ids.append(st["id"])
        finish(client, f"sing-{SLUG}-try")
    root = os.path.join(progress.state_dir(SLUG), "try")
    assert sorted(n for n in os.listdir(root) if n.isdigit()) == sorted(ids[-3:])


def test_the_pronunciation_reply_lists_the_parts(client, monkeypatch):
    monkeypatch.setattr(server, "_readings", lambda words: {})
    monkeypatch.setattr(server, "_pack_sounds", lambda: None)
    parts = client.get(f"/api/songs/{SLUG}/pronunciation").json()["parts"]
    assert parts == [{"n": 1, "name": "T1", "staves": ["1"]}, {"n": 2, "name": "T2", "staves": ["2"]},
                     {"n": 3, "name": "B1", "staves": ["3"]}, {"n": 4, "name": "B2", "staves": ["4"]}]


def test_parts_keep_their_staves_in_a_musescore_4_score(client, monkeypatch):
    monkeypatch.setattr(server, "_readings", lambda words: {})
    monkeypatch.setattr(server, "_pack_sounds", lambda: None)
    cleaned = state.load(SLUG).cleaned_path()
    tree = etree.parse(cleaned)
    for stub in tree.getroot().find("Score").iter("Part"):   # MuseScore 4 writes no id here
        for staff in stub.findall("Staff"):
            staff.attrib.pop("id", None)
    tree.write(cleaned)
    parts = client.get(f"/api/songs/{SLUG}/pronunciation").json()["parts"]
    assert [p["staves"] for p in parts] == [["1"], ["2"], ["3"], ["4"]]


def test_try_is_refused_while_a_job_or_a_full_run_holds_the_song(client):
    body = {"part": "#1", "first": 3, "last": 4}
    song = state.load(SLUG)
    state.start_job(song, "clean", server.WRITERS)   # it would sing a score being rewritten
    try:
        r = client.post(f"/api/songs/{SLUG}/sing/try", json=body)
        assert r.status_code == 409 and "job is working" in r.text
    finally:
        job_state.finish(song.dir, "clean")
    open(client.tmp / "fake" / "active" / f"sing-{SLUG}", "w").close()   # Sing and publish runs
    r = client.post(f"/api/songs/{SLUG}/sing/try", json=body)
    assert r.status_code == 409 and "Try once it is done" in r.text
    finish(client, f"sing-{SLUG}")
    fd = progress.run_lock(SLUG)   # an agent's sing_song.py run holds the song
    try:
        r = client.post(f"/api/songs/{SLUG}/sing/try", json=body)
        assert r.status_code == 409 and "Another sing_song run" in r.text
    finally:
        os.close(fd)
    assert calls(client, "systemd-run") == []
    assert client.post(f"/api/songs/{SLUG}/sing/try", json=body).status_code == 200


def test_try_by_number_keeps_two_parts_called_alike_apart(client, monkeypatch):
    monkeypatch.setattr(server, "_readings", lambda words: {})
    monkeypatch.setattr(server, "_pack_sounds", lambda: None)
    cleaned = state.load(SLUG).cleaned_path()
    with open(cleaned, encoding="utf-8") as f:
        text = f.read()
    with open(cleaned, "w", encoding="utf-8") as f:   # two parts called "Solo"
        f.write(text.replace("<trackName>T1</trackName>", "<trackName>Solo</trackName>", 1)
                    .replace("<trackName>T2</trackName>", "<trackName>Solo</trackName>", 1))
    parts = client.get(f"/api/songs/{SLUG}/pronunciation").json()["parts"]
    assert [(p["n"], p["name"]) for p in parts[:2]] == [(1, "Solo"), (2, "Solo")]
    st = client.post(f"/api/songs/{SLUG}/sing/try", json={"part": "#2", "first": 3, "last": 4}).json()
    (argv,) = calls(client, "systemd-run")
    assert argv[argv.index("--try") + 1] == "#2:3-4"
    assert st["part"] == "#2" and st["name"] == "Solo"
    finish(client, f"sing-{SLUG}-try")
    r = client.post(f"/api/songs/{SLUG}/sing/try", json={"part": "#9", "first": 3})
    assert r.status_code == 400 and "No part #9" in r.text


def test_the_snapshot_is_the_commit_with_songs_and_env_linked(tmp_path, monkeypatch):
    monkeypatch.setattr(sing_run, "CODE_DIR", str(tmp_path / "code"))
    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    os.makedirs(tmp_path / "code")
    old = tmp_path / "code" / "old"
    old.mkdir()
    os.utime(old, (0, 0))
    sha = sing_run.head()
    path = sing_run._snapshot(sha)
    assert os.path.isfile(os.path.join(path, "sing_song.py"))
    assert os.path.realpath(os.path.join(path, "songs")) == os.path.realpath(songs)
    assert not old.exists()
    assert sing_run._snapshot(sha) == path   # reused
    # gitignored host files are linked even before they exist, and again on reuse
    choirs = os.path.join(path, "choirs")
    assert os.path.islink(choirs) and os.readlink(choirs) == os.path.join(sing_run.CHECKOUT, "choirs")
    os.remove(choirs)
    sing_run._snapshot(sha)
    assert os.path.islink(choirs)
    assert os.readlink(os.path.join(path, ".env")) == os.path.join(sing_run.CHECKOUT, ".env")
