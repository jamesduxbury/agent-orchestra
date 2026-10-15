import json
import os
import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .rubrics import Rubric, RubricKind
from .types import DecisionResult

TASK_ID = re.compile(r"^t_\d{8}_[0-9a-f]{8}$")
DECISION_ID = re.compile(r"^d_[0-9a-f]{12}$")
TASK_RESULTS = ("accepted", "abandoned")
PRUNE_INTERVAL = timedelta(days=1)


class LogError(Exception):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


def default_state_dir() -> Path:
    if override := os.environ.get("ORCHESTRA_STATE_DIR"):
        return Path(override)
    state_home = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(state_home) / "orchestra"


@dataclass(frozen=True)
class ExportRow:
    decision_id: str
    task: str
    at: str
    decision: str
    rubric: dict
    state: str
    backend: str
    model_status: str
    model_choice: str | list[str] | None
    final_choice: str | list[str] | None
    overridden: bool | None
    task_result: str | None

    def to_json(self) -> dict:
        return asdict(self)


class DecisionLog:
    """One JSONL file per task, so retention is a file delete rather than a rewrite."""

    def __init__(self, root: Path, clock: Callable[[], datetime] = utc_now):
        self.root = root
        self.clock = clock

    @property
    def tasks_dir(self) -> Path:
        return self.root / "tasks"

    def start_task(self, title: str | None = None) -> str:
        task = f"t_{self.clock():%Y%m%d}_{uuid.uuid4().hex[:8]}"
        self._append(task, {"type": "task_started", "task": task, "title": title, "at": self._now()})
        return task

    def finish_task(self, task_id: str, result: str) -> None:
        if result not in TASK_RESULTS:
            raise LogError(f"result must be one of {', '.join(TASK_RESULTS)}")
        if any(record["type"] == "finished" for record in self._records(self._existing(task_id))):
            raise LogError(f"task {task_id} is already finished")
        self._append(task_id, {"type": "finished", "result": result, "at": self._now()})

    def record_decision(self, task_id: str, rubric: Rubric, state: str, result: DecisionResult) -> None:
        self._existing(task_id)
        self._append(
            task_id,
            {
                "type": "decision",
                "id": result.id,
                "at": self._now(),
                "rubric": _rubric_json(rubric),
                "state": state,
                "result": result.to_json(),
            },
        )

    def record_outcome(self, decision_id: str, chosen: str | list[str]) -> bool | None:
        """Returns whether the orchestrator overrode the model, or None if the model offered nothing."""
        task_id, decision = self._find_decision(decision_id)
        if decision["rubric"]["kind"] == RubricKind.PANEL and isinstance(chosen, str):
            chosen = [chosen]
        _validate_choice(decision["rubric"], chosen)
        suggested = decision["result"]["suggested"]
        overridden = None if suggested is None else _normalised(chosen) != _normalised(suggested)
        self._append(
            task_id,
            {"type": "outcome", "decision_id": decision_id, "chosen": chosen, "overridden": overridden, "at": self._now()},
        )
        return overridden

    def has_task(self, task_id: str) -> bool:
        return bool(TASK_ID.match(task_id)) and self._path(task_id).exists()

    def prune(self, retention_days: int, stale_days: int, dry_run: bool = False) -> list[Path]:
        if retention_days == 0 or not self.tasks_dir.exists():
            return []
        now = self.clock()
        expired = [
            path
            for path in sorted(self.tasks_dir.glob("*.jsonl"))
            if _expired(path, list(self._records(path)), now, retention_days, stale_days)
        ]
        if not dry_run:
            for path in expired:
                path.unlink(missing_ok=True)
        return expired

    def maybe_prune(self, retention_days: int, stale_days: int) -> list[Path] | None:
        """Prune at most once a day. Returns None when skipped."""
        marker = self.root / "last_prune"
        now = self.clock()
        try:
            if now - datetime.fromisoformat(marker.read_text().strip()) < PRUNE_INTERVAL:
                return None
        except (OSError, ValueError):
            pass
        removed = self.prune(retention_days, stale_days)
        self.root.mkdir(parents=True, exist_ok=True)
        marker.write_text(now.isoformat())
        return removed

    def export(self, since: datetime | None = None, decision: str | None = None) -> Iterator[ExportRow]:
        if not self.tasks_dir.exists():
            return
        for path in sorted(self.tasks_dir.glob("*.jsonl")):
            records = list(self._records(path))
            outcomes = {r["decision_id"]: r for r in records if r["type"] == "outcome"}
            task_result = next((r["result"] for r in records if r["type"] == "finished"), None)
            for record in records:
                if record["type"] != "decision":
                    continue
                if decision and record["rubric"]["decision"] != decision:
                    continue
                if since and datetime.fromisoformat(record["at"]) < since:
                    continue
                yield _export_row(path.stem, record, outcomes.get(record["id"]), task_result)

    def _now(self) -> str:
        return self.clock().isoformat()

    def _path(self, task_id: str) -> Path:
        if not TASK_ID.match(task_id):
            raise LogError(f"malformed task id {task_id!r}")
        return self.tasks_dir / f"{task_id}.jsonl"

    def _existing(self, task_id: str) -> Path:
        path = self._path(task_id)
        if not path.exists():
            raise LogError(f"unknown task {task_id}")
        return path

    def _append(self, task_id: str, record: dict) -> None:
        self.tasks_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Task files hold task descriptions and diffs, so keep them private to the user.
        descriptor = os.open(self._path(task_id), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "a") as file:
            file.write(json.dumps(record) + "\n")

    def _records(self, path: Path) -> Iterator[dict]:
        for line in path.read_text().splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and "type" in record:
                yield record

    def _find_decision(self, decision_id: str) -> tuple[str, dict]:
        if DECISION_ID.match(decision_id) and self.tasks_dir.exists():
            for path in self.tasks_dir.glob("*.jsonl"):
                for record in self._records(path):
                    if record["type"] == "decision" and record["id"] == decision_id:
                        return path.stem, record
        raise LogError(f"unknown decision {decision_id}")


def _rubric_json(rubric: Rubric) -> dict:
    return {
        "decision": rubric.decision,
        "kind": str(rubric.kind),
        "instructions": rubric.instructions,
        "options": rubric.options,
        "threshold": rubric.threshold,
        "select": rubric.select,
        "baseline": rubric.baseline,
    }


def _validate_choice(rubric: dict, chosen: str | list[str]) -> None:
    options = list(rubric["options"])
    if rubric["kind"] == RubricKind.PANEL:
        allowed = options + ([rubric["baseline"]] if rubric.get("baseline") else [])
        if not isinstance(chosen, list) or not chosen or any(option not in allowed for option in chosen):
            raise LogError(f"panel choice must be one or more of {', '.join(allowed)}")
    elif not isinstance(chosen, str) or chosen not in options:
        raise LogError(f"choice must be one of {', '.join(options)}")


def _normalised(choice: str | list[str]) -> str | list[str]:
    return sorted(choice) if isinstance(choice, list) else choice


def _expired(path: Path, records: list[dict], now: datetime, retention_days: int, stale_days: int) -> bool:
    finished = next((r for r in records if r["type"] == "finished"), None)
    if finished:
        return now - datetime.fromisoformat(finished["at"]) > timedelta(days=retention_days)
    timestamps = [datetime.fromisoformat(r["at"]) for r in records if "at" in r]
    last_write = max(timestamps) if timestamps else datetime.fromtimestamp(path.stat().st_mtime, UTC)
    return now - last_write > timedelta(days=stale_days)


def _export_row(task: str, record: dict, outcome: dict | None, task_result: str | None) -> ExportRow:
    result = record["result"]
    if outcome:
        final_choice, overridden = outcome["chosen"], outcome["overridden"]
    elif result["status"] == "decided":
        final_choice, overridden = result["choice"], False
    else:
        final_choice, overridden = None, None
    return ExportRow(
        decision_id=record["id"],
        task=task,
        at=record["at"],
        decision=record["rubric"]["decision"],
        rubric=record["rubric"],
        state=record["state"],
        backend=result["backend"],
        model_status=result["status"],
        model_choice=result["suggested"],
        final_choice=final_choice,
        overridden=overridden,
        task_result=task_result,
    )
