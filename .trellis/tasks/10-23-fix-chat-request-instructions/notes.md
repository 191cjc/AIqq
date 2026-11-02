# Change boundary

The conflicting text is in `CHAT_INSTRUCTIONS` and the Codex runtime's broad
"all chat records" reference-only wording. Edit only
`src/aiqq/services/agents/chat.py`, `src/aiqq/services/ai/codex_sdk.py`, relevant
existing tests in `tests/unit/services/test_agents.py` and
`tests/unit/services/test_codex_sdk_backend.py`, and the backend chat-read spec.
Keep payload v4 and its single current-request field. No routing/schema changes,
new configuration, count restrictions, audit changes or unrelated cleanup.
Preserve all preexisting dirty changes, including recent automatic image sizing.

The 16:12 request passed audit and explicitly asked to evaluate quoted text. Its
critical evaluation is not proof of an audit block or of the prompt conflict's
causal effect. Fix the demonstrated instruction ambiguity without forcing a
different intent onto that original request.

## Completed verification

- Both ChatAgent instructions and the Codex chat runtime now explicitly follow
  `current_message.content` subject to fixed role/tool/output/safety rules. History,
  quotations, attachments and tool results remain reference data.
- Existing ChatAgent assembly assertions and a new staged Codex `AGENTS.md` test
  cover the corrected boundary. The focused suite passed 19 tests.
- Full packaged unit discovery passed 467 tests in 21.003 seconds. Compilation of
  `src` and `tests/unit`, plus `git diff --check`, passed. No lint/typecheck command
  is configured.
- Two live probes used the actual ChatAgent and Codex chat-read runtime, production
  model configuration, a temporary synthetic database and disabled web search.
  The direct multi-part task correctly returned 42, hello = 你好, and 31 despite
  competing history instructions. The quote-analysis task identified 17+25=99 as
  incorrect and returned 42 without executing the quotation's competing command.
  No QQ messages were sent by these probes.
- Independent review found no code issues and added the precise wrong/correct
  example to the spec.
- Confirmed no service child processes or pending replies before restarting at
  2026-10-23 16:27:11 Asia/Shanghai. New PID 1688396; health `ok`, QQ connected,
  message database open.
- All preexisting worktree changes were preserved; changes remain uncommitted.
