# Dispatching roles from any harness

Roles run as separate processes through the `orchestra` CLI, using the command template in `[harnesses.generic]` of `~/.config/orchestra/orchestra.toml` (by default `claude -p --model {model}`). The CLI pipes the role's instructions and your brief to the command on stdin and prints its output.

Write the brief to a file, then run:

```bash
orchestra role run <role> --harness generic --brief-file "$BRIEF" --repo "$(pwd)"
```

- **Run reviewers in parallel** with background jobs, one output file each, then `wait`.
- **Escalate** by adding `--tier strong` when running `implementer`.
- `{model}` in the template becomes the harness's strong or fast model. `{sandbox}` becomes `read-only` or `workspace-write`, depending on whether the role may edit files.
- `orchestra role run ... --dry-run` prints the exact command without running it.
- If your harness can spawn its own subagents, you can use those instead. Print a role's instructions with `orchestra role show <role>` and use them as the subagent's system prompt.
