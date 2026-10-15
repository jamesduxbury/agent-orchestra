import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from orchestra_decider.backends.static import StaticBackend
from orchestra_decider.decide import decide
from orchestra_decider.log import DecisionLog, LogError, default_state_dir
from orchestra_decider.rubrics import parse_rubric

START = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)

TIER = parse_rubric(
    {
        "decision": "tier",
        "kind": "choice",
        "instructions": "Which workflow weight?",
        "threshold": 0.3,
        "options": {"direct": "Trivial", "single": "Small", "tdd": "Complex"},
    }
)
PANEL = parse_rubric(
    {
        "decision": "panel",
        "kind": "panel",
        "instructions": "Review for {option}?",
        "threshold": 0.1,
        "baseline": "generalist",
        "options": {"security": "auth"},
    }
)
CONFIDENT_TDD = StaticBackend({"tier": {"direct": 0.02, "single": 0.03, "tdd": 0.95}})
UNSURE = StaticBackend({"tier": {"direct": 0.3, "single": 0.4, "tdd": 0.3}})


class Clock:
    def __init__(self, now: datetime = START):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def log(tmp_path: Path, clock: Clock) -> DecisionLog:
    return DecisionLog(tmp_path / "state", clock=clock)


def records(log: DecisionLog, task: str) -> list[dict]:
    return [json.loads(line) for line in (log.root / "tasks" / f"{task}.jsonl").read_text().splitlines()]


def logged_decision(log: DecisionLog, task: str, backend=CONFIDENT_TDD, rubric=TIER, state="Rewrite auth"):
    result = decide(rubric, state, backend)
    result.task = task
    log.record_decision(task, rubric, state, result)
    return result


def task_files(log: DecisionLog) -> list[str]:
    return sorted(p.stem for p in (log.root / "tasks").glob("*.jsonl"))


# Tasks and records


def test_start_task_creates_a_private_task_file(log: DecisionLog):
    task = log.start_task("Add OAuth login")

    path = log.root / "tasks" / f"{task}.jsonl"
    assert task.startswith("t_20261001_")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert records(log, task) == [
        {"type": "task_started", "task": task, "title": "Add OAuth login", "at": "2026-10-01T09:00:00+00:00"}
    ]


def test_task_ids_are_unique(log: DecisionLog):
    assert log.start_task() != log.start_task()


def test_has_task_reports_known_tasks(log: DecisionLog):
    task = log.start_task()

    assert log.has_task(task)
    assert not log.has_task("t_20261001_deadbeef")


@pytest.mark.parametrize("task", ["../../etc/passwd", "t_1", ""])
def test_rejects_malformed_task_ids(log: DecisionLog, task: str):
    with pytest.raises(LogError):
        log.finish_task(task, "accepted")


def test_record_decision_keeps_rubric_state_and_result(log: DecisionLog):
    task = log.start_task()

    result = logged_decision(log, task)

    record = records(log, task)[-1]
    assert record["type"] == "decision"
    assert record["id"] == result.id
    assert record["state"] == "Rewrite auth"
    assert record["rubric"]["options"] == {"direct": "Trivial", "single": "Small", "tdd": "Complex"}
    assert record["result"] == result.to_json()


def test_record_decision_for_unknown_task_fails(log: DecisionLog):
    result = decide(TIER, "x", CONFIDENT_TDD)

    with pytest.raises(LogError, match="unknown task"):
        log.record_decision("t_20261001_deadbeef", TIER, "x", result)


def test_record_outcome_flags_an_override(log: DecisionLog):
    task = log.start_task()
    result = logged_decision(log, task)

    assert log.record_outcome(result.id, "single") is True
    assert records(log, task)[-1] | {"at": None} == {
        "type": "outcome",
        "decision_id": result.id,
        "chosen": "single",
        "overridden": True,
        "at": None,
    }


def test_record_outcome_matching_the_model_is_not_an_override(log: DecisionLog):
    task = log.start_task()
    result = logged_decision(log, task, backend=UNSURE)

    assert log.record_outcome(result.id, "single") is False


def test_panel_outcome_compares_selections_ignoring_order(log: DecisionLog):
    task = log.start_task()
    backend = StaticBackend({"generalist": {"true": 0.9, "false": 0.1}, "security": {"true": 0.9, "false": 0.1}})
    result = logged_decision(log, task, backend=backend, rubric=PANEL)

    assert log.record_outcome(result.id, ["security", "generalist"]) is False


def test_outcome_after_backend_failure_has_no_override_signal(log: DecisionLog):
    task = log.start_task()
    result = logged_decision(log, task, backend=StaticBackend(error="offline"))

    assert log.record_outcome(result.id, "tdd") is None


@pytest.mark.parametrize("chosen", ["yolo", ["tdd", "single"], []])
def test_record_outcome_rejects_choices_outside_the_rubric(log: DecisionLog, chosen):
    task = log.start_task()
    result = logged_decision(log, task)

    with pytest.raises(LogError):
        log.record_outcome(result.id, chosen)


def test_record_outcome_for_unknown_decision_fails(log: DecisionLog):
    log.start_task()

    with pytest.raises(LogError, match="d_000000000000"):
        log.record_outcome("d_000000000000", "tdd")


def test_finish_task_appends_a_finished_record(log: DecisionLog, clock: Clock):
    task = log.start_task()
    clock.advance(hours=2)

    log.finish_task(task, "accepted")

    assert records(log, task)[-1] == {"type": "finished", "result": "accepted", "at": "2026-10-01T11:00:00+00:00"}


def test_finish_task_rejects_unknown_results(log: DecisionLog):
    task = log.start_task()

    with pytest.raises(LogError, match="result"):
        log.finish_task(task, "maybe")


def test_finishing_twice_fails(log: DecisionLog):
    task = log.start_task()
    log.finish_task(task, "accepted")

    with pytest.raises(LogError, match="already finished"):
        log.finish_task(task, "abandoned")


# Retention


def finished_task(log: DecisionLog, clock: Clock) -> str:
    task = log.start_task()
    log.finish_task(task, "accepted")
    return task


def test_finished_task_is_kept_within_retention(log: DecisionLog, clock: Clock):
    task = finished_task(log, clock)
    clock.advance(days=30)

    assert log.prune(retention_days=30, stale_days=60) == []
    assert task_files(log) == [task]


def test_finished_task_is_purged_after_retention(log: DecisionLog, clock: Clock):
    task = finished_task(log, clock)
    clock.advance(days=30, seconds=1)

    assert log.prune(retention_days=30, stale_days=60) == [log.root / "tasks" / f"{task}.jsonl"]
    assert task_files(log) == []


def test_retention_counts_from_finish_not_start(log: DecisionLog, clock: Clock):
    task = log.start_task()
    clock.advance(days=20)
    log.finish_task(task, "accepted")
    clock.advance(days=20)

    assert log.prune(retention_days=30, stale_days=60) == []


def test_unfinished_task_is_kept_until_stale(log: DecisionLog, clock: Clock):
    task = log.start_task()
    clock.advance(days=59)
    logged_decision(log, task)
    clock.advance(days=59)

    assert log.prune(retention_days=30, stale_days=60) == []


def test_unfinished_task_is_purged_once_stale(log: DecisionLog, clock: Clock):
    log.start_task()
    clock.advance(days=60, seconds=1)

    assert len(log.prune(retention_days=30, stale_days=60)) == 1


def test_zero_retention_disables_purging(log: DecisionLog, clock: Clock):
    finished_task(log, clock)
    log.start_task()
    clock.advance(days=365)

    assert log.prune(retention_days=0, stale_days=60) == []
    assert len(task_files(log)) == 2


def test_zero_stale_days_keeps_unfinished_tasks(log: DecisionLog, clock: Clock):
    unfinished = log.start_task()
    finished_task(log, clock)
    clock.advance(days=365)

    assert log.prune(retention_days=30, stale_days=0) != []
    assert task_files(log) == [unfinished]


def test_dry_run_reports_without_deleting(log: DecisionLog, clock: Clock):
    finished_task(log, clock)
    clock.advance(days=31)

    assert len(log.prune(retention_days=30, stale_days=60, dry_run=True)) == 1
    assert len(task_files(log)) == 1


def test_unreadable_lines_do_not_break_pruning(log: DecisionLog, clock: Clock):
    task = finished_task(log, clock)
    with (log.root / "tasks" / f"{task}.jsonl").open("a") as file:
        file.write("{not json\n")
    clock.advance(days=31)

    assert len(log.prune(retention_days=30, stale_days=60)) == 1


def test_prune_on_empty_log_is_a_no_op(log: DecisionLog):
    assert log.prune(retention_days=30, stale_days=60) == []


def test_maybe_prune_runs_at_most_once_a_day(log: DecisionLog, clock: Clock):
    finished_task(log, clock)
    clock.advance(days=31)
    assert log.maybe_prune(retention_days=30, stale_days=60) is not None

    clock.advance(hours=23)
    assert log.maybe_prune(retention_days=30, stale_days=60) is None

    clock.advance(hours=2)
    assert log.maybe_prune(retention_days=30, stale_days=60) == []


# Export


def test_export_flattens_decisions_with_final_choices(log: DecisionLog, clock: Clock):
    task = log.start_task()
    accepted = logged_decision(log, task)
    overridden = logged_decision(log, task, backend=UNSURE)
    log.record_outcome(overridden.id, "tdd")
    log.finish_task(task, "accepted")

    rows = {row.decision_id: row for row in log.export()}

    assert rows[accepted.id].model_choice == "tdd"
    assert rows[accepted.id].final_choice == "tdd"
    assert rows[accepted.id].overridden is False
    assert rows[overridden.id].model_status == "abstain"
    assert rows[overridden.id].model_choice == "single"
    assert rows[overridden.id].final_choice == "tdd"
    assert rows[overridden.id].overridden is True
    assert {row.task_result for row in rows.values()} == {"accepted"}


def test_export_leaves_final_choice_empty_for_unresolved_abstentions(log: DecisionLog):
    task = log.start_task()
    logged_decision(log, task, backend=UNSURE)

    (row,) = log.export()

    assert row.final_choice is None
    assert row.overridden is None
    assert row.task_result is None


def test_export_filters_by_decision_and_date(log: DecisionLog, clock: Clock):
    task = log.start_task()
    logged_decision(log, task)
    clock.advance(days=5)
    recent = logged_decision(log, task)
    logged_decision(
        log,
        task,
        backend=StaticBackend({"generalist": {"true": 0.9, "false": 0.1}, "security": {"true": 0.1, "false": 0.9}}),
        rubric=PANEL,
    )

    rows = list(log.export(since=START + timedelta(days=1), decision="tier"))

    assert [row.decision_id for row in rows] == [recent.id]


def test_export_only_sees_retained_tasks(log: DecisionLog, clock: Clock):
    task = log.start_task()
    logged_decision(log, task)
    log.finish_task(task, "accepted")
    clock.advance(days=31)
    log.prune(retention_days=30, stale_days=60)

    assert list(log.export()) == []


# Location


def test_state_dir_honours_override_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ORCHESTRA_STATE_DIR", str(tmp_path / "custom"))

    assert default_state_dir() == tmp_path / "custom"


def test_state_dir_defaults_to_xdg_state_home(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("ORCHESTRA_STATE_DIR")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert default_state_dir() == tmp_path / "orchestra"
