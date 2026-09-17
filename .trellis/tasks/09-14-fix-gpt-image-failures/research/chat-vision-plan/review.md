# R11 v4 independent review

Reviewed against the exact pre-R11 source baseline at
`/var/lib/aiqq/backups/chat-read-20260916T152109/source`, preserving unrelated
workspace changes. User implementation authorization is “开始改动”. No reviewer
service restart, paid provider request, QQ send, historical replay, or commit.

## Findings fixed

1. `database/group_messages.py`: current attachments previously required an exact
   JSON metadata match with one immutable version. Missing-field enrichment of a
   directly nested image object can create a valid current object absent from
   every individual version, so the image disappeared from the readable list.
   Keep this object explicitly as `metadata_source=current_projection`, with
   `version_id=null` and actual `source_versions`; preserve the real URL-source
   attachment association for measured read metadata when available. Complete
   metadata that arrived as separate MIME/URL fields remains readable without
   pretending that one original event contained the merged object.
2. `database/group_messages.py`: version/event branches ignored sender, keyword,
   time and record-cursor filters applied to the messages branch. Both now share
   the selected current-message filter. Raw unsupported events for a known
   message are found by group/message identity even without a successful
   processing projection; standalone unknown events remain queryable by group
   or message ID.
3. `database/message_events.py`: a legacy current row with no first raw-event
   association never acquired one on later capture. Preserve the first known
   receipt when present, otherwise bind the first newly saved original event.
   Legacy snapshots and record IDs remain unchanged.
4. Runtime implementer fixed the review's parent-file-write issue: a confined
   tool could replace the model-writable `read-images` directory with a symlink,
   then make the unconfined bridge write outside the turn directory. Staging now
   uses trusted directory descriptors and no-follow file operations; regression
   verification is listed below.
5. Parent fixed the review's image-only entry mismatch: nested image objects
   recognized by storage but lacking an `attachments` key now count as media in
   the incoming message, so a pure @ image request is not rejected as blank.
6. Parent fixed deterministic read-error propagation: when all attempted image
   reads finish with errors, retain the model's independent answer and append
   missing fixed errors; use the specific error or mixed reason labels for the
   QQ summary. Generation actions, successful/partial reads and pending or
   cancelled reads retain their existing behavior. Reviewer checked all 298
   combinations of up to three known error kinds: maximum summary length is
   32 characters, within the 50-character contract.
7. Final reviewer strengthened the genuine CLI regression to invoke view_image
   on both returned images and assert two distinct image payloads in the final
   upstream request. The same test verifies the full >10,000-character history
   page survives tool stdout limits and detached tool children are reaped.
   Added bounded history receipt/cursor and strict Responses tool-schema checks.

## Requirements reviewed

| Requirement | Evidence / boundary |
| --- | --- |
| V1–V3 | Current record separately serialized; image-only @ accepted with dedup; explicit source selection and same-group reads; GIF/WebP first/middle/last sampling; unresolved references and face IDs are not fabricated. |
| V4–V6 | URL/metadata persistence only; source bytes remain request-local; 3 sources/9 frames; distinct expiry, permission, timeout, format and budget outcomes; deterministic final failure copy preserves accurate expiry information. |
| V7 | Existing GPT/NovelAI, compression, expiry fallback and pure-image delivery tests retained; read sessions do not reserve generation quota. |
| V8 | Parent reports one bounded successful live two-card vision probe through ChatAgent, stored raw events, skill scripts and view_image; both pixel-only markers recognized; no QQ send/generation; no leftover turn directories. |
| V9/V12 | Full 50+current equality test includes >10,000 characters, preserved whitespace, future columns, deep fields, empty values and >20 attachments; corrupt JSON retains raw text and error. |
| V10/V11 | Group-bound tool operations, stable record/version/event cursors, full skill staging, genuine native execution and two distinct viewed images, inherited isolation; full history pages arrive intact in model context and stdout contains bounded cursor receipts. |
| V13–V15 | Durable original receipt commits before local projection; duplicate receipts and conflicts retained; recovery cannot invoke business callbacks; unknown member lifecycle events stay unsupported/unobserved; confirmed own recall is monotonic. |
| V16 | Source/delivery metadata derives from actual in-memory bytes; actual upload/send attempts are separate from confirmed sent-message rows. |
| V17 | Id-preserving legacy snapshots and schema migration; replay local pending projections only; rollback must retain new records and the group-scoped writer. |

## Known limits / no speculative fixes

- Current QQ member recall/edit event capability is not verified; unknown group
  packets are archived, but no guessed event name or unobserved lifecycle fact
  is projected as a confirmed recall/edit. This is an explicit platform boundary
  of the approved plan.
- Landlock restricts TCP destination ports, not destination IP addresses. The
  shell cannot read production DB/config/proc or other turns, and the provider
  key stays in the parent. Do not claim this is loopback-only network isolation.
  Stronger address isolation would require a separate executor design change.
- A pre-R11 writer using `ON CONFLICT(message_id)` is incompatible with the new
  group-scoped unique key. Source rollback must retain a compatible writer;
  restoring an old database snapshot would discard newly received messages.
- A live model vision success is not a QQ delivery test. User-originated QQ
  acceptance remains the live interaction boundary; no manual test send made.

## Verification

- Final full packaged unit suite, run by parent after implementation and review
  fixes: **264 passed in 14.757 seconds**.
- Final reviewer runtime/backend/GPT/reference/upload regressions:
  **75 passed in 7.752 seconds**. The initial command named a nonexistent
  `test_codex_sdk` module; rerun used the existing `test_codex_sdk_backend` module
  and all selected tests passed. No application fix was needed for that command.
- Genuine installed CLI against synthetic local SSE: full skill scripts execute,
  two view_image calls supply distinct image data, full large history page is
  injected without clipping, detached process is gone, turn directories empty.
- HTTP bridge receipt retains each message/version/event cursor and corresponding
  has_more flag; strict Responses schemas retain all supported query fields and
  expose no group override.
- `git diff --check`: passed.
- `compileall` on packaged code, unit tests and skill scripts: passed.
- Skill validation: passed (parent).
- No standalone lint or type-check tool/configuration is declared in
  `pyproject.toml`; do not represent syntax compilation as a static type check.
- Review complete with no remaining blocking code findings for V1–V17.
  Deployment/health and a user-originated QQ acceptance remain parent-owned;
  the explicit platform and rollback limitations above remain applicable.
