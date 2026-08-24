from src.song_app import job_state


def test_job_status_and_recent_logs_survive_a_reload(tmp_path):
    job_state.start(str(tmp_path), "render", "sha1:score")
    job_state.append(str(tmp_path), "render", "Engraving")
    job_state.append(str(tmp_path), "render", "42%", "progress")

    running = job_state.load(str(tmp_path))["render"]
    assert running["status"] == "running"
    assert [(line["type"], line["line"]) for line in running["logs"]] == [
        ("log", "Engraving"), ("progress", "42%")]

    job_state.finish(str(tmp_path), "render")
    assert job_state.load(str(tmp_path))["render"]["status"] == "succeeded"


def test_job_logs_are_bounded_and_running_jobs_are_interrupted_on_restart(tmp_path):
    job_state.start(str(tmp_path), "clean")
    for index in range(job_state.LOG_LIMIT + 5):
        job_state.append(str(tmp_path), "clean", str(index))
    assert len(job_state.load(str(tmp_path))["clean"]["logs"]) == job_state.LOG_LIMIT

    job_state.interrupt_running(str(tmp_path))
    job = job_state.load(str(tmp_path))["clean"]
    assert job["status"] == "failed"
    assert "restarted" in job["error"].lower()


def test_conflicting_jobs_are_started_atomically(tmp_path):
    directory = str(tmp_path)
    assert job_state.start_if_idle(directory, "clean", ("clean", "render"))
    assert job_state.start_if_idle(directory, "render", ("clean", "render")) is None
    job_state.finish(directory, "clean")
    assert job_state.start_if_idle(directory, "render", ("clean", "render"))
