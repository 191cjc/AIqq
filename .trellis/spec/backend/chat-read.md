# Complete Chat Records and On-demand Vision

## 1. Scope / Trigger

Read when modifying group gateway capture, message persistence/migrations, chat
serialization, the chat-read skill, Codex tool permissions, Responses read loops,
or image/delivery metadata. Packaged `src/aiqq` is authoritative.

## 2. Signatures

```python
await repository.add_gateway_event(event_type, envelope, raw_text=text, connection_id=id)
await repository.get_current_for_reference(group_id, message_id)
await repository.list_for_reference(group_id, before_message_id=message_id, limit=50)
await repository.query_history(group_id, **filters)
session = GroupChatReadService(repository=repository, downloader=images).create_session(group_id)
await session.read_history(**filters)  # complete page dictionary
metadata, images = await session.read_image(record_id, attachment_index=0, version_id=None)
await backend.run(..., chat_read_session=session)
await session.close()
```

`GroupHistoryMessage.record` carries the complete wire object. The former small
domain fields remain for compatibility and image-action validation, not as the
production serialization whitelist. `ConversationRequest` carries `group_id`,
`current_message`, and `history_has_more` independently of recent history.

## 3. Contracts

- Protocol v3 supplies exactly the latest available 50 preceding same-group
  records plus the current message. Preserve progress/recalled records and their
  states. Do not normalize whitespace, clip text, or use the former character
  budget. `AIQQ_GROUP_HISTORY_MAX_CHARS` is ignored by this chat path.
- Each record contains `record` (every SQLite column, including original
  `payload_json`), `payload` (parsed JSON), separate `derived` metadata, attachment
  associations, latest event, and delivery attempts. Preserve future columns,
  unknown nested keys, arrays, null/false/zero/empty strings. Invalid JSON retains
  its original text and a parse error; non-object JSON is not replaced with `{}`.
- Gateway raw text commits before projection/business dispatch. Event receipts,
  message versions, attachment metadata and delivery attempts remain separate.
  Identity is `(group_openid, message_id)`. Duplicate/missing-field snapshots
  cannot erase earlier richer data; conflicting versions remain traceable.
  Recovery applies only local projections, never chat, quota, generation or send.
- Current attachments normally resolve to exact immutable source metadata.
  Directly nested image objects enriched across receipts instead carry explicit
  `metadata_source=current_projection`, `version_id=null` and actual
  `source_versions`; do not invent a source event containing the merged object.
  Keep the real URL-source attachment association for measured read metadata
  when available, and retain complete raw values in their original versions.
- Legacy migration preserves record IDs, original columns/payloads and custom
  indexes; adds `legacy_snapshot` versions. Never claim older lost envelopes or
  versions were reconstructed. Keep confirmed local recall monotonic. Unknown
  member lifecycle events are archived as unsupported; absence is unobserved,
  not proof that a member never recalled a message.
- Every history/image session is bound to one server-selected group. No arbitrary
  group, URL, DB path or local file parameters. History allows at most three pages
  of 50 with stable record/version cursors. Image reads allow three sources,
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
- Responses compatibility uses bounded read function calls and real input_image
  content, preserving complete tool records. Chat read never enables native
  generation or charges image quotas.
- QQ sender records actual upload/send attempts separately. Only a confirmed
  message ID creates a sent-message row. Image metadata comes from in-memory
  source and compressed delivery bytes: MIME, dimensions, byte size, SHA-256,
  source/provider when known. No historical metadata is invented.

## 4. Validation and Error Matrix

| Condition | Required outcome |
| --- | --- |
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

- Good: 50 full records exceeding 10,000 characters, plus a current attachment,
  survive DB → workflow → chat serialization exactly, including future columns.
- Base: a historical GIF is selected, downloaded on demand, sampled into three
  real visual inputs, then removed at request completion.
- Bad: interpreting a path as image understanding; publishing only seven known
  fields; downloading every incoming image; restoring an old DB over new messages.

## 6. Required Tests and Deployment Checks

Assert complete DB-to-model equality (future columns, deep unknown fields, more
than 20 attachments, false/null/zero, multiline text); current image-only @ and
raw/SDK dedup; stable same-group history/version paging; legacy migration with
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

Wrong: enable shell/view_image flags while retaining a handler that aborts every
command event, or claim read-only/workspace-write prevents host file reads.

Correct: align skill staging, instructions, tool events, actual pixels, and an
independently tested inherited isolation boundary. Prompt restrictions alone do
not enforce filesystem permissions.
