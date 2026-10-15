# Dispatching roles in Claude Code

Each role is installed as a subagent named `orchestra-<role>`, for example `orchestra-test-author` or `orchestra-reviewer-security`. Its model and tools come from the role's tier and capabilities.

- **Dispatch** a role with the Agent tool, using `subagent_type: "orchestra-<role>"` and the brief as the prompt.
- **Run reviewers in parallel** by making all the Agent calls in a single message.
- **Escalate** by passing `model: "{{strong_model}}"` to the Agent tool when dispatching `orchestra-implementer`. This overrides the role's fast model for that call.
- The subagent's final message is its report. It follows the output format defined by its role.
