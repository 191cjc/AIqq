# R11 v4 implementation

Authorized: user explicitly said 开始改动 on 2026-09-16.
Baseline: /var/lib/aiqq/backups/chat-read-20260916T152109

Boundary: packaged src/aiqq only, related unit tests, new aiqq-chat-read skill, README and specs. Data gap lives in gateway/repository projection and chat serialization/runtime. Implement complete event/version metadata and full recent 50 plus request; same-group read tools and temporary vision input. No historical image cache, auto-generation, QQ test sends, unrelated legacy refactor or broad commit. Preserve existing dirty changes.

Ownership: storage implementer owns database/group_messages.py and gateway.py; runtime implementer owns services/ai/codex_sdk.py, responses.py and new runtime/isolation modules; read-tool implementer owns services/chat_read*, image download decoder, skill/scripts. Parent owns logic/models.py, ports.py, conversation.py, services/agents/chat.py, QQ client/handlers/sender/reply and bootstrap integration. Shared interfaces coordinated before edits.

## Validation to date

- 803-row production snapshot migration: all original columns/IDs unchanged;803 legacy snapshots,126 indexed attachments; second initialization unchanged. Evidence migration-probe.json. Live DB was not used for dry-run.
- One real vision probe: current model gpt-6-astra,76.36s, two prepared cards recognized exactly via ordinary ChatAgent→skill scripts→view_image. No image_action, no QQ send, both work/runtime turn directories cleared. Evidence live-vision-result.json.
- Parent integration subset50 passed after client/media/delivery changes. Final full suite and review pending.
- Rollback retains composite group identity writer/new event tables/newly received messages. Do not restore old database over newer messages or old ON CONFLICT(message_id) writer.

## Final deployment — 2026-09-16

- Independent review complete, no blocking findings. Whole packaged suite264 passed14.757s; final runtime/image regression75 passed7.752s. compileall, skill validation and git diff --check passed.
- Production idle verified: no service child processes and latest messages were non-mention receipts; prior image starts had terminal finishes. Fresh pre-deploy SQLite backup saved808 rows, env/unit snapshots and76 release source hashes.
- Service restarted16:09:51 CST, PID1297532; QQ connected16:09:54. Local/public health HTTP200; DB open, NovelAI configured/ready, no pending projections or write failures.
- Post-deploy original808 records were compared by all original columns and record IDs to pre-deploy snapshot: exact match.808 legacy versions,127 attachments. All76 release-file hashes and .env match. Evidence deployment-result.json.
- All-failed vision reads now preserve deterministic error reasons in both full answer and QQ summary; mixed errors do not become uniformly expired.
- No historical request replay, image generation or manual QQ test send. No source commit made; unrelated dirty workspace retained.
- Platform limits: QQ member recall/edit event shapes remain unverified (unsupported raw events archived); network restrictions are TCP-port-based, not loopback-address-only. User-originated QQ image-reading acceptance can now run against deployed service.

## Corrective deployment after chat outage — 2026-09-16 16:33 CST

The 16:09 deployment health checks missed a real chat initialization failure:
service `UMask=0077` caused CLI0.152.1 to call `fchmod(fd,0644)`, rejected by the
new inherited seccomp policy before any model request. The previous terminal
native/live probes did not inherit that service mask. See `outage-repair.md` for
the differential reproduction and exact-syscall evidence.

Child-only umask0022 after confinement fixes initialization while parent/service
0077, outer turn0700, and all syscall/filesystem/network rules remain enforced.
Safe failure classification was added without logging real CLI stderr. Full265
tests pass; all12 runtime tests pass under the complete production systemd
restrictions; independent review's25 SDK/runtime tests pass. Actual gpt-6-astra
vision under those same restrictions and both production feature flags reads
two prepared cards exactly in27.52s, with no generation/QQ send/production writes
or leftover turns.

Corrective restart16:33:04, PID1308272, QQ connected16:33:06. Local/public health
pass, NovelAI ready, no storage failures/pending projections.816 baseline message
rows match all columns after restart;817 current rows. Configuration/unit hashes
unchanged. Evidence: `outage-live-result.json`, `outage-review.md`, and
`outage-deployment-result.json`. No manual message replay or source commit.

## Standalone search corrective deployment — 2026-09-16 20:34 CST

User reported web search failures then explicitly requested repair/restart.
Native web.run calls `/v1/alpha/search`; R11 bridge omitted this endpoint, yielding
local404 and tool result `aborted` while Responses succeeded. Added exact
feature-gated authenticated forwarding, parent key substitution, preserved
native request bytes/headers, request/output/count/deadline bounds, no redirects,
and safe status/reason diagnostics. Search's64MiB input bound matches Responses
because native payloads contain full conversation; real CLI regression verifies
>256KiB history intact. No service isolation, QQ or storage behavior changes.

Full272 tests passed; final7 search tests passed under complete production
systemd restrictions. Independent review passed with additional boundary checks.
Actual gpt-6-astra web/open/image probe completed39.79s,3 search HTTP200 results,
public document+image URLs, zero leftover turns and no QQ/generation/DB writes.
Restart20:34:04, PID1393579, QQ connected20:34:06; local/public health normal,
NovelAI ready, no storage failures.1262 original rows unchanged,1264 now present.
Environment/service hashes unchanged. Details: web-search-repair.md,
web-search-review.md, web-search-live-result.json, web-search-deployment-result.json.
