# Codex Model Routing

This repository uses project-scoped Codex custom agents to route work by complexity and risk.

## Roles

| Role | Model | Effort | Intended work |
|---|---|---|---|
| `sol_architect` | `gpt-5.6-sol` | `xhigh` | Architecture, deep review, difficult debugging, security, and high-risk changes |
| `terra_worker` | `gpt-5.6-terra` | `xhigh` | Bounded implementation, refactoring, tests, and routine bug fixes |
| `luna_editor` | `gpt-5.6-luna` | `xhigh` | Documentation, mechanical edits, test maintenance, and small confirmed fixes |

The main thread defaults to `gpt-5.6-sol` with `high` effort. It classifies work, delegates concrete subtasks, reviews agent output, performs final verification, and owns the final response.

## Configuration

- `.codex/config.toml` sets the project model default and limits agent execution to four open threads with one child level.
- `.codex/agents/` contains the three project-scoped custom agent definitions.
- `AGENTS.md` defines routing, escalation, coordination, fallback, and repository quality rules.
- No API key or other secret is required by this routing configuration.

Codex loads project-scoped configuration only for a trusted project. Runtime permission choices made in the parent session still apply to spawned agents.

## Activation

After changing routing configuration, start a new Codex session from the repository root. Ask Codex to summarize the active project instructions and available custom agents.

The client must expose custom agent selection when it spawns a child thread. A generic child task named `luna_editor`, `terra_worker`, or `sol_architect` is not equivalent to selecting that custom agent profile. Confirm the spawned thread reports the expected model and reasoning effort before relying on routing for production work.

Use these prompts for a direct routing check:

```text
Review the architecture of this authentication flow. Delegate the analysis to sol_architect, wait for its result, and summarize the risks and recommended plan. Do not implement changes.
```

```text
Delegate this bounded implementation to terra_worker. Use its configured xhigh reasoning, wait for the result, verify the changed file, and summarize what changed.
```

```text
Delegate this small documentation update to luna_editor. Keep the scope bounded, use xhigh reasoning, wait for the result, verify the changed file, and summarize what changed.
```

## Troubleshooting

- Confirm the project is trusted and the session started after the configuration change.
- Confirm each custom agent file defines `name`, `description`, and `developer_instructions`.
- Confirm the local Codex model catalog contains the configured model IDs and supports the configured effort.
- Confirm the client's subagent tool can select a custom agent type. If it only accepts a generic task name, update or change the client instead of treating the task name as successful model routing.
- Inspect a spawned child thread and verify its actual model and reasoning effort. The expected combinations are listed in the role table above.
- Keep every fallback at `high` effort or above and report any substitution.

Official references:

- [AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
