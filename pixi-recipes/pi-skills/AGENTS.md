# Global Agent Instructions

## Delegate to subagents by task shape

Don't do open-ended or specialized work inline when a subagent fits. Pick by
task shape:

| Task | Agent | Why |
|---|---|---|
| Open-ended research, multi-step search, locate code/symbol/string across files/repos/external sources, uncertain first match | `Explore` (read-only, cheap, compact return) or `general-purpose` if mutation likely | Keeps large search noise out of main context; returns concise finding |
| Designing implementation strategy, identifying critical files, sequencing, trade-offs | `Plan` (read-only architect) | Returns step-by-step plan; you decide and verify, don't synthesize from scratch |
| Implementing from a detailed, self-contained spec (paths, line numbers, exact change) | `programmer` (deepseek-v4-flash-free, has edit/write) | Offloads mechanical coding + test runs from main context |
| Code review of a diff/changes, gate before reporting done or pushing | `reviewer` (read-only) | Independent pass; catches what the implementer missed |

## Signals that say "delegate, don't do it inline"

- You are not confident the first search will hit the right match.
- The task spans the codebase or multiple repos / external sources.
- Each result only informs the next query (iterative narrowing).
- Web fetches will dump large payloads (READMEs, full pages) into context.
- The work is mechanical coding from a spec you already fully understand.
- You just wrote/edited code and want a gate before reporting it done.

## Rules

- **Never delegate understanding.** Write prompts that prove you understood:
  file paths, line numbers, what specifically to change or find. Don't write
  "based on your findings, fix it" — that pushes synthesis onto the subagent.
- **Trust but verify.** A subagent's summary describes intent, not outcome. When
  it writes/edits code, read the actual changes before reporting work as done.
  When it returns paths from a search, read the specific lines to confirm.
- **Self-contained prompts.** Subagents don't see this conversation. Give them
  everything: goal, why, what's ruled out, surrounding context, expected output
  shape. Brief like a smart colleague who just walked in.
- **Parallel when independent.** Multiple independent subagents → single
  message, multiple tool calls, `run_in_background: true` on each. Foreground
  only when you need the result to proceed.
- **Foreground vs background.** Foreground = need result to proceed. Background
  = genuinely independent work to do meanwhile. Don't poll/sleep waiting.
- **Verify, don't redo.** After a search subagent returns paths, verify the
  specific files/lines yourself; don't rerun the whole search inline.

## Anti-patterns to avoid

- Running 4+ web searches / git clones / greps inline in the main context to
  chase down where a string or symbol is defined. That is exactly what
  `Explore` / `general-purpose` exist for.
- Writing code inline from a vague plan when `Plan` could sequence it and
  `programmer` could implement it.
- Reporting code as done without an independent `reviewer` pass on non-trivial
  edits.
- Delegating with a vague prompt ("find the bug") instead of a specific one
  ("auth middleware token expiry check uses `<` not `<=` at src/auth.ts:42").
- Doing the same search yourself that you already delegated to a subagent.
