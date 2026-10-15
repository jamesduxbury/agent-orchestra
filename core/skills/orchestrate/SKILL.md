---
name: orchestrate
description: Use when delivering a coding task through specialised subagents (scoping, test-first implementation, review) and the weight of the process should match the task, so a small change gets a light workflow and a risky one gets full red/green TDD with targeted reviewers. Routing decisions come from a local decision model via the `orchestra` CLI.
---

# Orchestrate

You are the orchestrator. You write briefs, check the work of subagents and talk to the human. A small local decision model makes four routing calls for you, quickly and cheaply:

| Decision | Question | Options |
|---|---|---|
| `scope` | Is the task specified well enough to start? | `scope`, `ready` |
| `tier` | How heavy should the workflow be? | `direct`, `single`, `tdd` |
| `panel` | Which specialist reviewers join the `generalist`? | `quality`, `security`, `tests`, `performance` |
| `verdict` | What happens after a review? | `accept`, `revise`, `escalate`, `ask-human` |

How to run a role in this harness is in `dispatch.md` beside this file. Read it before your first dispatch.

The roles are `scoper`, `test-author` (strong model), `implementer` (fast model), and the reviewers `reviewer-generalist`, `reviewer-quality`, `reviewer-security`, `reviewer-tests` and `reviewer-performance`.

## Asking for a decision

Keep a state file outside the repository (for example `$TMPDIR/orchestra/<task>/state.md`) describing the task as it stands. Then run:

```bash
orchestra decide <decision> --task "$TASK" --state-file "$STATE" [--git-diff "$BASE"]
```

It always prints one line of JSON and exits 0.

- `"status": "decided"` means follow `choice`.
- `"status": "abstain"` means the model was unsure or unavailable (see `reason`). Run `orchestra rubric show <decision>`, make the call yourself against that rubric (treat `suggested` as a weak hint), then record it with `orchestra outcome <id> --chosen <option>`. For `panel`, pass comma-separated reviewers, for example `--chosen generalist,security`.
- You may override a `decided` choice when you know something the model could not. For example, the human asked for TDD, or the diff touches auth but `security` was not selected. Record every override with `orchestra outcome`. Overrides are the training signal for improving the model, so never skip them.
- Explicit instructions from the human always beat the model.

## State file

Write plain text the model can read. Rewrite it before each decision.

```
Task: <one or two sentences>
Acceptance criteria:
- <testable statement>
Context: <relevant files, constraints, anything the human said>
```

For `verdict`, replace the context with the current iteration and the review findings:

```
Iteration <n> review findings:
- <reviewer>: BLOCKING <path:line> <problem>
- <reviewer>: SUGGESTION <path:line> <problem>
Tests: <all passing | n failing>
```

## Workflow

### 0. Start

Run `orchestra task start --title "<short title>"` and keep the `task` id it prints as `$TASK`. Record `BASE=$(git rev-parse HEAD)`, so `--git-diff "$BASE"` describes everything this task changes, including uncommitted and untracked files.

### 1. Scope

Run `decide scope`. On `scope`, dispatch `scoper` with the request and pointers to relevant code. Show the human its acceptance criteria and open questions, wait for answers, then update the state file. On `ready`, write the acceptance criteria yourself from the request.

### 2. Tier

Run `decide tier`.

- **`direct`**: make the change yourself, or dispatch `implementer` for anything beyond a few lines. Run the relevant tests, check the diff yourself, then go to step 5. No reviewer dispatch.
- **`single`**: dispatch `implementer` with the acceptance criteria and ask it to add tests alongside the code. Go to step 3.
- **`tdd`**:
  1. **Red.** Dispatch `test-author` with the acceptance criteria.
  2. **Check red.** Run the new tests yourself. Every test must fail because the behaviour is missing, not because of an import error, typo or broken fixture. Check that every criterion is covered and that the tests do not pin implementation details. Send problems back to `test-author`. If the human asked for collaborative TDD, present the tests and wait for approval.
  3. **Green.** Dispatch `implementer` with the test files and an instruction not to modify them. Go to step 3.

### 3. Review

Run `decide panel --git-diff "$BASE"`. Dispatch every reviewer in `choice` in parallel, each with the acceptance criteria and the command to see the change (`git diff $BASE` plus untracked files). Read the diff yourself while they work.

### 4. Verdict

Collect the findings into the state file (see the format above), keeping each BLOCKING finding verbatim. Add your own findings as `orchestrator`. Run `decide verdict`.

- **`accept`**: go to step 5.
- **`revise`**: dispatch `implementer` again with every BLOCKING finding, then return to step 3. Re-run the same panel. You do not need a new panel decision.
- **`escalate`**: dispatch `implementer` with the strong model (see `dispatch.md`) and the full history of findings, then return to step 3.
- **`ask-human`**: present the conflicting findings and the options, and wait.

Stop after three review iterations. If you have not yet escalated, escalate. Otherwise ask the human.

### 5. Finish

Run the full test suite and any project checks. Then:

```bash
orchestra task finish "$TASK" --result accepted   # or abandoned
```

Report the outcome briefly: the tier, the reviewers, the iterations, any decisions you overrode, and anything left for the human. Do not commit unless the human asked you to.

## Writing briefs

Subagents start with no memory of this conversation, so every brief must stand alone:

- the goal and acceptance criteria
- the files and commands that matter, including how to run the tests
- constraints, such as tests not to touch or patterns to follow
- what to return (each role already defines its output format)

Keep briefs specific and short. Paste the relevant findings or test names, not the whole conversation.

## When the decider is unavailable

If every decision abstains with `backend_error`, the decision model is not running. Tell the human once (for example "start Ollama with `ollama serve`"), then carry on making each call yourself from the rubrics and recording outcomes as usual.
