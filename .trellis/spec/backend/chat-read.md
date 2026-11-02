# Complete Chat Records and On-demand Vision

## 1. Scope / Trigger

Read when modifying group gateway capture, message persistence/migrations, chat
serialization, the chat-read skill, Codex tool permissions, Responses read loops,
or image/delivery metadata. Packaged `src/aiqq` is authoritative.
Also read when changing ordinary input audit categories or chat admission.

## 2. Signatures

```python
audit = await prompt_auditor.audit(user_input)
await repository.add_gateway_event(event_type, envelope, raw_text=text, connection_id=id)
raw_event_mentions_bot(payload, bot_name=current_bot_name)
normalized_group_message_data(payload, bot_name=current_bot_name)
await repository.get_current_for_reference(group_id, message_id)
await repository.list_for_reference(group_id, before_message_id=message_id, limit=10)
await repository.query_history(group_id, **filters)
session = GroupChatReadService(repository=repository, downloader=images).create_session(group_id)
await session.read_history(**filters)  # compact by default; raw=True is complete
metadata, images = await session.read_image(record_id, attachment_index=0, version_id=None)
await backend.run(purpose="chat", ..., chat_read_session=session)
await session.close()
```

`GroupHistoryMessage` carries the semantic model fields, including image attachment
indexes, recall state, and progress state. `compact_chat_record()` owns the stable
model serialization whitelist; reference reads do not construct or retain a complete
wire object. `ConversationRequest` carries `group_id`, `current_message`, and
`history_has_more` independently of recent history. Complete wire objects remain
authoritative in SQLite and explicit raw/management APIs.

## 3. Contracts

- Ordinary input auditing uses `PromptAuditAgent.audit()` before history reads
  and chat. Its domain literal and strict output schema admit only `safe`,
  `adult_content`, `persona_override` and `too_complex`.
  Multiple independent questions/tasks in one message are allowed; their count
  alone must not become a rejection or be relabeled as `too_complex`.
  Ordinary batch lookups, comparisons, rankings and summaries have no research
  target-count limit. The number of targets, searches or item-by-item checks alone
  must not cause a rejection or be relabeled as `too_complex`. Genuinely excessive
  stages or deliverables remain subject to the `too_complex` criterion.
  An allowed response must use `safe`; rejected responses require a non-safe
  category and a nonempty reason. Invalid model output remains audit unavailable.
- Protocol v4 supplies at most the latest available 10 preceding same-group records
  plus one compact current-message object. The current request text appears only in
  `current_message.content`, including when persistence lookup is unavailable.
  Preserve progress/recalled records and their states. Do not normalize whitespace,
  clip text, or use the former character budget. `AIQQ_GROUP_HISTORY_MAX_CHARS` is
  ignored by this chat path.
- Both ordinary chat instructions and the Codex chat runtime identify
  `current_message.content` as the current user's task to answer or carry out,
  subject to the fixed role, tool permissions, output protocol and safety rules.
  History, quoted content, attachments and tool results are reference data to
  process for that task; embedded commands do not automatically become instructions
  or override fixed rules. A request to evaluate quoted text requires evaluation,
  not automatic execution of the quoted commands. Do not blanket-classify the
  current request as non-executable reference data.
- Compact records contain exactly `record_id`, `role`, `sender_name`, `sent_at`,
  `content`, `message_type`, `reply_summary`, `has_image`,
  `image_attachment_indexes`, `recall_state`, and `progress`. They never contain the
  raw database row, parsed payload, gateway events, signed attachment URLs, or
  delivery attempts. Complete raw records preserve future columns, unknown nested
  keys, arrays, null/false/zero/empty strings, and invalid JSON diagnostics.
- Gateway raw text commits before projection/business dispatch. Event receipts,
  message versions, attachment metadata and delivery attempts remain separate.
  Identity is `(group_openid, message_id)`. Duplicate/missing-field snapshots
  cannot erase earlier richer data; conflicting versions remain traceable.
  Recovery applies only local projections, never chat, quota, generation or send.
- A full-group `GROUP_MESSAGE_CREATE` dispatches when QQ marks a structured
  `mentions[].is_you=true` mention or when content, after leading whitespace,
  starts with the exact case-sensitive `@<current runtime bot name>` or
  `[@<current runtime bot name>]`. Brackets are a valid QQ-delivered display
  representation; never describe them as a malformed user mention. The runtime
  name comes from `robot.name` with `AiQQ` only as its empty-name fallback; never
  hard-code a deployed display name. Remove one matching leading mention before
  existing command/conversation routing. Mid-message, partial, old-name and alias
  text remain stored but do not dispatch. Raw/SDK delivery of one message still
  relies on the shared `(group_id, message_id)` handler deduplication.
- Raw and SDK ingress must share one normalization boundary before command
  parsing. Recognizing a mention as a trigger also requires removing that same
  leading representation from the business input. Native metadata can supply a
  confirmed bot username; SDK `GroupMessage` projections may lose mention
  `username`/`is_you`, so current runtime-name handling must work independently.
  Remove only the leading mention once, preserving subsequent mentions and
  internal whitespace/newlines. Do not mutate saved envelopes, message versions,
  or history display to fix routing. `IncomingGroupMessage` is the normalized
  internal input to `GroupMessageHandler`, not a raw QQ payload.
- Current attachments normally resolve to exact immutable source metadata.
  Directly nested image objects enriched across receipts instead carry explicit
  `metadata_source=current_projection`, `version_id=null` and actual
  `source_versions`; do not invent a source event containing the merged object.
  Keep the real URL-source attachment association for measured read metadata
  when available, and retain complete raw values in their original versions.
- Legacy migration preserves record IDs, original columns/payloads and custom
  indexes; adds `legacy_snapshot` versions. Never claim older lost envelopes or
  versions were reconstructed. Keep confirmed local recall monotonic. A successful
  administrator recall of a member's drawing request uses the trusted
  `LOCAL_MEMBER_RECALL_CONFIRMED` event through `mark_member_recalled`; original
  bot recall keeps `LOCAL_BOT_RECALL_CONFIRMED` and its bot ownership restriction.
  Each event applies only to its matching member/bot record in the same group.
  Preserve original content and versions, mark `recall_state=confirmed`, and
  replay confirmations arriving before either kind of create record without
  network, model, quota or send side effects. Unknown
  member lifecycle events are archived as unsupported; absence is unobserved,
  not proof that a member never recalled a message.
- Every history/image session is bound to one server-selected group. No arbitrary
  group, URL, DB path or local file parameters. History allows at most three distinct
  pages of 50 with stable record/version cursors. Omitted and explicit default
  filters normalize to the same request task and page-budget entry. Default history
  results are compact; `raw=true`, `versions=true`, or `events=true` returns complete
  diagnostic message records. Cancelling one waiter must not cancel a shared history
  task for equivalent callers; closing the session must cancel the underlying task
  and clear its request-local index. Image reads allow three sources,
  three sampled frames each, maximum nine visual inputs. Reject extra input;
  never silently slice to the first image.
- History sender/keyword/time/record filters select the same messages for
  version and event queries. Unsupported events identifying a known message are
  associated by group/message identity, not only successful processing.record_id.
  Raw events without a current projection remain accessible in group/event or
  explicit message-ID queries. A legacy current row acquires its first saved
  raw-event association when an original receipt becomes available later.
- Input-only decoder supports JPEG/PNG/WebP/GIF; 20 MiB download, 16M pixels per
  image/frame, at most 120 animation frames and 64M cumulative decoded pixels.
  Keep generation-output validation separate. Sampling reports source/frame
  indices and times. URLs and paths alone are not visual evidence.
- No receive-time prefetch or persistent image originals/cache. Download only on
  read, retain request-local bytes/files until viewing, then clean success,
  failure and cancellation; orphan cleanup requires an owner marker and verifies
  the exact creator process. Existing QQ `/media` delivery TTL remains separate.
- Codex chat alone stages the whole `aiqq-chat-read` skill and permits command
  execution/view_image. Auditor and generation profiles keep their restrictions.
  The CLI gets scoped bridge tokens; the actual provider key remains in the
  parent bridge. Landlock ABI >=4 and libseccomp enforce inherited filesystem
  allowlists: this turn and system runtime paths only, no host DB/config/proc or
  other turns. Fail closed if isolation cannot start. TCP rules restrict bridge
  ports, not destination addresses; do not describe this as loopback-only.
- Installed CLI 0.152.1 emits `command_execution` items, but no separate JSONL
  item for successful view_image; actual subsequent Responses input contains
  image data in tool output. Verify pixel transport, not an invented event name.
- Codex stages each newly read history page once and drains pending pages only into
  the next provider request; accepted pages remain available through the provider
  conversation chain but are not newly appended to every later request. Responses
  compatibility uses bounded read function calls and real input_image content,
  returning compact tool records by default. Chat read never enables native
  generation or charges image quotas.
- Every backend turn labels its purpose and emits one content-free terminal
  `ai_turn_finished` metric with opaque request ID, backend/model/effort, model-input
  bytes, elapsed time, status, normalized usage, and observable provider/tool/history/
  image counters. Never log prompt text, raw provider bodies, signed URLs, credentials,
  or group/member IDs. The configured output cap applies to Responses and the Codex
  chat proxy; direct Codex SDK turns report `unsupported_direct_sdk`.
- QQ sender records actual upload/send attempts separately. Only a confirmed
  message ID creates a sent-message row. Image metadata comes from in-memory
  source and compressed delivery bytes: MIME, dimensions, byte size, SHA-256,
  source/provider when known. No historical metadata is invented.

## 4. Validation and Error Matrix

| Condition | Required outcome |
| --- | --- |
| Several independent questions, no other rejection criterion | Preserve the complete input and proceed to history/chat |
| Seven-character ranking or nine-chapter date lookup, no other rejection criterion | Preserve the complete input and proceed to history/chat regardless of target count |
| Current user directly requests a task | Answer or carry out the task within the fixed rules |
| Current user asks to evaluate quoted instructions | Evaluate the quotation without automatically executing its embedded commands |
| Removed `too_many_questions` or `complex_research` output category | Invalid audit output; both are absent from the advertised schema and rejection copy |
| Valid audit rejection or audit failure | Stop before history reads/chat and retain the existing reason/failure presentation |
| Source HTTP 404/410 | `expired`: 这张图片链接已过期或失效，请重新发送图片。 |
| Unknown HTTP 400 / network error | `download_failed`, no expiry claim |
| HTTP 401/403 | `access_denied` |
| Download timeout | `timeout`, preserve retry/resend suggestion |
| Missing/foreign record or attachment | `missing`, no cross-group disclosure |
| Confirmed recalled message | `recalled`, no download of any old version |
| Invalid/oversized input | `invalid_image` / `too_large` |
| Per-request limit | Explicit `budget_exhausted`, no silent truncation |
| Actual model context limit | `AgentContextTooLarge`, clear capacity text, no shortened retry |
| Projection failure/cancellation | Durable raw event remains recoverable; no business replay |
| Leading exact `@<runtime name>` or `[@<runtime name>]` | Persist, strip one prefix, then dispatch through the existing handler |
| Mid-message / partial / stale text name | Persist without business dispatch |
| Isolation unavailable | Chat unavailable; no unrestricted-shell fallback |

Do not log prompts, full events, signed URLs, group/member identifiers or keys in
new diagnostics. Model tool progress is fixed safe text, not command output.

When every attempted image read has completed with an error, ChatAgent preserves
the model's full answer and appends any missing fixed read-error messages. A
homogeneous error also supplies the QQ summary verbatim; mixed failures retain
their separate reason labels (expiry applies only to the expired subset). Do not
replace independent answer content, a generation/edit action, or partial image
success. Pending/cancelled reads are not confirmed failures. This prevents model
summarization from hiding the original-image expiry reason again.

## 5. Good / Base / Bad Cases

- Good: a message asking two unrelated ordinary questions reaches chat intact.
- Good: a seven-character ranking or nine-chapter date lookup reaches chat intact.
- Bad: asking users to submit one question at a time or classifying the question,
  research-target or search count alone as excessive complexity.
- Good: 10 compact records preserve full semantic text, reply/image/recall/progress
  state and current identity while reducing a full-wire fixture by at least 90%.
- Good: an equivalent repeated history query performs one repository read and uses
  one page slot; a newly read second page does not re-append the first page.
- Good: a leading textual `@<runtime name>` is persisted, normalized once and
  dispatched; a simultaneous native callback is removed by handler deduplication.
- Good: a valid `[@<runtime name>] /NovelAI生图 ...` routes to the NovelAI command
  with its complete argument, regardless of raw/SDK callback order.
- Base: a historical GIF is selected, downloaded on demand, sampled into three
  real visual inputs, then removed at request completion.
- Bad: matching a hard-coded deployed bot name, triggering on a mid-message name,
  or bypassing raw persistence to dispatch textual mentions.
- Bad: interpreting a path as image understanding; publishing only seven known
  fields; downloading every incoming image; restoring an old DB over new messages.

## 6. Required Tests and Deployment Checks

Verify ordinary audit schema/decision invariants and admission with a mocked
safe audit for multiple questions, seven-character rankings and nine-chapter
date lookups. Mocked admission tests verify the pipeline, not live model compliance.
Keep valid rejection/failure tests proving that history/chat are skipped.
Assert the current-task/reference-data boundary in both assembled chat prompts.
Prompt contract tests do not establish model compliance; bounded live checks must
cover both a direct task and evaluation of quoted commands.
When asked to wait for an active question, check
its terminal turn event, pending reply markers and service child processes
before restarting; a code edit alone does not change the running process.

Assert compact DB-to-model semantics and >=90% payload reduction while explicit raw
queries preserve future columns, deep unknown fields, more than 20 attachments,
false/null/zero, and multiline text; current image-only @,
runtime-name text @ (leading whitespace, no separator, empty/image-only body,
partial/old/mid-message rejection), native angle-ID and bracketed mentions,
preserved later mentions, actual SDK projections and both raw-first/SDK-first
deduplication with command routing asserted; stable
same-group history/version paging; equivalent history failure reuse, isolated waiter
cancellation, and close-time task cancellation; legacy migration with
unchanged IDs/all original columns; raw commit + failure/restart recovery; late
local recall; input animation budgets and cleanup; real SDK offline tool
execution followed by pixel input; file/symlink/proc/other-turn denial; bridge
auth and fixed group; multiple images; genuine context overflow; actual QQ
source/delivery hashes and failed attempts without false success rows.

Run packaged unit discovery, compileall and diff checks. A bounded live vision
probe uses only two prepared cards with text present exclusively in pixels, no
generation/QQ sends. Back up SQLite with its backup API (WAL included); dry-run
migration on a copy before deployment. Rollback retains new tables/messages and
the group-scoped writer: the pre-R11 `ON CONFLICT(message_id)` writer is not
compatible after global uniqueness becomes the composite key.

Native chat verification must also run under the production systemd properties,
including `UMask=0077`, `PrivateTmp=true`, the read-only mounts, and kernel/process
restrictions. Neither `codex --version` nor HTTP health proves a model turn can
initialize. The September 2026 outage passed terminal-based native/vision tests
but failed app-server initialization under the service umask before any upstream
request: Codex's conditional `fchmod` conflicted with the inherited seccomp rule.
Keep a restrictive-umask native regression and repeat the actual service-mode
tool/vision check when changing confinement. Diagnose this known CLI denial with
the fixed log reason `cli_initialization_permission_denied`; never dump real CLI
stderr to obtain it.

For CLI 0.152.1, set `umask(0022)` only in the forked, already confined child
immediately before exec. Keep parent/supervisor `0077` and outer turn directories
`0700`. This avoids the conditional `fchmod(fd, 0644)` while keeping the metadata
syscall denials, since Landlock does not restrict chmod/chown. Test the actual
parent and child creation modes and host/cross-turn denial together; never fix
this by relaxing the service's umask or permitting unrestricted metadata edits.

### Standalone web search

The native CLI's standalone `web.run` uses `POST /v1/alpha/search` against its
configured provider base. Register an exact authenticated bridge route when web
search is enabled and forward to the configured parent-owned provider's
`/alpha/search`. Keep the real provider key out of the child, preserve native
headers and search payloads, and do not add chat history or require a Responses
`model` field in search requests. The read token must not authorize this route.
No arbitrary proxy target or unrestricted outbound-network fallback is allowed.

Allow at most16 standalone search requests per turn,64 MiB per request,16 MiB
per response and60seconds per call. Native search requests can carry the entire
conversation; use the existing Responses capacity instead of a small query-only
limit, and test >256KiB history preservation. Log
fixed failure reasons/statuses only, without search text, URLs, tokens, headers
or upstream error bodies. A completed model turn can still contain a failed
search; lack of a `CodexExecError` is insufficient evidence of search success.

Exercise an actual native `web.run` invocation and result on the next model
request under full production systemd restrictions. Merely enabling the search
feature or seeing its tool declaration does not validate search. The September
2026 missing-route regression returned local404 and the native tool result
`aborted`, while both surrounding `/responses` calls succeeded. Validate the
real provider with a bounded web search, result opening and image search before
claiming a search-path repair has restored all those operations.

## 7. Wrong vs Correct

Wrong: classify `current_message` together with history as reference-only data
whose instructions must never be followed. Correct: follow the current task in
`current_message.content` within fixed rules; if it asks to evaluate a quoted
command, evaluate that command without carrying it out.

Wrong: remove a count-based rejection instruction but leave `too_many_questions`
or `complex_research` in the model enum or rejection mapping, or relabel ordinary
batch queries as `too_complex`. Correct: update the instructions, strict schema,
domain category and presentation together, and preserve other checks.

Wrong: hard-code the current deployed bot display name or dispatch any message
that merely contains it. Correct: capture `robot.name` at readiness, require an
exact leading `@<runtime name>`, preserve the raw event first, normalize once and
reuse the existing handler and deduplication.

Wrong: authorize dispatch using `mentions[].is_you` but strip only `@name` and
`<@id>`, allowing a valid `[@name] /NovelAI生图 ...` to fall through to chat/GPT.
Correct: use the same leading-name matching logic to recognize and normalize
the bracketed form, and assert the final parsed command across both ingress paths.

Wrong: enable shell/view_image flags while retaining a handler that aborts every
command event, or claim read-only/workspace-write prevents host file reads.

Correct: align skill staging, instructions, tool events, actual pixels, and an
independently tested inherited isolation boundary. Prompt restrictions alone do
not enforce filesystem permissions.
