import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from .backends import create_backend
from .config import DEFAULT_BACKEND_OPTIONS, Config, ConfigError, default_config_path, load_config
from .decide import abstained, decide
from .install import HARNESSES, InstallError, Locations, install
from .log import TASK_RESULTS, DecisionLog, LogError, default_state_dir
from .replay import ReplayError, cases_from_golden, cases_from_log, evaluate
from .roles import RoleError, load_roles
from .rubrics import RubricError, load_rubric, load_rubrics
from .signals import SignalsError, collect_diff_signals, with_signals

# Editable installs only. Installed copies set `rubrics_dir` and `roles_dir` in their config.
BUNDLED_CORE = Path(__file__).resolve().parents[3] / "core"

USER_ERRORS = (ConfigError, InstallError, LogError, ReplayError, RoleError, RubricError, SignalsError, OSError)


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise UsageError(f"{self.prog}: {message}")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        args = _parser().parse_args(argv)
    except UsageError as error:
        if argv and argv[0] == "decide":
            # Even a malformed call must leave the orchestrator a JSON answer to fall back from.
            decision = argv[1] if len(argv) > 1 and not argv[1].startswith("-") else "unknown"
            print(json.dumps(abstained(decision, f"input_error: {error}", "unknown").to_json()))
            return 0
        print(error, file=sys.stderr)
        raise SystemExit(2) from None
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
    common = _Parser(add_help=False)
    common.add_argument("--config", type=Path, default=None, help="defaults to $ORCHESTRA_CONFIG or ~/.config/orchestra/orchestra.toml")

    parser = _Parser(prog="orchestra", description="Local decision model routing for agent orchestration")
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

    rubric_cmd = commands.add_parser("rubric", help="show the rubric behind a decision")
    rubric_actions = rubric_cmd.add_subparsers(dest="action", required=True)
    rubric_show = rubric_actions.add_parser("show", parents=[common])
    rubric_show.add_argument("decision")
    rubric_show.add_argument("--rubrics-dir", type=Path)
    rubric_show.set_defaults(handler=_rubric_show)

    role_cmd = commands.add_parser("role", help="list, show or dispatch agent roles")
    role_actions = role_cmd.add_subparsers(dest="action", required=True)
    role_list = role_actions.add_parser("list", parents=[common])
    role_list.add_argument("--roles-dir", type=Path)
    role_list.set_defaults(handler=_role_list)
    role_show = role_actions.add_parser("show", parents=[common])
    role_show.add_argument("role")
    role_show.add_argument("--brief-file", help="brief to append, or - for stdin")
    role_show.add_argument("--roles-dir", type=Path)
    role_show.set_defaults(handler=_role_show)
    role_run = role_actions.add_parser("run", parents=[common], help="run a role through a harness command")
    role_run.add_argument("role")
    role_run.add_argument("--brief-file", required=True, help="brief to append, or - for stdin")
    role_run.add_argument("--harness", default="generic")
    role_run.add_argument("--tier", choices=("strong", "fast"), help="override the role's tier, e.g. to escalate")
    role_run.add_argument("--repo", type=Path, default=Path("."), help="working directory for the agent")
    role_run.add_argument("--roles-dir", type=Path)
    role_run.add_argument("--dry-run", action="store_true", help="print the command instead of running it")
    role_run.set_defaults(handler=_role_run)

    install_cmd = command("install", _install, "install the skill, roles and config for a harness")
    install_cmd.add_argument("--harness", required=True, choices=HARNESSES)
    install_cmd.add_argument("--target", type=Path, help="skills directory, required for the generic harness")
    install_cmd.add_argument("--home", type=Path, default=Path.home())
    install_cmd.add_argument("--force", action="store_true", help="overwrite files edited since the last install")
    install_cmd.add_argument("--dry-run", action="store_true")
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
        # A null task tells the orchestrator there is no logged decision to record an outcome against.
        result.task = None
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
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        os.fchmod(descriptor, 0o600)
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


def _rubric_show(args: argparse.Namespace) -> int:
    directory = _rubrics_dir(args, _config(args))
    for path in sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")]):
        if load_rubric(path).decision == args.decision:
            sys.stdout.write(path.read_text())
            return 0
    raise RubricError(f"unknown decision {args.decision!r}")


def _role_list(args: argparse.Namespace) -> int:
    roles = load_roles(_roles_dir(args, _config(args)))
    _print_json([{"name": r.name, "tier": r.tier, "description": r.description} for r in roles.values()])
    return 0


def _role_show(args: argparse.Namespace) -> int:
    role = _role(args, _config(args))
    sys.stdout.write(role.prompt(_read_state(args.brief_file)) if args.brief_file else role.body)
    return 0


def _role_run(args: argparse.Namespace) -> int:
    config = _config(args)
    role = _role(args, config)
    harness = config.harnesses.get(args.harness)
    if harness is None:
        raise ConfigError(f"unknown harness {args.harness!r}, configured: {', '.join(sorted(config.harnesses))}")
    if not harness.command:
        raise ConfigError(f"harness {args.harness!r} has no command, it dispatches roles natively")
    model = harness.strong if (args.tier or role.tier) == "strong" else harness.fast
    sandbox = "workspace-write" if "write" in role.capabilities else "read-only"
    placeholders = {"{model}": model, "{sandbox}": sandbox, "{tools}": ",".join(role.tools)}
    command = [_fill(part, placeholders) for part in harness.command]
    if args.dry_run:
        _print_json({"command": command, "cwd": str(args.repo)})
        return 0
    prompt = role.prompt(_read_state(args.brief_file))
    completed = subprocess.run(command, input=prompt, text=True, cwd=args.repo, stdout=subprocess.PIPE)
    sys.stdout.write(completed.stdout)
    return completed.returncode


def _fill(template: str, placeholders: dict[str, str]) -> str:
    for placeholder, value in placeholders.items():
        template = template.replace(placeholder, value)
    return template


def _role(args: argparse.Namespace, config: Config):
    roles = load_roles(_roles_dir(args, config))
    if args.role not in roles:
        raise RoleError(f"unknown role {args.role!r}, available: {', '.join(roles)}")
    return roles[args.role]


def _roles_dir(args: argparse.Namespace, config: Config) -> Path:
    return args.roles_dir or config.roles_dir or BUNDLED_CORE / "roles"


def _install(args: argparse.Namespace) -> int:
    home = args.home
    data_home = os.environ.get("XDG_DATA_HOME") if home == Path.home() else None
    locations = Locations(
        home=home,
        share_dir=Path(os.environ.get("ORCHESTRA_SHARE_DIR") or Path(data_home or home / ".local" / "share") / "orchestra"),
        config_path=args.config or Path(os.environ.get("ORCHESTRA_CONFIG") or home / ".config" / "orchestra" / "orchestra.toml"),
        codex_home=Path(os.environ.get("CODEX_HOME") or home / ".codex"),
        target=args.target,
    )
    config = load_config(locations.config_path)
    report = install(args.harness, BUNDLED_CORE.parent, locations, config, force=args.force, dry_run=args.dry_run)
    _print_json(report.to_json())
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
