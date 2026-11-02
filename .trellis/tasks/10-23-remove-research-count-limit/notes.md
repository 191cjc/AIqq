# Change boundary

The behavior lives in the packaged prompt auditor and its strict output contract.
Remove `complex_research` from `services/agents/prompt_audit.py`,
`services/agents/schemas.py`, `logic/models.py`, and the rejection presentation
in `logic/conversation.py`. Update unit coverage in `test_agent_schemas.py` and
`test_conversation.py`, plus README and backend chat-read spec.

Do not edit the retired root-level implementation or its retired tests. Do not
change provider search budgets, timeouts, image limits, or unrelated existing
worktree changes. No refactor or commit is needed for this small behavior change.

Prior investigation confirmed the active service runs `python -m aiqq.main` with
an editable install pointing to `src/aiqq`. Recent stored refusals explicitly cite
seven-character rankings and nine-chapter lookups as at least five targets.

## Completed verification

- Removed the category, instruction and rejection copy across all four production
  owners; ordinary batch research cannot be rejected solely for target/search count.
- Focused audit schema/workflow suite: 22 tests passed.
- `.venv/bin/python -m unittest discover -s tests/unit -t .`: 460 tests passed.
- `.venv/bin/python -m compileall -q src tests/unit` and `git diff --check`: passed.
- Independent review against the before-task snapshots found no issues. No lint or
  type-check command is configured in this repository.
- Two bounded live audits using production configuration returned `allowed=true`,
  `category=safe` for the previously rejected seven-character ranking and nine-chapter
  date lookup. These probes sent no QQ messages and performed no web searches.
- Confirmed zero service child processes and zero pending replies before restarting
  `aiqq.service` at 2026-10-23 11:18:25 Asia/Shanghai. Health returned `ok`, with QQ
  connected and the message database open; new service PID was 1605763.
- Existing worktree edits were preserved; changes are uncommitted.
