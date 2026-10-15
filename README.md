# agent-orchestra

Orchestration skills for AI coding agents that scale the process to the task. A small local decision model makes the routing calls, so a typo fix gets a direct edit, a small change gets one implementer and one review, and a risky change gets full red/green TDD (strong model writes failing tests, fast model implements) with specialist reviewers.

It works in Claude Code, Codex CLI and any harness that can run shell commands. The decision model is swappable through config.

## How it works

```
orchestrator agent (any harness)
  │  follows core/skills/orchestrate/SKILL.md
  │
  ├── orchestra decide scope|tier|panel|verdict ──▶ local decision model (Ollama /v1/systemone)
  │        always prints JSON, abstains when unsure or unavailable
  │
  └── dispatches roles ──▶ scoper · test-author · implementer · reviewer-{generalist,quality,security,tests,performance}
           Claude Code: native subagents      Codex / generic: `orchestra role run`
```

| Decision | Options | Notes |
|---|---|---|
| `scope` | `scope`, `ready` | Dispatch the scoper before any code is written? |
| `tier` | `direct`, `single`, `tdd` | Workflow weight |
| `panel` | `quality`, `security`, `tests`, `performance` | Specialists added to the always-present `generalist`. One yes/no question each, batched in one request |
| `verdict` | `accept`, `revise`, `escalate`, `ask-human` | After each review iteration |

Each decision is defined by a rubric in `core/rubrics/`. The model receives the rubric, and the orchestrator reads the same rubric (`orchestra rubric show <decision>`) when the model abstains, so both decide against the same criteria.

**Confidence** is computed by the decider itself, so thresholds mean the same for every backend. For `choice` decisions it is 1 − normalised entropy, which matches what Ollama reports. For the panel it is how far the least certain option sits from the selection line. Below the rubric's threshold the decider abstains, and still reports what it would have suggested.

## Setup

```bash
brew install uv ollama
ollama pull strands-decider:2b
uv tool install --editable ./decider          # puts `orchestra` on your PATH
orchestra install --harness claude-code       # skill + orchestra-* subagents in ~/.claude
orchestra install --harness codex             # skill in ~/.codex/skills
orchestra install --harness generic --target <skills dir>
```

The installer copies rubrics, roles and golden cases to `~/.local/share/orchestra` and creates `~/.config/orchestra/orchestra.toml` if missing (see `config/orchestra.example.toml`). It is idempotent. It only overwrites files it wrote and you have not edited (`--force` overrides), removes files for roles that no longer exist, and `--dry-run` shows what would change.

Then ask your agent to use the `orchestrate` skill for a task.

## Demo

```bash
scripts/demo.sh                 # three tasks through Strands Decider 2B
scripts/demo.sh laya            # the same tasks with a different decision model
```

The demo uses a throwaway repository and log. It shows a typo going `direct`, a small feature going `single` with a generalist review, and a bcrypt migration going `tdd`, with diff signals flagging `src/auth/` and the panel suggesting a security reviewer.

## Swapping the decision model

Any model served on a `/v1/systemone` endpoint works by changing one line:

```toml
[backends.systemone]
model = "laya"            # or any other decision model Ollama or llama.cpp serves
url = "http://localhost:11434"
```

Before switching, score the candidate:

```bash
orchestra replay --golden ~/.local/share/orchestra/golden --set model=laya   # against curated cases
orchestra replay --set model=laya                                             # against your own logged outcomes
```

On the shipped golden set, Strands Decider 2B agrees on 27 of 29 cases and abstains on 4. Laya agrees on 15 and abstains on 24. The golden set was also used to tune the rubric wording, so treat these as optimistic and prefer replaying your own log once it has history.

**Adding a model family** with a different API is one file in `decider/src/orchestra_decider/backends/`. It needs a class with a `name`, a `from_options(dict)` constructor and `decide(request) -> {question: {option: probability}}`, registered in `backends/__init__.py`. `tests/test_backend_contract.py` checks every backend against the same contract.

## Decision log and retention

Pass `--task` and each decision is logged to `~/.local/state/orchestra/tasks/<task>.jsonl` (mode 0600), together with the rubric, the state and the orchestrator's final choice (`orchestra outcome`). One file per task makes retention a file delete.

```toml
[log]
retention_days = 30   # after the task finishes. 0 disables purging
stale_days = 60       # unfinished tasks, from their last write. 0 keeps them
```

Every `orchestra` command prunes at most once a day, so no daemon is needed, and `orchestra log prune --dry-run` shows what would go. `orchestra log export` writes the retained window as a flat JSONL dataset (state, options, model choice, final choice, whether it was overridden) for evaluation or fine-tuning.

## CLI

| Command | Purpose |
|---|---|
| `orchestra decide <decision> --state-file F [--task T] [--git-diff BASE]` | Routing decision as JSON. Never fails, abstains instead |
| `orchestra signals --git-diff BASE` | The diff summary the model sees |
| `orchestra rubric show <decision>` | The rubric, for deciding without the model |
| `orchestra task start` / `task finish <task> --result accepted\|abandoned` | Task lifecycle for the log |
| `orchestra outcome <decision-id> --chosen <option>` | Record the orchestrator's final choice |
| `orchestra log prune [--dry-run]` / `log export [--since] [--decision] [--output]` | Retention and datasets |
| `orchestra replay [--golden DIR] [--backend B] [--set k=v]` | Score a backend |
| `orchestra role list` / `role show <role>` / `role run <role> --harness H --brief-file F [--tier strong]` | Roles and dispatch for Codex and generic harnesses |
| `orchestra install --harness claude-code\|codex\|generic` | Install or update |

## Layout

```
core/rubrics/    decision rubrics (single source of truth)
core/roles/      role prompts with tier and capabilities
core/golden/     golden cases for replay
core/skills/     the orchestrate skill (harness-neutral)
harnesses/       per-harness dispatch guides
decider/         the `orchestra` CLI (Python, uv)
docs/            spike notes on the systemone API
scripts/         demo
```

## Development

```bash
cd decider && uv run pytest
```

Live contract tests run against Ollama when `strands-decider:2b` or `laya` are pulled, and are skipped otherwise. llama.cpp's `/v1/systemone` should work with the same backend, but is untested (see `docs/spike-systemone.md`).
