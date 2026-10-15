# Dispatching roles in Codex

Roles run as separate `codex exec` processes through the `orchestra` CLI. It picks the model from the role's tier, uses a read-only sandbox for roles that cannot write, and prints the agent's final message.

Write the brief to a file, then run:

```bash
orchestra role run <role> --harness codex --brief-file "$BRIEF" --repo "$(pwd)"
```

- **Run reviewers in parallel** with background jobs, one output file each, then `wait`:

  ```bash
  for r in generalist security; do
    orchestra role run "reviewer-$r" --harness codex --brief-file "$BRIEF" --repo "$(pwd)" > "$OUT/$r.md" &
  done
  wait
  ```

- **Escalate** by adding `--tier strong` when running `implementer`.
- Subagent runs can take several minutes, so allow a long command timeout.
- `orchestra role run ... --dry-run` prints the exact command without running it.
