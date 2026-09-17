# Expired QQ reply fallback — 2026-09-15

## Current request and evidence

The user explicitly requests replying by mentioning the original user when `msg_id` has expired. Existing repair/restart authorization applies. Keep both foreground settings at 600 seconds and the image-call timeout at 240 seconds.

The 17:08:14 request finished image editing successfully at 17:14:14 (85.466 seconds in the image adapter). QQ rejected the following markdown with HTTP 400 / code 40034031 / `msgid已经过期,不能回复`. Because markdown was sent before image preparation/storage, the generated image was discarded. DeferredReply.finish also currently drops result.images.

Installed botpy `_handle_response` discards numeric code and raises `botpy.errors.ServerError(data['message'])`. Therefore an exact match of that known message on ServerError is necessary; arbitrary substring matching on generic exceptions is forbidden. No SDK monkeypatch is needed. The SDK API defaults msg_seq to 1 and serializes locals; explicitly pass msg_id=None and msg_seq=None on fallback to avoid reusing a passive sequence. Null fields represent no reply context at this SDK boundary; do not mistake omitted Python kwargs for omitted wire fields.

## Behavior owner and implementation boundary

- `services/qq/sender.py`: add an opt-in original-member argument to reply methods and private send boundary. Only explicitly enabled conversation sends with a source message and original member may retry an exact expired reply rejection once as a proactive message. Ensure fallback content mentions that member (markdown/text, or media content). Reuse existing uploaded file_info for image retry. Other errors, cancellations, proactive failures, and ambiguous network outcomes propagate without new application retries. Preserve original local source_message_id association while recording proactive delivery and excluding the expired msg_seq. Log fixed fallback attempt/success/failure metadata without raw errors, IDs, content or URLs.
- `logic/ports.py`: synchronize the optional argument for text, markdown, and image reply contracts. Defaults leave admin and progress behavior unchanged.
- `interfaces/qq/reply.py`: opt completed responses, image notices, and deferred notices into fallback using the original member. Prepare/compress/save images before the first QQ message so failed text/fallback does not discard generated image bytes. Preserve existing per-image failure notices and cancellation. Deferred completion updates its existing page and uses the same final send path with stored original group/member/source/sequence context, including images. Do not regenerate or re-upload on expired-message fallback.
- Targeted sender/presenter tests: real ServerError expiry, markdown mention/keyboard retention, media reuse, strict opt-in, no retry on other errors/timeout/cancellation, fallback failure counted once, accurate persistence, actual botpy payload defaults, images saved despite text delivery failure, and deferred final @ plus images.
- Parent owns README, spec, task artifacts and deployment. Implementation worker owns the above production files and tests; handlers/tests may be touched only if needed to preserve the same final-delivery behavior on deferred-start failure.

No generation requests, historical task replay, new database schema, timeout reset, generalized network retries, or changes to admin explicit-reply semantics. Temporary media remains subject to configured TTL; no promise of indefinite recovery. QQ proactive permission/quota still governs actual acceptance.

Review clarification: an uncaught deferred workflow exception must become the same safe unavailable ConversationResult and use final delivery. Merely updating the page is insufficient when the original page notice failed. Cancellation still updates failure state where possible and propagates without sending a new final message. Distinguish final QQ delivery failures from pending-page update failures in logs.

## Validation and rollout

Use mocked QQ calls; no manual group messages or paid image probes. Run relevant tests, independent Trellis review, the full packaged unit suite and git diff --check. Preserve the existing compression patch and unrelated dirty legacy files. Backup before this change: `/var/lib/aiqq/backups/qq-expired-reply-20260915T172855/`. Restart aiqq.service after confirming no active user workflow in logs, then check local/public health and QQ connection. Real proactive QQ acceptance remains unverified until an actual authorized group request exercises it.

## Deployment result

2026-09-15 17:42:58 CST deployed expired-msgid proactive @ fallback and deferred result/image delivery; PID 938845, active/running, NRestarts=0. 35 targeted / 190 full unit tests passed, independent review complete, compileall and git diff --check passed. Local/public health HTTP 200 with QQ connected and DB open. Foreground remains 600 seconds. No live proactive QQ send or paid generation; account acceptance awaits a future group request. Exact backup: /var/lib/aiqq/backups/qq-expired-reply-20260915T172855/.

Code changes remain uncommitted after the earlier user-requested commit. Keep this task in progress pending live QQ delivery evidence.

## First live proactive result — 2026-09-15 17:50 CST

The user submitted message record 620 at 17:44:50 requesting the previous image again. Context preparation completed at 17:45:06; the observed Codex turn completed at 17:50:05. Reference record 609 then failed to load at 17:50:15. Its temporary image file is absent and its HTTPS URL returns HTTP 404; the original media was sent at 16:40:33 and the configured default retention is 900 seconds. This request did not reach gpt_image_started.

At 17:50:16 QQ rejected the fixed failure markdown with expired-msgid code 40034031. The new fallback attempted proactive delivery and succeeded at 17:50:17. Bot record 621 has a QQ-assigned message ID, delivery_mode=proactive, fallback_reason=expired_msg_id, and markdown beginning with the original-member mention. The visible reply is 图片处理暂时不可用，请稍后再试。 This verifies live proactive markdown acceptance for this account; proactive image delivery has not yet been exercised. No manual message or generation replay was initiated during diagnosis.
