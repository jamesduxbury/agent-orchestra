import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from .backends import create_backend
from .config import DEFAULT_BACKEND_OPTIONS, Config, ConfigError, default_config_path, load_config
from .decide import abstained, decide
from .log import TASK_RESULTS, DecisionLog, LogError, default_state_dir
from .replay import ReplayError, cases_from_golden, cases_from_log, evaluate
from .rubrics import RubricError, load_rubrics
from .signals import SignalsError, collect_diff_signals, with_signals

# Editable installs only. Installed copies set `rubrics_dir` in their config.
BUNDLED_CORE = Path(__file__).resolve().parents[3] / "core"

USER_ERRORS = (ConfigError, LogError, ReplayError, RubricError, SignalsError, OSError)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command != "log":
        _prune_opportunistically(args.config)
    if args.handler is _decide:
        return _decide(args)
    try:
        return args.handler(args)
    except USER_ERRORS as error:
        print(f"orchestra {args.command}: {error}", file=sys.stderr)
        return 2


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, default=None, help="defaults to $ORCHESTRA_CONFIG or ~/.config/orchestra/orchestra.toml")

    parser = argparse.ArgumentParser(prog="orchestra", description="Local decision model routing for agent orchestration")
    commands = parser.add_subparsers(dest="command", required=True)

    def command(name: str, handler, help: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, parents=[common], help=help)
        sub.set_defaults(handler=handler)
        return sub

    decide_cmd = command("decide", _decide, "ask the decision model for a routing decision (always prints JSON)")
    decide_cmd.add_argument("decision", help="rubric name, e.g. tier")
    decide_cmd.add_argument("--state-file", required=True, help="task state, or - for stdin")
    decide_cmd.add_argument("--task", help="task id from `orchestra task start`, to log the decision")
    decide_cmd.add_argument("--rubrics-dir", type=Path)
    _add_diff_arguments(decide_cmd, required=False)

    signals_cmd = command("signals", _signals, "print the diff signals a decision would see")
    _add_diff_arguments(signals_cmd, required=True)

    task_cmd = commands.add_parser("task", help="start or finish a logged task")
    task_actions = task_cmd.add_subparsers(dest="action", required=True)
    start = task_actions.add_parser("start", parents=[common])
    start.add_argument("--title")
    start.set_defaults(handler=_task_start)
    finish = task_actions.add_parser("finish", parents=[common])
    finish.add_argument("task")
    finish.add_argument("--result", required=True, choices=TASK_RESULTS)
    finish.set_defaults(handler=_task_finish)

    outcome_cmd = command("outcome", _outcome, "record the option actually chosen for a decision")
    outcome_cmd.add_argument("decision_id")
    outcome_cmd.add_argument("--chosen", required=True, help="option, or comma-separated options for a panel")

    log_cmd = commands.add_parser("log", help="manage the decision log")
    log_actions = log_cmd.add_subparsers(dest="action", required=True)
    prune = log_actions.add_parser("prune", parents=[common])
    prune.add_argument("--dry-run", action="store_true")
    prune.set_defaults(handler=_log_prune)
    export = log_actions.add_parser("export", parents=[common])
    export.add_argument("--since", type=_date, help="YYYY-MM-DD")
    export.add_argument("--decision")
    export.add_argument("--output", type=Path, help="defaults to stdout")
    export.set_defaults(handler=_log_export)

    replay_cmd = command("replay", _replay, "score a backend against logged outcomes or golden cases")
    replay_cmd.add_argument("--golden", type=Path, help="directory of golden case YAML files")
    replay_cmd.add_argument("--rubrics-dir", type=Path)
    replay_cmd.add_argument("--backend", help="backend to evaluate, defaults to the configured one")
    replay_cmd.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="backend option override")
    return parser


def _add_diff_arguments(command: argparse.ArgumentParser, required: bool) -> None:
    command.add_argument("--git-diff", metavar="BASE", required=required, help="add signals from the diff against BASE")
    command.add_argument("--repo", type=Path, default=Path("."))


def _decide(args: argparse.Namespace) -> int:
    # Always JSON on stdout with exit 0, so the orchestrator can fall back to deciding itself.
    backend_name = "unknown"
    try:
        config = _config(args)
        backend_name = config.backend
        backend = create_backend(config.backend, config.backend_options)
        rubrics = load_rubrics(_rubrics_dir(args, config))
        log = _log(config)
        if args.decision not in rubrics:
            result = abstained(args.decision, f"unknown_decision: {args.decision!r}", backend_name)
        elif args.task and not log.has_task(args.task):
            result = abstained(args.decision, f"input_error: unknown task {args.task}", backend_name)
        else:
            state = _read_state(args.state_file)
            if args.git_diff:
                state = with_signals(state, _diff_signals(args, config))
            result = decide(rubrics[args.decision], state, backend)
            if args.task:
                result.task = args.task
                _record_quietly(log, rubrics[args.decision], state, result)
    except (ConfigError, RubricError) as error:
        result = abstained(args.decision, f"config_error: {error}", backend_name)
    except (OSError, SignalsError) as error:
        result = abstained(args.decision, f"input_error: {error}", backend_name)
    except Exception as error:  # noqa: BLE001 - never leave the orchestrator without an answer
        result = abstained(args.decision, f"internal_error: {type(error).__name__}: {error}", backend_name)

    print(json.dumps(result.to_json()))
    return 0


def _record_quietly(log: DecisionLog, rubric, state: str, result) -> None:
    try:
        log.record_decision(result.task, rubric, state, result)
    except (LogError, OSError) as error:
        print(f"orchestra decide: decision not logged: {error}", file=sys.stderr)


def _signals(args: argparse.Namespace) -> int:
    print(_diff_signals(args, _config(args)).describe())
    return 0


def _task_start(args: argparse.Namespace) -> int:
    _print_json({"task": _log(_config(args)).start_task(args.title)})
    return 0


def _task_finish(args: argparse.Namespace) -> int:
    _log(_config(args)).finish_task(args.task, args.result)
    _print_json({"task": args.task, "result": args.result})
    return 0


def _outcome(args: argparse.Namespace) -> int:
    chosen = [option.strip() for option in args.chosen.split(",")] if "," in args.chosen else args.chosen
    overridden = _log(_config(args)).record_outcome(args.decision_id, chosen)
    _print_json({"decision_id": args.decision_id, "chosen": chosen, "overridden": overridden})
    return 0


def _log_prune(args: argparse.Namespace) -> int:
    config = _config(args)
    removed = _log(config).prune(config.retention_days, config.stale_days, dry_run=args.dry_run)
    _print_json({"dry_run": args.dry_run, "removed": [str(path) for path in removed]})
    return 0


def _log_export(args: argparse.Namespace) -> int:
    rows = _log(_config(args)).export(since=args.since, decision=args.decision)
    lines = "".join(json.dumps(row.to_json()) + "\n" for row in rows)
    if args.output:
        # The dataset holds task descriptions, so keep it as private as the log.
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as file:
            file.write(lines)
    else:
        sys.stdout.write(lines)
    return 0


def _replay(args: argparse.Namespace) -> int:
    config = _config(args)
    backend_name = args.backend or config.backend
    options = config.backend_options if backend_name == config.backend else dict(DEFAULT_BACKEND_OPTIONS.get(backend_name, {}))
    backend = create_backend(backend_name, options | dict(_option(item) for item in args.set))
    if args.golden:
        cases = cases_from_golden(args.golden, load_rubrics(_rubrics_dir(args, config)))
    else:
        cases = cases_from_log(_log(config))
    _print_json(evaluate(cases, backend).to_json())
    return 0


def _prune_opportunistically(config_path: Path | None) -> None:
    try:
        config = load_config(config_path or default_config_path())
        _log(config).maybe_prune(config.retention_days, config.stale_days)
    except Exception:  # noqa: BLE001 - housekeeping must never block the command
        pass


def _config(args: argparse.Namespace) -> Config:
    return load_config(args.config or default_config_path())


def _log(config: Config) -> DecisionLog:
    if override := os.environ.get("ORCHESTRA_STATE_DIR"):
        return DecisionLog(Path(override))
    return DecisionLog(config.log_dir or default_state_dir())


def _rubrics_dir(args: argparse.Namespace, config: Config) -> Path:
    return args.rubrics_dir or config.rubrics_dir or BUNDLED_CORE / "rubrics"


def _diff_signals(args: argparse.Namespace, config: Config):
    return collect_diff_signals(args.git_diff, args.repo, list(config.sensitive_paths), list(config.test_paths))


def _read_state(state_file: str) -> str:
    if state_file == "-":
        return sys.stdin.read()
    return Path(state_file).read_text()


def _option(item: str) -> tuple[str, object]:
    key, separator, raw = item.partition("=")
    if not separator:
        raise ConfigError(f"--set expects KEY=VALUE, got {item!r}")
    try:
        return key, json.loads(raw)
    except json.JSONDecodeError:
        return key, raw


def _date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)


def _print_json(payload: dict | list) -> None:
    print(json.dumps(payload))
