# R11 storage implementation result

2026-09-16. User authorized implementation with “开始改动”. This report covers the storage/gateway implementation subtask; deployment and the parent session's final integration checks are separate.

## Implemented schema and behavior

- `group_messages` retains all existing columns and `record_id`, and adds first/latest event references, version count, update source/time, observed recall state and conflict paths. Identity is now `(group_openid, message_id)`.
- `group_message_events` appends each actual receipt with exact original WebSocket text, SHA-256, event/group/message identifiers, connection-local receipt context, sequence, source and time. Unknown group events remain archived with `unsupported` processing status. Local successful deliveries/recalls use explicitly labeled local envelopes; these are not presented as original QQ gateway packets.
- `message_event_processing` holds pending/applied/duplicate/unsupported/failed projection state. Original receipt and pending state commit before the separate projection transaction. Startup recovers only database projections; this module has no conversation/generation/QQ business callback. Transaction cancellation rolls back while the earlier original receipt remains recoverable.
- `message_versions` preserves original event payloads and complete current-row snapshots. Missing object keys may enrich duplicate creates; conflicting scalars, explicit null/empty values and arrays retain the earlier current value, while later raw versions and conflict paths remain readable. Duplicate raw receipts are retained even when projection is skipped. No unverified QQ edit/recall event names are invented.
- `message_attachments` stores per-version JSON pointer, URL, full metadata, optional separate measured metadata and last-read outcome. No image bytes, download queue, image cache or image files are added.
- `message_delivery_attempts` stores supplied business parameters/results/source/status. Failed attempts do not create fake successful message rows. The sender integration is owned by the main session.

Complete history output uses `{record, payload, derived, attachments, latest_event, delivery_attempts}`. `record` comes from every SQLite row column, retaining the original `payload_json`; parsed JSON and parsing errors are separate. Unknown fields, null/false/zero, whitespace and arrays survive. Latest-50 history includes progress and recalled records; there is no event whitelist or content trimming in this repository. Same-group current records, keyset pages, filters, versions and original events share this serializer. Time filters compare actual SQLite Julian instants rather than strings with different offsets.

APIs include `get_full_message`, `get_full_record`, `get_current_for_reference`, `has_before`, `query_history`, `get_image_attachment`, `record_image_read`, `record_delivery_attempt`, `storage_status` and `recover_pending_events`. Image attachment indices count only image attachments. Current confirmed recall state also prevents reading pixels from pre-recall versions.

## Migration and rollback

Migration detects the known legacy global `message_id` uniqueness constraint, rebuilds the table from its actual DDL, copies every existing column, retains record IDs/custom indexes/triggers and preserves the AUTOINCREMENT high-water mark. It then creates additional tables/indexes. Existing records without versions receive one explicit `legacy_snapshot`; original gateway envelopes and missing earlier versions are not fabricated. Repeated initialization is idempotent.

The implementation subtask used temporary test databases only. It did not modify the production database, restart the service, replay requests, call a model, download historical images or send QQ messages. The parent session must perform the authorized consistent SQLite backup/dry-run and deployment checks.

Rollback must retain new schema/data and messages received since deployment. Disable new runtime reads/writes using a compatible code patch; do not restore an old database file over newer messages. Pre-R11 `_upsert ... ON CONFLICT(message_id)` is not directly compatible with the new composite identity, so an application rollback must retain a compatible group-scoped writer (or use an explicitly verified forward compatibility patch). Do not blindly restore the old repository writer after migration. Original code/files and a consistent SQLite backup are diagnostics/recovery material, not permission to discard later records.

## Verification

Passed 29 tests:

```text
.venv/bin/python -m unittest tests.unit.database.test_group_messages tests.unit.database.test_message_history tests.unit.services.test_qq_gateway tests.unit.services.test_group_message_viewer -q
```

Coverage includes exact original text/envelopes; unknown DB/payload fields, empty values and 25 attachments; full 50 messages exceeding 10000 characters; stable paging and cross-offset time filters; cross-group identical message IDs and rejected foreign versions; duplicate/missing/conflicting event snapshots; failed/cancelled projection recovery; legacy full-column/ID/index/sequence migration; malformed/non-object JSON; attachment provenance and group-scoped metadata updates; confirmed local recall before delayed message creation; failed delivery without fake success; and unknown events without invented member recall state.

`compileall` for database/gateway and scoped `git diff --check` passed. Final integrated/full-suite review remains the main session's responsibility.

## Review handoff

No verified platform member recall/edit event format was found in the installed SDK. Such received group events are durably archived as unsupported; absence of an event is `unobserved`, not proof that a message has not been recalled.

Reviewer follow-up: current attachment resolution matches exact path + metadata against immutable version sources. Arrays are deliberately not merged and ordinary received attachments resolve correctly. A directly nested image object enriched by missing-key merge can form a projected object matching no individual raw version; preserve an explicitly marked projected attachment/provenance entry instead of omitting its attachment index. Its complete fields already remain available in the full payload and historical versions. This edge case was surfaced to the main session during ownership handoff.
