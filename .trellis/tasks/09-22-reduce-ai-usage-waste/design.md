# Design: Reduce AI usage waste

## 1. Boundaries

This change keeps complete records authoritative in SQLite and the repository's
explicit raw APIs. It changes only the projections placed in default model
input and on-demand chat-read results.

```text
SQLite complete rows
  -> GroupMessageRepository compact reference query
  -> GroupHistoryMessage domain projection
  -> compact chat JSON (10 preceding + current request)
  -> AI backend

SQLite complete rows
  -> GroupChatReadSession
  -> compact page by default / complete page on explicit raw request
  -> one pending-page delivery through ChatRuntime
  -> AI backend
```

No database migration is required. Web/admin readers and `get_full_*` methods
continue to receive complete wire records.

## 2. Compact record contract

Extend `GroupHistoryMessage` with explicit model-relevant state:

- `image_attachment_indexes: tuple[int, ...]`
- `recall_state: str`
- `progress: bool`

Add a single logic-layer serializer for the compact record keys required by
R2. `ChatAgent`, compact repository history pages, and tests use this owner
instead of defining local whitelists.

`list_for_reference()` and `get_current_for_reference()` select and decode only
the current `group_messages` row. They must not call `_wire_record()`. Complete
attachment/event/delivery expansion remains behind `get_full_message()`,
`get_full_record()`, and raw `query_history()`.

`ChatAgent` emits protocol version 4:

- `reference_material.group_messages`: compact preceding records.
- `current_message`: compact current metadata with `content` replaced by the
  normalized `ConversationRequest.user_input`.
- If persistence lookup fails, `current_message` remains a stable compact object
  with nullable identity/metadata and the normalized content.
- The former top-level `user_input` and `fields_complete` claims are removed.

Image-action authorization uses explicit `has_image`, attachment indexes, and
`recall_state`, not the removed wire object.

## 3. History query modes and deduplication

Add a boolean `raw` option at the chat-read boundary and CLI script. The default
is false. `versions=true` or `events=true` implies complete/raw message records
because those diagnostics need original provenance. The repository keeps its
existing complete default for non-chat callers and accepts an internal compact
selection used by `GroupChatReadSession`.

`GroupChatReadSession` normalizes all supported filter defaults into a stable
key. A per-session task map shares the first result, including failures, for an
equivalent key. Only a newly created task increments the three-page budget and
updates retrieved record/image IDs and range metadata. Close cancels unfinished
history and image tasks and clears all request-local indexes.

`ChatRuntime` replaces cumulative `_history_context` with a pending-page queue.
The `/history` bridge appends each newly completed page once. The next
`/v1/responses` request atomically drains that queue into one untrusted user
message. Later requests receive only pages read since the preceding request.
Separate counters preserve page numbering after the cumulative list is removed.

The Responses backend continues its normal bounded function-call transcript,
but receives compact tool results by default. This task does not change it to a
`previous_response_id` conversation model.

## 4. Turn observability

Add a required typed `purpose` argument to `AIBackend.run()` and update every
production caller. Both backends use a shared metrics helper that:

1. creates an opaque random request ID and records monotonic start time;
2. measures UTF-8 model-input bytes without retaining the text;
3. normalizes only integer token fields from `AgentTurnResult.usage`;
4. logs exactly one `event=ai_turn_finished` record on success, mapped failure,
   cancellation, or unexpected exception;
5. never includes prompt/provider content or business identifiers.

Responses counters come from a per-run mutable counter passed through the tool
loop. Codex counters use exact `ChatRuntime` proxy/read values when a chat
runtime is active; unavailable direct-SDK provider counts are logged as
`unknown`, not guessed. Reasoning effort records the actual existing selection
logic without changing it, keeping P3 out of scope.

`GroupChatReadSession` exposes read-only count properties needed by either
backend. These are captured before the owning agent closes the session.

## 5. Output cap

`CodexSDKBackend` accepts the configured maximum and passes it into
`ChatRuntime`. The proxy sets or lowers `max_output_tokens` on forwarded chat
Responses requests. It never raises a smaller limit already chosen upstream.

`ResponsesBackend` keeps its existing hard cap. Direct Codex SDK turns cannot
receive a hard cap through installed SDK 0.1.11 `ThreadOptions` or `TurnOptions`;
their metric uses `output_limit_mode=unsupported_direct_sdk`. No unsupported
SDK field or unverified config key is added.

## 6. Compatibility and documentation

- Change the configured initial history limit from 50 to 10 in bootstrap.
- Update chat instructions, README, `.trellis/spec/backend/chat-read.md`, and
  `.agents/skills/aiqq-chat-read/SKILL.md` together.
- Update `read_history.py` and the Responses read-tool schema with `raw`.
- Retain protocol v3 parsing nowhere; this is an internal producer/consumer
  payload and can advance atomically to v4.
- Preserve all group-scope, image decoding, isolation, and context overflow
  behavior from the current spec.

## 7. Risks and rollback

- Compact projection could omit a field needed by image actions. Mitigation:
  make image indexes, recall state, progress, and reply summaries first-class
  fields and cover them end to end.
- One-shot pending-page delivery relies on the Codex response chain retaining
  accepted input. Mitigation: regression-test exact forwarded request payloads
  across multiple model/tool rounds; retain bounded explicit re-query on a new
  cursor.
- Metrics can accidentally leak text through exceptions. Mitigation: log only
  enumerated status/failure names and exception class names, never exception
  strings or request bodies.
- If regressions appear, compact serialization and pending-page delivery can be
  reverted independently without reverting persistence. No migration rollback
  is needed.

## 8. Deferred work

Model/reasoning/capability routing and same-user cancellation semantics remain
P3/P4 follow-up tasks. They must not be folded into this implementation while
touching adjacent bootstrap or handler code.
