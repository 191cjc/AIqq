# Reduce AI usage waste

## Goal

Reduce unnecessary AI input context and repeated history injection in AiQQ's
ordinary group-chat path, and make logical AI turns measurable, while
preserving public-group safety review, image-pixel verification, per-group
isolation, and request cleanup.

## Background

- `AI_USAGE_WASTE_INVESTIGATION.md` measured an average initial chat payload of
  553,671 bytes with complete wire records versus 12,843 bytes for a semantic
  projection, approximately a 43x difference.
- `ConversationWorkflow` requests up to 50 preceding messages
  (`src/aiqq/logic/conversation.py:112`), and `ChatAgent` serializes every
  message's complete wire value plus a complete current message alongside
  duplicated `user_input` (`src/aiqq/services/agents/chat.py:83`).
- Complete wire records include the database row, encoded and decoded payload,
  attachments, gateway events, and delivery attempts
  (`src/aiqq/database/group_messages.py:694`,
  `src/aiqq/database/group_messages.py:715`).
- `ChatRuntime` accumulates history pages and appends every accumulated page to
  every later provider request (`src/aiqq/services/ai/chat_runtime.py:157`,
  `src/aiqq/services/ai/chat_runtime.py:248`).
- Both AI backends return usage data, but agent consumers discard it. The
  configured output limit reaches `ResponsesBackend`, but the installed Python
  Codex SDK 0.1.11 exposes no output-limit thread or turn option.
- `.trellis/spec/backend/chat-read.md` currently requires 50 complete records
  in initial model context. This task intentionally replaces that model-input
  contract without changing complete persistence or management access.

## Requirements

### R1 - Non-sensitive AI turn observability

- Every production `AIBackend.run()` caller must label the turn purpose as
  `audit`, `chat`, `image_audit`, `novelai_plan`, `novelai_author`, or
  `image_generation`.
- Both backends must emit one terminal metric event for success, failure, or
  cancellation. It must include an opaque per-turn request ID, purpose, backend,
  model, effective reasoning effort when applicable, application input bytes,
  elapsed milliseconds, status, normalized input/cached/output tokens when
  available, and provider/tool/history/image counters when observable.
- Metrics must not include prompt text, message content, signed URLs, raw
  provider bodies, API keys, or raw group/member identifiers.
- `OPENAI_MAX_OUTPUT_TOKENS` must continue to cap Responses requests and must cap
  Codex chat requests that pass through AiQQ's Responses proxy. Direct Codex SDK
  turns must report that no supported hard-limit mechanism is available rather
  than silently claiming enforcement.

### R2 - Compact initial chat context

- Define one stable compact chat-record projection containing `record_id`,
  `role`, `sender_name`, `sent_at`, `content`, `message_type`, `reply_summary`,
  `has_image`, `image_attachment_indexes`, `recall_state`, and `progress`.
- Default initial history must contain at most 10 preceding same-group records.
- Preceding and current messages sent to the model must use the compact
  projection. Current request text must have one canonical representation and
  must not also appear inside a complete wire object.
- Initial compact history retrieval must not construct attachment/event/delivery
  wire expansions that will immediately be discarded.
- Preserve complete stored wire data and existing management/raw-history
  functions. This is a model-input contract change, not a schema migration.

### R3 - Compact, deduplicated on-demand history

- Ordinary chat-read history queries must return compact semantic records by
  default.
- An explicit raw query, and queries requesting versions or gateway events,
  must retain complete same-group records for diagnostics such as original
  payloads and delivery attempts.
- Equivalent query/filter/cursor requests within one chat-read session must
  share one repository operation and consume one history-page budget entry.
- The Codex chat proxy must append only history pages read since the previous
  provider request. Previously delivered pages must not be appended again.
- Existing three-page, group binding, image source/frame, and context capacity
  safety limits remain in force unless a focused regression proves a smaller
  model/tool limit preserves supported workflows.

### R4 - Preserved safety and isolation

- Keep the existing prompt safety audit; this task does not remove or replace
  it.
- Never describe an image without actual pixel input. URLs, attachment names,
  paths, and metadata remain insufficient visual evidence.
- Keep each chat-read session server-bound to one group and one model turn. Do
  not introduce cross-group, cross-user, or long-lived model state.
- Cancellation and closure must still terminate child work and clear
  request-local history/image state.

## Acceptance Criteria

- [ ] AC1: A full-record fixture produces an initial chat payload with no
      `payload`, raw database row, gateway event, or delivery-attempt objects;
      its encoded size is at least 90% smaller than the equivalent complete-wire
      payload.
- [ ] AC2: Initial history contains no more than 10 preceding records and still
      preserves reply summaries, image indexes/flags, recall/progress state,
      message type, role, timestamp, sender name, and record identity.
- [ ] AC3: The current request text appears exactly once in the model-input JSON,
      including when current-message lookup is unavailable.
- [ ] AC4: A second on-demand history page does not cause the first page to be
      newly appended to the next Codex provider request, and an equivalent
      repeated query does not consume another repository call or page slot.
- [ ] AC5: Default history-tool results are compact; explicit raw/version/event
      reads still return complete records and remain bound to the selected group.
- [ ] AC6: Successful, failed, and cancelled audit/chat/NovelAI turns produce
      content-free terminal metric events with normalized usage and all
      observable counters.
- [ ] AC7: The configured output cap reaches Responses requests and proxied
      Codex chat requests; direct SDK turns expose their unsupported cap mode in
      diagnostics.
- [ ] AC8: Existing safety rejection, real-pixel image reading, group isolation,
      cancellation, cleanup, and explicit context-capacity behavior continue to
      pass.
- [ ] AC9: README, backend spec, packaged chat-read skill, and tests describe
      compact-10 initial context and compact-default/raw-on-demand history.

## Out of Scope

- P3 model/capability routing: changing models or reasoning levels, adding an
  intent router, disabling web/chat-read/GPT-image capabilities by request, or
  preloading current images.
- P4 same-user concurrency: cancellation, replacement windows, queueing, or a
  `/取消` interaction.
- Removing or replacing the safety audit.
- Reusing long-lived model threads across turns, groups, or users.
- Destructive database changes or removal of complete wire records.
- Provider pricing estimates, billing reconciliation, service deployment, or
  restarting `aiqq.service`.

## Technical Notes

- P0-P2 remain one task because the metrics counters, compact projection, and
  page-delivery fix meet at the same backend/chat-read interfaces and require a
  single integrated regression pass.
- P3 and P4 should be planned as later tasks with their own quality and user
  interaction decisions.
- Official OpenAI documentation was inaccessible from the environment (HTTP
  403). The direct-SDK output-limit constraint is based on the installed
  `openai-codex-sdk 0.1.11` validated option models used by production.
