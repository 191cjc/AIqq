# Implementation Plan: Reduce AI usage waste

## Phase A - Compact initial context

- [x] Add the shared compact chat-record serializer and explicit
      image/recall/progress fields to the logic model.
- [x] Change reference/current repository queries to avoid complete wire
      expansion while preserving complete raw APIs.
- [x] Change `ChatAgent` to protocol v4, one canonical current-content field,
      compact image authorization, and updated instructions.
- [x] Set the bootstrap history window to 10.
- [x] Replace complete-context tests with compact-contract, missing-current,
      semantic preservation, and >=90% payload-reduction regressions.

## Phase B - History tool compaction and one-shot delivery

- [x] Add compact/default and explicit raw history modes through repository,
      service, Responses tool schema, packaged script, and skill instructions.
- [x] Normalize history filters and deduplicate equivalent per-session reads
      without double-charging the page budget.
- [x] Replace cumulative ChatRuntime history injection with an atomically
      drained pending-page queue and independent counters.
- [x] Cover compact versus raw/version/event results, repeated reads, multi-page
      request payloads, failures, close/cancellation, and group isolation.

## Phase C - Metrics and output cap

- [x] Add typed AI turn purposes and a shared content-free metrics helper.
- [x] Label chat, prompt audit, image audit, NovelAI planning/authoring, and Codex
      image-driver calls.
- [x] Instrument Responses and Codex success/failure/cancellation paths with
      usage normalization and observable provider/tool/history/image counters.
- [x] Pass the configured output cap into Codex backend construction and enforce
      it on proxied chat requests; report unsupported direct-SDK cap mode.
- [x] Add log-capture tests proving fields, counts, status handling, and absence
      of prompt/content/secrets.

## Phase D - Contract sync and validation

- [x] Update README, `.trellis/spec/backend/chat-read.md`, chat instructions,
      and `.agents/skills/aiqq-chat-read/SKILL.md` to the same compact-10 and
      compact-default/raw-on-demand contract.
- [x] Run focused tests:

```bash
uv run python -m unittest \
  tests.unit.services.test_agents \
  tests.unit.services.test_chat_complete_context \
  tests.unit.services.test_chat_read \
  tests.unit.services.test_chat_runtime \
  tests.unit.services.test_responses_backend \
  tests.unit.services.test_codex_sdk_backend \
  tests.unit.database.test_group_messages \
  tests.unit.database.test_message_history \
  tests.unit.test_bootstrap
```

- [x] Run the complete quality gate:

```bash
uv run python -m unittest discover -s tests/unit
uv run python -m compileall -q src tests/unit
git diff --check
```

- [x] Inspect the final diff for accidental prompt/raw-record logging and for
      unrelated P3/P4 behavior changes.

## Risky files and rollback points

- `src/aiqq/database/group_messages.py`: keep complete wire methods unchanged;
  rollback compact query branches independently if raw/admin tests regress.
- `src/aiqq/services/ai/chat_runtime.py`: pending-page drain and proxy output cap
  affect live Codex traffic; validate exact forwarded JSON before full-suite
  testing.
- `src/aiqq/logic/ports.py`: the required purpose parameter touches every
  backend caller; use search to prove no production call site is omitted.
- No schema migration, deployment, or service restart is part of this task.

## Validation Results (2026-09-23)

- Focused command above: 87 tests in 5.665s, OK.
- `uv run python -m unittest discover -s tests/unit`: 380 tests in
  17.504s, OK.
- `uv run python -m compileall -q src tests/unit`: exit 0.
- `git diff --check`: exit 0.
- No standalone linter or static type checker is configured in `pyproject.toml`;
  compileall and the complete unit suite are the available project checks.
- Final source review found no prompt/raw-record logging and no P3/P4 behavior
  changes.
