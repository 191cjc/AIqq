# Verification

## Implementation checks

- Added optional image-start callback to explicit GPT, NovelAI and conversational
  GPT generation/edit. Notice invocation follows checks/admission and precedes
  the provider; failures log safe exception types and cancellation propagates.
- QQ start text is persistent and one-shot. Commands allocate the following
  sequence for final replies; conversation shares its reporter counter with
  start/deferred notices and resolves final sequences after the workflow ends.
- A request-local lock preserves send order during deferred page creation.
  Cancelling blocked deferred setup cancels/awaits the workflow and prevents a
  waiting start callback from creating an orphan provider request.
- Implementer passed 11 new integration tests and 73 existing focused tests.
  Router tests additionally assert that all four manual/panel style scenarios
  send start at sequence 1 and final response at sequence 2.
- Source/test compileall and git diff --check passed. No configured linter or
  static type checker. Full independent check/deployment results follow below.

## Test boundaries

Tests use real workflows, admission manager, command/handler routing and reply
presenter with fake QQ/provider transport. They verify group/source association,
event order, final text/image sequences, request deduplication, duplicate callback
suppression, no notice for rejected/non-image requests, failure sanitization,
cancellation cleanup, exhausted temporary progress, deferred late image starts,
failed deferred setup, and a blocked-pending-page race.

No synthetic QQ messages, paid images or live provider test calls were made.

## Independent review

No runtime defects found. Reviewer updated two QQ client test assertions to
include the new optional callback while preserving routing, identity and dedup
coverage; the 14 QQ-client tests passed. Full unit suite: **354 tests passed** in
18.312 seconds. Source/test compileall and git diff --check passed.

## Deployment

Restarted the existing `aiqq.service` at 2026-09-20 16:28:06 Asia/Shanghai.
PID 2814991, active/running, NRestarts=0; `aiqq_ready` logged at 16:28:11.
Health returned HTTP 200/status ok, QQ gateway connected, database open and
NovelAI configured/ready. No service configuration or provider settings changed.
All pre-existing dirty changes were retained; no commit was made.

## Follow-up: visible original-message quote

User requested that the start notice visibly reply to the original message.
Observed live notices had `delivery_mode=reply`, `msg_seq=1` and the correct
local source association, but no `message_reference`. Passive authorization
alone did not request the visible quote.

Added a default-off `quote_source` text-sender option, enabled only by the start
presenter. The sender now supplies the original ID in `message_reference`, with
`ignore_get_message_error=True`. Existing fallback preserves that reference
while clearing passive fields and mentioning the original member. No automatic
quoting for unrelated text, Markdown or image messages.

Implementer passed 37 focused tests (12 sender, 11 start-notice integration,
14 presenter), compileall and diff checks. Tests capture real installed BotAPI
HTTP JSON on normal delivery, both known expiry errors, default nonquoted text,
and missing-source input; they also verify stored success/attempt metadata.
No live QQ test was sent; SDK serialization is verified, with actual QQ client
rendering to be observed on a normal subsequent request.

Independent follow-up review found no defects. Full unit suite: **356 tests
passed** in 17.703 seconds; compileall and git diff --check passed. No additional
runtime edits were needed.

Deployed the quote amendment at 2026-09-20 16:45:41 Asia/Shanghai after checking
that the former Codex child had exited and the latest image delivery completed.
New PID 2822111, active/running, NRestarts=0; `aiqq_ready` at 16:45:44. Health was
HTTP 200/status ok, QQ connected, database open and NovelAI configured/ready.
No synthetic QQ sends, paid generations or commits were performed.

## Concurrent investigation: recent image delivery failures

The user also requested investigation of recent image upload failures. Matched
QQ logs with persisted delivery attempts for source records 4519 and 4598:

- 2026-09-20 16:25:39 upload succeeded (attempt 1106); image message rejected
  at 16:25:41 (attempt 1108). NovelAI PNG, 1216x832, 1,771,929 bytes.
- 2026-09-20 16:42:17 upload succeeded (attempt 1166); image message rejected
  at 16:42:19 (attempt 1168). NovelAI PNG, 832x1216, 1,489,715 bytes.

Both send-message responses were HTTP 400, QQ code 40034006, message
`消息内容违规`. The image upload endpoint succeeded; both images were below
the 2 MiB compression threshold. The failed messages contained media only.
QQ did not specify the content element responsible or establish whether the
classification was correct. A later image message succeeded at 16:44:12.

The presenter currently maps all ordinary image delivery exceptions to
`图片处理失败，暂时无法发送到 QQ。`, hiding the platform rejection reason.
The persisted failure records retain only `ServerError`; the numeric code was
recovered from the SDK log. No delivery-policy changes, historical retries or
additional image generations were made during this read-only investigation.

## Follow-up: content rejection user copy

User requested clearer wording after the investigation. The QQ transport now
translates the observed exact image-only SDK rejection into the shared
`ImageContentRejected` exception after the existing failed-attempt record. The
presenter selects `QQ 返回“消息内容违规”，图片未能发送。请调整画面内容后重试。`
for this exception, retaining generic copy for other failures.

Implementer checks passed: 31 focused tests (15 sender, 16 presenter), compileall
and git diff --check. Coverage includes actual installed SDK parser through
sender/presenter, upload/send rejection, both expiry variants, foreground and
deferred completion, strict nonmatches/text exclusions, cancellation, no extra
uploads/sends and no false image success. Full independent review and deployment
results will be recorded below. No live QQ or paid image test calls were made.

Independent review found no defects and required no edits. Full unit suite:
**361 tests passed** in 17.774 seconds; compileall and git diff --check passed.
Project has no configured linter or static type checker.

Waited for the active request to complete its image delivery at 17:10:51, then
restarted aiqq.service at 2026-09-20 17:11:40 Asia/Shanghai. PID 2832900,
active/running, NRestarts=0; `aiqq_ready` at 17:11:44. Health returned HTTP 200,
status ok, QQ connected, database open and NovelAI configured/ready. No synthetic
QQ messages or paid generations; actual future QQ rejection display has not
been live-tested. All pre-existing dirty changes retained; no commit made.
