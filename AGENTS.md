# Agent Instructions

Follow these rules for all future work in this repository.

- Do not use em dashes in prose, documentation, comments, or code comments.
- Write all code, code comments, identifiers, commit messages, and technical
  documentation in English.
- Keep documentation updated whenever behavior, configuration, architecture, or
  user-facing workflows change.
- Prefer small, focused changes that match the existing project structure.
- Verify backend changes with lint and compile checks when practical.
- Verify frontend changes with typecheck when practical.

## Agent and model routing

Use specialized subagents when the task matches one of the roles below.
The main agent owns task classification, coordination, final verification, and the final response.

### Sol architect

Delegate to `sol_architect` for:

- Complex or ambiguous planning
- Architecture and system design
- Deep code review
- Security-sensitive analysis or implementation
- Difficult debugging or unclear root-cause analysis
- Concurrency, networking, persistence, authentication, or distributed systems
- Large implementations with multiple interacting components
- Protocol, schema, or compatibility changes
- Changes with significant regression risk

The `sol_architect` agent should inspect the relevant code, analyze tradeoffs, and produce a concrete plan before high-risk implementation.

### Terra worker

Delegate to `terra_worker` for:

- Routine implementation
- Small or medium bug fixes with a reasonably clear cause
- Local refactoring that follows an existing pattern
- Test creation and test maintenance
- Mechanical migrations
- Straightforward maintenance
- Bounded implementation steps from an approved plan

The `terra_worker` agent should keep changes focused, follow existing conventions, and verify the affected behavior.

### Luna editor

Delegate to `luna_editor` for:

- Documentation changes
- Small, mechanical code changes
- Test maintenance with an established pattern
- Formatting and narrowly scoped cleanup
- Small fixes with a confirmed root cause

The `luna_editor` agent uses `xhigh` despite the smaller task size. It must preserve the quality floor and must not be used for ambiguous or architectural work.

### Escalation rules

Start with `terra_worker` only when the task is clear, bounded, and follows an established pattern.

Escalate to `sol_architect` when any of the following is true:

- The root cause remains unclear after initial investigation
- Multiple services, runtimes, or subsystems are involved
- Architectural judgment or a new abstraction is required
- Requirements are ambiguous, incomplete, or conflicting
- The change affects authentication, authorization, networking, persistence, security, concurrency, or protocol compatibility
- The implementation has significant data-loss, security, or regression risk
- The proposed change requires a broad rewrite

If `terra_worker` discovers one of these conditions, it must stop expanding the scope and report the evidence to the main agent for escalation.

### Coordination rules

- The minimum allowed `model_reasoning_effort` for every role is `high`.
- `sol_architect` may use `high` or `xhigh`; use `xhigh` by default for the most demanding work.
- `terra_worker` must always use `xhigh`.
- `luna_editor` must use `xhigh`.
- A lower Terra effort is not allowed.
- Delegate only concrete and bounded subtasks.
- Use parallel subagents only when their work is independent.
- Avoid parallel edits to the same files or tightly coupled code paths.
- For complex work, let `sol_architect` analyze and plan first, then delegate approved bounded implementation steps to `terra_worker` when appropriate.
- The main agent must wait for required delegated work before presenting the final result.
- The main agent must review subagent output against the actual repository state.
- The main agent owns final integration, verification, and user-facing communication.
- Do not delegate trivial work when coordination would cost more than doing the work directly.

### Failure and fallback behavior

- If a named custom agent is unavailable, continue with the main agent using the same role instructions and report the fallback.
- If a configured model or effort is unavailable, use the closest supported configuration with at least `high` effort and report the substitution.
- Never silently broaden permissions, disable safety controls, or skip required verification because a delegated agent failed.
