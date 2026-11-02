# Implementation

- [x] Add isolated provider storage while retaining the original table default.
- [x] Add provider-specific style workflow messages and image style-read errors.
- [x] Wire repositories and add Nai v5 style parsing/routing/menu/panel entries.
- [x] Add meaningful storage, command, generation and bootstrap regression tests,
      including legacy-only settings, independent clear/replace and persistence.
- [x] Update README and the NovelAI style spec with the isolation contract.
- [x] Run focused tests, full unit discovery and git diff --check.
- [x] Restart aiqq.service and verify health and panel synchronization.

Keep edits limited to style storage/workflow, bootstrap, QQ commands/router/UI/
panel, their tests and documentation. Do not revert existing workspace edits or
publish secrets. Keep all code changes uncommitted for user review.

## Verification (2026-10-08)

- Focused style/storage/routing/bootstrap tests: 73 passed; final panel/UI subset:
  13 passed after shortening descriptions.
- Final full unit discovery: 418 passed in 19.724s; git diff --check passed.
- Live database contains both independent style tables (read-only check, no
  member IDs, prompts or credentials printed).
- QQ panel successfully updated with six commands; final service startup reports
  event=qq_panel_synchronized action=unchanged, confirming idempotent sync.
- Final restart: 2026-10-08 15:14:52 CST, PID 535978, active/running.
- /health: status=ok; QQ connected; NovelAI and Nai v5 configured=true, ready=true.
- Existing workspace edits retained. Implementation is complete and uncommitted;
  no unrelated task was archived and no work was reset.
