# Reference image failure messages — plan and code only

## Authorization and scope

Latest user request: explain why an expired reference was reported as a generic image-processing error, improve the wording, and prepare both the plan and code. **Do not restart, reload, deploy, change service configuration, replay requests, send QQ messages, or invoke generation in this iteration.** This overrides earlier restart authorization. Service baseline: PID 938845, started 2026-09-15 17:42:58 CST.

## Evidence and smallest behavior gap

Message 620 failed at 17:50:15 while loading reference record 609. Its temporary HTTPS image returns 404. WebImageService raises a WebImageError with only descriptive text; GroupReferenceImageLoader catches it and returns None after all candidate URLs fail; ConversationWorkflow converts None into RuntimeError and its generic catch returns 图片处理暂时不可用，请稍后再试。 The correct user-facing result must identify a missing/expired original image and tell the user how to proceed.

The source of classification is the downloader's HTTP/timeout/validation outcome. The reference loader aggregates candidates; the workflow owns fixed user wording. Preserve provider-generation failure classification separately: a generation API 404 is not a reference-image expiry.

## Design

1. Extend WebImageError with optional structured kind and HTTP status while preserving existing construction/catching behavior. Classify 404/410 as not_found; 401/403 as access_denied; actual asyncio/aiohttp timeouts as timeout (including TimeoutError caught inside the current OSError handler); invalid content/decoding as invalid_image; input byte limits as too_large. Other HTTP/connection/URL-policy/unknown failures retain download_failed. Do not parse exception strings, expose URLs or weaken URL/DNS/redirect/size checks.
2. Add shared ReferenceImageUnavailable in aiqq.exceptions with a fixed kind contract. GroupReferenceImageLoader raises it when no candidate is usable. No URLs -> missing. Try each existing candidate once; later success wins. Only when all failed candidates have the same recognized kind report that kind; all not_found -> expired. Mixed outcomes/unknown failures -> download_failed, never infer expiry from one failed candidate or from a filename/age. Preserve cancellation. Keep None compatibility in the port: workflow interprets None from alternative loaders as missing.
3. Catch ReferenceImageUnavailable before the generic image-processing catch in ConversationWorkflow. Produce an unavailable ConversationResult with identical full_text/summary, stable reference_image_<kind> error code and no images. Full and summary must both explain the failure so the normal QQ presenter cannot hide it behind a summary. Map unknown kinds to download_failed. Use safe fixed stage/kind/status diagnostics; no raw provider text/URLs or secrets. Failure still precedes quota reservation and generation.
4. Existing generation failure messages, optional web-search-image behavior, image audits, quota, retries, compression, storage TTL and QQ delivery mechanics retain their behavior. This iteration fixes the reference-image branch rather than building a general error framework.

## Proposed user copy

| Kind | Text |
| --- | --- |
| expired | 引用的图片已过期或已失效，请重新发送原图后再试。 |
| missing | 未找到可用的原图，请重新发送图片后再试。 |
| timeout | 读取原图超时，请稍后重试，或重新发送图片。 |
| access_denied | 无法访问原图，请重新发送图片后再试。 |
| invalid_image | 原图无法读取，请重新发送有效的图片后再试。 |
| too_large | 原图过大，读取失败，请压缩后重新发送。 |
| download_failed | 原图下载失败，请稍后重试，或重新发送图片。 |

404/410 establishes that the resource is unavailable, not whether expiration or deletion caused it. The expired-or-invalid wording intentionally covers both. Unknown failures never receive the expired wording.

## File ownership and verification

Implementation worker: src/aiqq/exceptions.py, services/images/web.py and reference.py, logic/conversation.py; a ports.py docstring may describe the shared error if useful. Tests: unit/services/test_web_image.py, test_reference_image.py, unit/logic/test_conversation.py. A presenter regression may be added in test_conversation_reply.py if needed to prove actual displayed wording; preserve its existing compression/fallback changes. Parent: README, spec and task artifacts.

Regressions must exercise downloader statuses, asyncio and aiohttp timeouts, invalid/oversized image, mixed candidate errors vs later success, missing reference, cancellation, safe logging, and an actual downloader -> reference loader -> workflow 404 regression. Assert no generation/quota reservation for reference failures and final summary/full text agree. Preserve generation API 404 wording. Use mocks and no live network. Run relevant tests, independent review, then the complete packaged unit suite and git diff --check. Verify the running service PID/start time remains unchanged; do not deploy.

## Implementation and verification result

R8 reference-image error wording completed as plan/code only. Independent review found no blockers; 28 targeted and 203 full packaged unit tests passed (8.440 seconds), 8 changed code/test files passed AST parsing, git diff --check passed. No configured standalone lint/type-check. Service PID remains 938845, start time 2026-09-15 17:42:58 CST, active; no restart/reload/deployment/config changes, live requests or generation. New wording is not yet deployed.
