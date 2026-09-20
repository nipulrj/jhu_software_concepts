"""The pull pipeline: its stages, its busy flag, and its command line.

Marked ``integration``: this is the ETL flow the Pull Data button integrates,
tested a stage at a time rather than end to end.

Nothing here reaches the network.  The real scraper, the real standardizer
subprocess and the real ``Popen`` are each replaced at their own seam, and every
file the pull would write goes to ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

import doubles
import load_data
import pull_data

pytestmark = pytest.mark.integration


@pytest.fixture
def file_state(tmp_path) -> pull_data.FileState:
    """A file-backed busy flag whose two files live under ``tmp_path``."""
    return pull_data.FileState(
        status_path=tmp_path / "pull_status.json",
        lock_path=tmp_path / ".pull_data.lock",
    )


# ----------------------------------------------------------------------
# The base class
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "call",
    [
        lambda state: state.is_busy(),
        lambda state: state.begin(),
        lambda state: state.end(),
        lambda state: state.read(),
        lambda state: state.write(message="x"),
    ],
)
def test_the_base_state_refuses_to_pretend(call):
    """A half-implemented backend fails loudly, not silently as "not busy"."""
    with pytest.raises(NotImplementedError):
        call(pull_data.PullState())


# ----------------------------------------------------------------------
# MemoryState
# ----------------------------------------------------------------------
def test_memory_state_starts_idle():
    state = pull_data.MemoryState()

    assert state.is_busy() is False
    assert state.read() == pull_data.IDLE_STATUS


def test_memory_state_can_start_busy():
    """The one-line way a test arranges "a pull is in progress"."""
    assert pull_data.MemoryState(busy=True).is_busy() is True


def test_memory_state_claims_and_releases():
    state = pull_data.MemoryState()

    assert state.begin() is True
    assert state.is_busy() is True
    assert state.begin() is False, "a second claim must be refused"

    state.end()
    assert state.is_busy() is False


def test_memory_state_merges_status_fields():
    """Writing one field leaves the others alone, and stamps the time."""
    state = pull_data.MemoryState()

    state.write(state="running", stage="scraping")
    state.write(message="halfway")

    status = state.read()
    assert status["stage"] == "scraping"
    assert status["message"] == "halfway"
    assert status["updated_at"].startswith("20")


def test_memory_state_read_returns_a_copy():
    """A caller cannot edit the state by editing what it was handed."""
    state = pull_data.MemoryState()

    state.read()["message"] = "tampered"

    assert state.read()["message"] == pull_data.IDLE_STATUS["message"]


def test_memory_state_accepts_a_starting_status():
    state = pull_data.MemoryState(status={"state": "done", "message": "earlier run"})

    assert state.read()["message"] == "earlier run"


def test_snapshot_adds_the_running_flag():
    """What ``GET /status`` answers: the status, plus whether a pull is on."""
    state = pull_data.MemoryState(busy=True)

    assert state.snapshot()["running"] is True


# ----------------------------------------------------------------------
# FileState
# ----------------------------------------------------------------------
def test_file_state_starts_idle(file_state):
    """No files yet, so nothing is running and nothing has happened."""
    assert file_state.is_busy() is False
    assert file_state.read() == pull_data.IDLE_STATUS


def test_file_state_writes_the_lock_and_removes_it(file_state):
    """The flag is a file holding this process's id, so another process sees it."""
    assert file_state.begin() is True
    assert file_state.lock_path.read_text(encoding="utf-8") == str(os.getpid())
    assert file_state.is_busy() is True

    file_state.end()
    assert not file_state.lock_path.exists()


def test_file_state_refuses_a_second_claim(file_state):
    file_state.begin()

    assert file_state.begin() is False


def test_file_state_ending_twice_is_harmless(file_state):
    """A crash between claim and release must not need manual cleanup."""
    file_state.begin()
    file_state.end()
    file_state.end()


def test_file_state_status_survives_a_new_object(file_state, tmp_path):
    """The point of a file: a Flask restart does not forget a running pull."""
    file_state.write(state="running", message="scraping")

    reopened = pull_data.FileState(
        status_path=file_state.status_path, lock_path=file_state.lock_path
    )
    assert reopened.read()["message"] == "scraping"


def test_file_state_writes_the_status_atomically(file_state):
    """The temporary file is renamed, so a reader never sees half a document."""
    file_state.write(state="running")

    assert file_state.status_path.exists()
    assert not file_state.status_path.with_suffix(".tmp").exists()
    assert json.loads(file_state.status_path.read_text(encoding="utf-8"))["state"] == (
        "running"
    )


def test_file_state_tolerates_a_corrupt_status_file(file_state):
    """A truncated file reads as idle rather than raising on every page load."""
    file_state.status_path.parent.mkdir(parents=True, exist_ok=True)
    file_state.status_path.write_text("{ not json", encoding="utf-8")

    assert file_state.read() == pull_data.IDLE_STATUS


def test_file_state_tolerates_a_status_file_that_is_not_an_object(file_state):
    """A JSON array is valid JSON and still not a status."""
    file_state.status_path.parent.mkdir(parents=True, exist_ok=True)
    file_state.status_path.write_text("[1, 2, 3]", encoding="utf-8")

    assert file_state.read() == pull_data.IDLE_STATUS


def test_a_lock_left_by_a_dead_process_is_cleared(file_state, monkeypatch):
    """A pull that was killed must not disable the button forever."""
    file_state.lock_path.parent.mkdir(parents=True, exist_ok=True)
    file_state.lock_path.write_text("999999", encoding="utf-8")
    monkeypatch.setattr(pull_data, "pid_is_alive", lambda pid: False)

    assert file_state.running_pid() is None
    assert not file_state.lock_path.exists()


def test_a_lock_held_by_a_live_process_is_respected(file_state, monkeypatch):
    """Someone else's running pull is still a running pull."""
    file_state.lock_path.parent.mkdir(parents=True, exist_ok=True)
    file_state.lock_path.write_text("4242", encoding="utf-8")
    monkeypatch.setattr(pull_data, "pid_is_alive", lambda pid: True)

    assert file_state.running_pid() == 4242
    assert file_state.is_busy() is True


def test_an_unreadable_lock_is_discarded(file_state):
    """A lock file holding nonsense is cleared rather than trusted."""
    file_state.lock_path.parent.mkdir(parents=True, exist_ok=True)
    file_state.lock_path.write_text("not a pid", encoding="utf-8")

    assert file_state.running_pid() is None
    assert not file_state.lock_path.exists()


def test_begin_loses_a_race_gracefully(file_state, monkeypatch):
    """Two processes claiming at once: exactly one wins.

    ``open(..., "x")`` is what decides it, so the loser is simulated by that
    call raising, which is precisely what it does when the other process got
    there first.
    """
    monkeypatch.setattr(pull_data.Path, "exists", lambda self: False)

    def already_there(*_args, **_kwargs):
        raise FileExistsError

    monkeypatch.setattr(pull_data.Path, "open", already_there)

    assert file_state.begin() is False


# ----------------------------------------------------------------------
# pid_is_alive
# ----------------------------------------------------------------------
@pytest.mark.parametrize("pid", [0, -1])
def test_no_process_has_a_non_positive_id(pid):
    assert pull_data.pid_is_alive(pid) is False


def test_this_process_is_alive():
    """The one process we can be certain about."""
    assert pull_data.pid_is_alive(os.getpid()) is True


def test_windows_asks_the_task_list(monkeypatch):
    """On Windows there is no ``os.kill(pid, 0)``, so ``tasklist`` answers."""
    monkeypatch.setattr(pull_data.os, "name", "nt")
    monkeypatch.setattr(
        pull_data.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="python.exe 4242 ...", stderr=""),
    )

    assert pull_data.pid_is_alive(4242) is True
    assert pull_data.pid_is_alive(9999) is False


def test_an_unanswerable_task_list_assumes_alive(monkeypatch):
    """When we cannot tell, refusing to start a second scrape is the safer error."""
    monkeypatch.setattr(pull_data.os, "name", "nt")

    def fail(*_args, **_kwargs):
        raise OSError("tasklist is not on PATH")

    monkeypatch.setattr(pull_data.subprocess, "run", fail)

    assert pull_data.pid_is_alive(4242) is True


@pytest.mark.parametrize(
    "error, expected",
    [(ProcessLookupError, False), (PermissionError, True), (None, True)],
)
def test_posix_asks_the_kernel(monkeypatch, error, expected):
    """On POSIX, signal 0 asks whether the process exists.

    ``PermissionError`` means someone else's process -- but a live one.
    """
    monkeypatch.setattr(pull_data.os, "name", "posix")

    def kill(_pid, _signal):
        if error is not None:
            raise error

    monkeypatch.setattr(pull_data.os, "kill", kill, raising=False)

    assert pull_data.pid_is_alive(4242) is expected


def test_default_state_is_file_backed():
    """The running application keeps its flag where a restart cannot lose it."""
    assert isinstance(pull_data.default_state(), pull_data.FileState)


# ----------------------------------------------------------------------
# The pipeline
# ----------------------------------------------------------------------
def test_the_pipeline_runs_every_stage_in_order():
    """Scrape, clean, standardize, load -- each fed by the one before it."""
    seen = []

    def scraper():
        seen.append("scrape")
        return [{"entry_id": 1}]

    def cleaner(rows):
        seen.append("clean")
        return [dict(row, cleaned=True) for row in rows]

    def standardizer(records):
        seen.append("standardize")
        return [dict(record, standardized=True) for record in records]

    def loader(records):
        seen.append("load")
        assert records[0]["cleaned"] and records[0]["standardized"]
        return {"inserted": 1, "total": 1}

    summary = pull_data.run_pipeline(
        scraper=scraper,
        cleaner=cleaner,
        standardizer=standardizer,
        loader=loader,
        state=pull_data.MemoryState(),
    )

    assert seen == ["scrape", "clean", "standardize", "load"]
    assert summary["scraped"] == 1
    assert summary["llm_applied"] is True


def test_the_pipeline_reports_its_progress():
    """Each stage announces itself, which is what the page polls for."""
    state = pull_data.MemoryState()
    messages = []
    original = state.write

    def record(**fields):
        if "message" in fields:
            messages.append(fields["message"])
        return original(**fields)

    state.write = record

    pull_data.run_pipeline(
        scraper=doubles.FakeScraper([{"entry_id": 1}]),
        cleaner=lambda rows: rows,
        standardizer=pull_data.passthrough_standardizer,
        loader=doubles.RecordingLoader(),
        state=state,
    )

    assert any("Checking Grad Cafe" in m for m in messages)
    assert any("Cleaning 1 scraped result" in m for m in messages)
    assert any("Writing new records" in m for m in messages)
    assert state.read()["stage"] == "complete"


def test_a_pull_refuses_to_start_while_one_is_running():
    """The gate, at the pipeline rather than only at the route."""
    state = pull_data.MemoryState(busy=True)

    with pytest.raises(pull_data.PullInProgress):
        pull_data.run_pipeline(scraper=lambda: [{"entry_id": 1}], state=state)


def test_a_scrape_that_found_nothing_is_an_error():
    """Nothing scraped means something is wrong, not that there is no news."""
    state = pull_data.MemoryState()

    with pytest.raises(RuntimeError, match="returned no rows"):
        pull_data.run_pipeline(scraper=lambda: [], state=state)

    assert state.read()["state"] == "error"
    assert state.is_busy() is False


def test_a_failing_stage_records_the_failure_and_releases_the_flag():
    """A stage that raises must not leave the button disabled."""
    state = pull_data.MemoryState()

    with pytest.raises(doubles.LoaderFailure):
        pull_data.run_pipeline(
            scraper=doubles.FakeScraper([{"entry_id": 1}]),
            cleaner=lambda rows: rows,
            standardizer=pull_data.passthrough_standardizer,
            loader=doubles.FailingLoader("disk full"),
            state=state,
        )

    assert state.is_busy() is False
    assert state.read()["state"] == "error"
    assert "disk full" in state.read()["error"]


def test_skipping_the_llm_uses_the_passthrough_stage():
    """``--skip-llm`` loads without the two LLM columns and says so as it goes."""
    state = pull_data.MemoryState()
    messages = []
    original = state.write

    def record(**fields):
        messages.append(fields.get("message"))
        return original(**fields)

    state.write = record

    summary = pull_data.run_pipeline(
        skip_llm=True,
        scraper=doubles.FakeScraper([{"entry_id": 1}]),
        cleaner=lambda rows: rows,
        loader=doubles.RecordingLoader(),
        state=state,
    )

    assert summary["llm_applied"] is False
    assert "Skipping LLM standardization." in messages


def test_a_standardizer_that_declined_is_not_counted_as_applied():
    """Returning the input unchanged means it did not run."""
    summary = pull_data.run_pipeline(
        scraper=doubles.FakeScraper([{"entry_id": 1}]),
        cleaner=lambda rows: rows,
        standardizer=lambda records: records,
        loader=doubles.RecordingLoader(),
        state=pull_data.MemoryState(),
    )

    assert summary["llm_applied"] is False


# ----------------------------------------------------------------------
# The completion message
# ----------------------------------------------------------------------
def test_the_message_reports_new_records():
    summary = {"inserted": 7, "total": 19, "scraped": 12, "llm_applied": True}

    message = pull_data.describe_summary(summary)

    assert "Added 7 new records (19 now in the database)" in message
    assert "Update Analysis" in message


def test_the_message_reports_that_nothing_was_new():
    summary = {"inserted": 0, "total": 19, "scraped": 12, "llm_applied": True}

    message = pull_data.describe_summary(summary)

    assert "No new results were available" in message
    assert "all 12 entries checked" in message


def test_the_message_mentions_a_standardizer_that_did_not_run():
    summary = {"inserted": 1, "total": 1, "scraped": 1, "llm_applied": False}

    assert "LLM columns were left empty" in pull_data.describe_summary(summary)


def test_the_message_stays_quiet_when_the_llm_was_skipped_on_purpose():
    summary = {"inserted": 1, "total": 1, "scraped": 1, "llm_applied": False}

    message = pull_data.describe_summary(summary, skip_llm=True)

    assert "LLM columns" not in message


# ----------------------------------------------------------------------
# The real stage factories
# ----------------------------------------------------------------------
def test_the_default_scraper_walks_the_newest_results(monkeypatch, tmp_path):
    """The real scraper is pointed at the pull's own files and started fresh.

    Starting without the checkpoint is what makes a pull walk the *newest*
    results rather than resuming where a long scrape stopped.
    """
    import scrape

    monkeypatch.setattr(pull_data, "PULL_DIR", tmp_path)
    monkeypatch.setattr(pull_data, "PULL_RAW_PATH", tmp_path / "raw.json")
    monkeypatch.setattr(pull_data, "PULL_CHECKPOINT_PATH", tmp_path / "checkpoint.json")
    recorded = {}

    class StubScraper:
        def scrape_data(self, target_entries, resume):
            recorded["target"] = target_entries
            recorded["resume"] = resume
            return [{"entry_id": 1}]

    monkeypatch.setattr(scrape, "GradCafeScraper", StubScraper)

    rows = pull_data.make_scraper(target=25)()

    assert rows == [{"entry_id": 1}]
    assert recorded == {"target": 25, "resume": False}
    assert scrape.RAW_DATA_PATH == tmp_path / "raw.json"


def test_the_default_cleaner_is_the_real_one(sample_raw_rows):
    """No second implementation of cleaning lives in this module."""
    cleaned = pull_data.default_cleaner(sample_raw_rows)

    assert len(cleaned) == len(sample_raw_rows)
    assert cleaned[0]["term"] == "Fall 2026"


def test_the_default_loader_writes_to_postgresql(empty_database, sample_records):
    """The stage the pull uses when nothing is injected."""
    summary = pull_data.default_loader(sample_records)

    assert summary["inserted"] == len(sample_records)
    assert load_data.row_count(empty_database) == len(sample_records)


def test_the_passthrough_standardizer_returns_its_input():
    records = [{"entry_id": 1}]

    assert pull_data.passthrough_standardizer(records) is records


# ----------------------------------------------------------------------
# The standardizer subprocess
# ----------------------------------------------------------------------
def test_the_standardizer_runs_the_vendored_tool(monkeypatch, tmp_path):
    """The records go out as a file and the tool's output comes back."""
    clean_path = tmp_path / "clean.json"
    out_path = tmp_path / "extended.json"
    out_path.write_text(
        json.dumps([{"entry_id": 1, "llm-generated-program": "Computer Science"}]),
        encoding="utf-8",
    )
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(pull_data.subprocess, "run", run)

    standardize = pull_data.make_standardizer(
        workers=2, clean_path=clean_path, out_path=out_path
    )
    result = standardize([{"entry_id": 1}])

    assert result[0]["llm-generated-program"] == "Computer Science"
    assert json.loads(clean_path.read_text(encoding="utf-8")) == [{"entry_id": 1}]
    assert commands[0][0] == sys.executable
    assert "--workers" in commands[0] and "2" in commands[0]


def test_a_standardizer_that_will_not_start_returns_the_input(monkeypatch, tmp_path):
    """llama-cpp-python is optional: without it the rows still load."""

    def fail(*_args, **_kwargs):
        raise OSError("no such file")

    monkeypatch.setattr(pull_data.subprocess, "run", fail)
    records = [{"entry_id": 1}]

    standardize = pull_data.make_standardizer(
        clean_path=tmp_path / "clean.json", out_path=tmp_path / "out.json"
    )

    assert standardize(records) is records


def test_a_standardizer_that_failed_returns_the_input(monkeypatch, tmp_path, capsys):
    """A non-zero exit is reported, and the pull carries on without it."""
    monkeypatch.setattr(
        pull_data.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 3),
    )
    records = [{"entry_id": 1}]

    standardize = pull_data.make_standardizer(
        clean_path=tmp_path / "clean.json", out_path=tmp_path / "out.json"
    )

    assert standardize(records) is records
    assert "standardizer exited with 3" in capsys.readouterr().err


def test_a_standardizer_that_wrote_nothing_returns_the_input(monkeypatch, tmp_path):
    """Exit code zero but no output file is still nothing to load."""
    monkeypatch.setattr(
        pull_data.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0),
    )
    records = [{"entry_id": 1}]

    standardize = pull_data.make_standardizer(
        clean_path=tmp_path / "clean.json", out_path=tmp_path / "absent.json"
    )

    assert standardize(records) is records


# ----------------------------------------------------------------------
# Spawning the detached pull
# ----------------------------------------------------------------------
def test_spawn_pull_starts_a_child_and_returns(monkeypatch):
    """The production runner: the scrape outlives the request that started it."""
    started = {}

    def popen(command, **kwargs):
        started["command"] = command
        started["cwd"] = kwargs["cwd"]
        return object()

    monkeypatch.setattr(pull_data.subprocess, "Popen", popen)
    state = pull_data.MemoryState()

    status = pull_data.spawn_pull(state=state, target=250)

    assert started["command"][1].endswith("pull_data.py")
    assert started["command"][-2:] == ["--target", "250"]
    assert status["state"] == "running"
    assert "contacting Grad Cafe" in status["message"]


def test_spawn_pull_omits_a_default_target(monkeypatch):
    """The child's own default is the same one, so it need not be passed."""
    commands = []
    monkeypatch.setattr(
        pull_data.subprocess, "Popen", lambda command, **k: commands.append(command)
    )

    pull_data.spawn_pull(state=pull_data.MemoryState())

    assert "--target" not in commands[0]


def test_spawn_pull_can_be_told_which_interpreter(monkeypatch):
    commands = []
    monkeypatch.setattr(
        pull_data.subprocess, "Popen", lambda command, **k: commands.append(command)
    )

    pull_data.spawn_pull(state=pull_data.MemoryState(), python="/usr/bin/python3")

    assert commands[0][0] == "/usr/bin/python3"


def test_spawn_pull_defaults_to_the_file_backed_state(monkeypatch, tmp_path):
    """Called with nothing, it records into the application's own status file."""
    monkeypatch.setattr(pull_data.subprocess, "Popen", lambda *a, **k: None)
    monkeypatch.setattr(
        pull_data,
        "default_state",
        lambda: pull_data.FileState(
            status_path=tmp_path / "status.json", lock_path=tmp_path / "lock"
        ),
    )

    assert pull_data.spawn_pull()["state"] == "running"


# ----------------------------------------------------------------------
# python pull_data.py
# ----------------------------------------------------------------------
def test_main_runs_a_pull(monkeypatch, capsys):
    """The command line, which is also what the detached child runs."""
    monkeypatch.setattr(
        pull_data,
        "run_pipeline",
        lambda **kwargs: {
            "scraped": 12,
            "inserted": 12,
            "total": 12,
            "elapsed_seconds": 0.4,
        },
    )

    assert pull_data.main(["--target", "12", "--skip-llm"]) == 0
    assert "Scraped 12, inserted 12" in capsys.readouterr().err


def test_main_passes_its_arguments_through(monkeypatch):
    """``--target``, ``--workers`` and ``--skip-llm`` reach the pipeline."""
    seen = {}

    def fake_pipeline(**kwargs):
        seen.update(kwargs)
        return {"scraped": 1, "inserted": 1, "total": 1, "elapsed_seconds": 0.1}

    monkeypatch.setattr(pull_data, "run_pipeline", fake_pipeline)

    pull_data.main(["--target", "99", "--workers", "4", "--skip-llm"])

    assert seen["target"] == 99
    assert seen["workers"] == 4
    assert seen["skip_llm"] is True


def test_main_reports_a_pull_already_running(monkeypatch, capsys):
    """Exit code 2, and the running pull's own status is left alone."""

    def busy(**_kwargs):
        raise pull_data.PullInProgress("already running")

    monkeypatch.setattr(pull_data, "run_pipeline", busy)

    assert pull_data.main([]) == 2
    assert "already running" in capsys.readouterr().err


def test_main_reports_a_failed_pull(monkeypatch, capsys):
    """Exit code 1; the status was already recorded by the pipeline."""

    def fail(**_kwargs):
        raise RuntimeError("Grad Cafe is unreachable")

    monkeypatch.setattr(pull_data, "run_pipeline", fail)

    assert pull_data.main([]) == 1
    assert "[pull] failed: Grad Cafe is unreachable" in capsys.readouterr().err
