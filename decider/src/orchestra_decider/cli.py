import argparse
import json
import sys
from pathlib import Path

from .backends import create_backend
from .config import Config, ConfigError, default_config_path, load_config
from .decide import abstained, decide
from .rubrics import RubricError, load_rubrics
from .signals import SignalsError, collect_diff_signals, with_signals

# Editable installs only. Installed copies set `rubrics_dir` in their config.
BUNDLED_CORE = Path(__file__).resolve().parents[3] / "core"

USER_ERRORS = (ConfigError, RubricError, SignalsError, OSError)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
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
    decide_cmd.add_argument("--rubrics-dir", type=Path)
    _add_diff_arguments(decide_cmd, required=False)

    signals_cmd = command("signals", _signals, "print the diff signals a decision would see")
    _add_diff_arguments(signals_cmd, required=True)

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
        if args.decision not in rubrics:
            result = abstained(args.decision, f"unknown_decision: {args.decision!r}", backend_name)
        else:
            state = _read_state(args.state_file)
            if args.git_diff:
                state = with_signals(state, _diff_signals(args, config))
            result = decide(rubrics[args.decision], state, backend)
    except (ConfigError, RubricError) as error:
        result = abstained(args.decision, f"config_error: {error}", backend_name)
    except (OSError, SignalsError) as error:
        result = abstained(args.decision, f"input_error: {error}", backend_name)
    except Exception as error:  # noqa: BLE001 - never leave the orchestrator without an answer
        result = abstained(args.decision, f"internal_error: {type(error).__name__}: {error}", backend_name)

    print(json.dumps(result.to_json()))
    return 0


def _signals(args: argparse.Namespace) -> int:
    print(_diff_signals(args, _config(args)).describe())
    return 0


def _config(args: argparse.Namespace) -> Config:
    return load_config(args.config or default_config_path())


def _rubrics_dir(args: argparse.Namespace, config: Config) -> Path:
    return args.rubrics_dir or config.rubrics_dir or BUNDLED_CORE / "rubrics"


def _diff_signals(args: argparse.Namespace, config: Config):
    return collect_diff_signals(args.git_diff, args.repo, list(config.sensitive_paths), list(config.test_paths))


def _read_state(state_file: str) -> str:
    if state_file == "-":
        return sys.stdin.read()
    return Path(state_file).read_text()
