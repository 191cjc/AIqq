# GPT Image Service Contract

## Scope / Trigger

Read this when changing `src/aiqq/services/images/codex_responses.py`, shared helpers in `gpt.py`, the image-only SDK mode, image configuration/bootstrap, GPT image workflow errors, or image request tests. The packaged implementation is authoritative; root-level legacy modules are reference material only.

## Signatures

```python
async def generate(
    self, action: ImageAction, *, reference_image: ImageAsset | None = None
) -> ImageAsset: ...

ImageGenerationUnavailable(
    message: str, *, kind: str = "unknown", attempt_id: str = "",
    status_code: int | None = None, provider_request_id: str | None = None,
)

def image_failure_result(error: ImageGenerationUnavailable) -> ConversationResult: ...
```

The common exception lives in `aiqq.exceptions`. `GPTImageError` specializes it in the adapter; logic modules do not import OpenAI or HTTP exception types.

## Request, Response, and Configuration

- Production uses `CodexResponsesImageService`: genuine Codex SDK/CLI → authenticated temporary loopback bridge → configured HTTPS `/responses`. Both generate and edit declare only `image_generation` and force that tool. Edit forwards the SDK-encoded reference image in Responses input.
- The current provider restricts the image key to official Codex clients. Preserve genuine SDK headers; do not forge an official client identity on an ordinary HTTP call. Bridge requests accept only the per-attempt token, fixed path and configured driver model; the actual image key stays in the bridge.
- `OPENAI_IMAGE_DRIVER_MODEL` defaults to `gpt-6-astra`, independently of the chat model. `OPENAI_IMAGE_MODEL` defaults to `gpt-image-2` and selects the hosted tool model. Current provider tests show `gpt-5.6-sol` routes through Responses Lite, which rejects `image_generation`; this is a provider-specific observation, not a universal model claim.
- Tool defaults are `size="1024x1024"`, `quality="medium"`, `output_format="jpeg"`, compression 85. Editing retains `input_fidelity="high"`. Do not pass Images API fields such as `n` or `response_format` to the tool.
- Require `response.completed` and a completed `image_generation_call.result`; validate its Base64 and actual image, then normalize to one 1024×1024 RGB JPEG. HTTP 200, an image-start event or text alone is not success. Parse SSE with bounded storage and correct fragmented UTF-8/event handling.
- `OPENAI_IMAGE_API_KEY` and `OPENAI_IMAGE_BASE_URL` fall back independently to chat configuration. Verify the effective pair and model when diagnosing the provider; do not log the configuration object or credentials.
- The adapter retries only HTTP 502, at most twice after delays of 0.5 and 1 second, for at most three generation/edit POSTs per business attempt. Each retry reuses the same model, prompt and reference bytes. All other HTTP statuses, timeouts, connection errors and invalid image responses stop without retry.
- Image-only SDK mode disables request and stream retries and web tools. Image success comes from the captured Responses stream, independently of the SDK's final structured acknowledgement. Default chat/audit mode is unchanged and cannot invoke native image generation. Selective retries belong to the bridge and apply only to HTTP 502 before streaming. Accept one logical SDK request per attempt; reject additional SDK requests locally to avoid duplicate generation.
- A stream error, interrupted SSE, failed/incomplete terminal, absent/invalid image or timeout is never retried. `OPENAI_IMAGE_TIMEOUT_SECONDS` bounds the whole image attempt. Timeout, cancellation and service close must stop the SDK, upstream HTTP operation and loopback listener.
- The old Images API adapter remains available for existing helpers/tests, but production does not fall back to it automatically.
- A business attempt has one started and one finished event. Each scheduled retry has a gpt_image_retry event with the same attempt_id, retry_number, delay_seconds, HTTP status, safe provider request ID and bounded error diagnostics. A later timeout must not inherit the prior 502's response metadata. Cancellation during backoff or a request stops subsequent requests and propagates normally.
- Only validated, normalized images count as local generation successes. All failed/cancelled attempts release image admission. Local failure does not establish the provider's billing outcome.

## Validation and Error Matrix

| Failure | Kind | Observable behavior |
| --- | --- | --- |
| 401 / 403 | authentication | Fixed administrator-configuration message |
| 404 | not_found | Fixed request-failed message; provider details remain diagnostic data |
| 429 | rate_limited | Fixed limit message; no automatic retry |
| 400 / 422 | invalid_request | Fixed request-rejected message |
| Timeout / connection | timeout / connection | Safe failure text; no hidden repeat POST |
| 502 | upstream | Retry at most twice, then safe upstream-failure text if exhausted |
| Other 5xx | upstream | Safe upstream-failure text; no automatic retry |
| Invalid successful response | invalid_response | No image or success quota recorded |
| Cancellation | propagated CancelledError | Terminal diagnostic event and released admission |

Both GPT workflows use `image_failure_result`; known errors produce `image_service_<kind>`. Unknown kinds use the generic unavailable result. Preserve the existing distinct audit, reference-download, quota, and QQ-delivery failure paths.

## Good / Base / Bad Cases

- Good: a valid Base64 response becomes one normalized 1024×1024 JPEG.
- Base: a provider 404 records operation, attempt ID, status, provider request ID and a safe diagnostic, while QQ receives fixed text.
- Bad: a provider echoes credentials, prompts, image data or HTML; raw content must not reach logs or QQ. Diagnostic metadata is bounded and cleaned.

## Required Tests

- Use a fake SDK sender plus MockTransport to assert actual outgoing Responses tool schema, driver model, image credentials and reference input forwarding. Retain old AsyncOpenAI JSON/multipart tests for the legacy adapter.
- Cover authenticated path/model guards, duplicate SDK POST rejection, image-only SDK configuration and unchanged ordinary chat defaults.
- Cover fragmented UTF-8 and SSE, response size limits, terminal failures/incompletion, valid completed image results and cleanup on cancellation/close/timeout.
- Count actual transport requests for non-502 failures and timeouts to verify no retry.
- For both generate/edit, cover 502→200, 502→502→200, continuous 502 capped at three requests, a non-502 error/timeout after 502, and cancellation during retry backoff. Verify prompt and reference bytes survive retries, retry diagnostics remain safe, and later errors do not retain stale 502 metadata.
- Cover valid image normalization, malformed responses, missing request IDs, safe error metadata, redaction and cancellation.
- Use the real ImageQuotaManager in workflow tests to prove failures/cancellation allow the next member's request without charging the failed attempt.
- Offline checks do not prove live provider capability; record real generate/edit outcomes separately.

## Wrong vs Correct

Wrong: treat `/models` missing an image model as proof that the provider cannot generate images, or log only `HTTP 404` and guess the cause.

Correct: inspect the actual endpoint and error. The old Images API `model_not_found` account-group failure does not establish whether a Responses driver account can execute a hosted image tool. The current image key successfully generated and edited images through genuine Codex + `gpt-6-astra` + `image_generation` on 2026-09-15. Ordinary direct HTTP returned 403; the `gpt-5.6-sol` Codex route returned a Responses Lite tool rejection. Verify the exact chosen route with a decodable image before declaring recovery.

## QQ Upload Size Contract

### Scope and signatures

All `ConversationReplySender.send` image deliveries, including web search, GPT and NovelAI, must prepare the actual bytes before saving temporary media and calling QQ. The owner is `services/images/qq_upload.py`:

```python
def prepare_qq_image(image: ImageAsset) -> ImageAsset: ...
```

### Input/output contract

- The fixed upload limit is `2 * 1024 * 1024` bytes. At or below the limit, preserve the original bytes, MIME and dimensions; do not re-encode small images.
- Above the limit, validate the real image against existing input byte/pixel limits, apply EXIF orientation and encode JPEG. Composite transparency on white. Prefer the original resolution with quality from 90 down to 70; if still oversized, reduce dimensions proportionally with a bounded number of encode attempts.
- Every returned transformed asset must be a decodable JPEG at or below the limit, with actual MIME and dimensions. Compression must run outside the asyncio event loop via `asyncio.to_thread`.
- Pass the transformed asset to both media storage and QQ metadata. Extension, HTTP content type and recorded attachment MIME must all match the transformed bytes.
- Keep original generation/reference-image inputs, quota behavior and existing retry policy unchanged. This rule adds no QQ upload retries or failed-request replay.
- Log only byte counts, dimensions and fixed metadata; never image data, URLs, paths, messages or credentials.

### Validation and error matrix

| Condition | Behavior |
| --- | --- |
| Size <= 2 MiB | Original asset unchanged |
| Size > 2 MiB and valid | JPEG <= 2 MiB, preserving aspect ratio |
| Invalid oversized image / encode failure / cannot fit | Existing fixed QQ image-delivery failure text; do not save/upload oversized original |
| Cancellation during preparation | Propagate cancellation; no subsequent upload |

### Cases and required tests

- Good: the observed 4,999,518-byte PNG becomes roughly 385 KB at quality 90, preserving 1797×1773 dimensions.
- Base: an asset of exactly 2,097,152 bytes passes unchanged.
- Bad: changing only the MIME or uploading the original large bytes after compression failure.
- Test PNG/JPEG/WebP inputs, below/exact threshold preservation, actual oversized high-entropy image reduction, EXIF orientation, transparent background and failure before upload. Delivery tests must check actual stored bytes and MIME/extension consistency, not only mocked compressor calls.

### Wrong vs correct

Wrong: treat QQ `850027` (remote media upload timeout) as a GPT generation error, or compress only images produced by one provider.

Correct: enforce the size limit at the shared QQ delivery boundary. The observed web image was downloaded successfully; QQ fetched only 4,734,601 of 4,999,518 bytes before reporting timeout. Local compression reduces transfer size without repeating generation.

## Expired QQ Reply Delivery

### Scope and signatures

Applies to `QQMessageSender` final reply methods, `ConversationReplySender.send` / deferred completion, and the final foreground send in `GroupMessageHandler.handle(message: IncomingGroupMessage) -> bool`. Existing signatures and configuration remain unchanged: `fallback_member_openid: str = ""` opts a passive reply into recovery; the handler's boolean means the event was handled, not that QQ accepted delivery.

### Ownership and contract

- `services/qq/sender.py` owns QQ rejection classification and proactive fallback. `logic/ports.py` carries an optional original-member argument; the presenter explicitly enables fallback for final text/images, image-failure notices and deferred notices. Unmodified admin/progress callers retain their existing behavior.
- The installed botpy SDK drops numeric error codes and raises `ServerError` with only the message. Both observed expiry variants must be recognized using a centralized immutable collection: `40034031` → `msgid已经过期,不能回复`, and `40034005` → `回复消息msg_id已过期`. Match those exact messages on that SDK exception; do not infer expiry from arbitrary exception text, timeouts, or an elapsed local timer.
- Only a rejected passive reply with explicit original-member context may be retried once as a proactive message. Preserve or add `<@member_openid>` for text/Markdown only. Image messages (type7) send media without mention/caption text, both initially and on fallback; the SDK may serialize absent content as null. Keep the local `[图片]` placeholder and source metadata without adding a mention to image payload content. Clear both reply fields on fallback: the SDK defaults `msg_seq` to 1 and serializes its arguments, so pass `msg_id=None` and `msg_seq=None` explicitly. An API fake accepting arbitrary kwargs alone does not verify this SDK behavior.
- Reuse the uploaded `file_info` on an image fallback; do not repeat media upload or generation. A failed fallback, unrelated error or cancellation does not schedule another application retry.
- Persist a successful proactive delivery only once, with its original local `source_message_id` association and explicit proactive fallback metadata. Do not record the expired `msg_seq` as if it were used for the successful delivery. Failed sends do not create success records.
- Log fixed fallback attempt/outcome metadata. Never log raw errors, mention targets, prompt/content, media URLs or credentials in new diagnostics.

### Result lifetime and deferred completion

- Prepare/compress/save generated images before the first QQ message attempt, so a rejected text reply does not discard all image bytes. Storage is temporary and retains the configured media TTL; it is not a permanent archive. Preparation failure must never upload an oversized original, and cancellation stops subsequent sends.
- Deferred completion updates its original output page and delivers the result through the same final reply path with original group/member/source context, including images. The foreground threshold is independently configurable; the current deployment deliberately uses 600 seconds even though QQ passive replies expire after five minutes.
- A deferred workflow exception also becomes a safe unavailable result and uses final delivery, since its initial page notice may have failed. Cancellation remains propagated and does not send a new final message. Log page-update and final-delivery failures separately.
- Foreground final send exceptions are contained at the handler boundary with fixed `event=group_reply_delivery_failed error_type=...`, matching deferred delivery containment. Do not let a delivery rejection escape into gateway error handling, repeat the workflow, emit a false success record, or attempt another message after an ambiguous failure. `CancelledError` still propagates.
- Proactive permission and quotas remain platform constraints. Mock success proves the fallback logic, not actual QQ account acceptance. Do not automatically replay historical failed requests or start a fresh image generation as part of delivery recovery.

### Validation, cases and failure matrix

| Observed outcome | Action |
| --- | --- |
| Passive reply succeeds | Keep original reply context; no extra send |
| Either exact expiry `ServerError`, with original source/member and non-progress opt-in | One direct attempt without passive context; text/Markdown mentions the member, images contain media only |
| Unrelated rejection, modified/suffixed text, wrong exception class, timeout | No proactive retry |
| Direct attempt fails | No additional attempt or successful delivery record; terminal foreground handler logs and contains ordinary exceptions |
| Cancellation | Propagate; stop further operations |

Good: passive delivery succeeds once. Base: either definite expiry rejection becomes a direct @ answer followed by the original image, reusing uploaded media. Bad: treating only the first observed error wording as the entire platform contract drops valid generated images when QQ uses its other expiry response.

### Required regression coverage

Feed both recorded QQ HTTP400 payload variants through the installed SDK error parser and test actual SDK null reply fields. Cover text/Markdown mention/keyboard retention, pure-image payloads without caption/mention on initial and fallback sends, media reuse, single success record and original-request association, strict opt-in, unrelated error/timeout/cancellation, fallback rejection, image storage before text failure, and foreground/deferred final text plus images through the real presenter/sender. Assert persisted image content also has no mention. Also verify terminal foreground delivery errors are contained, logged without raw messages/IDs, and do not repeat the workflow; cancellation must propagate. Use mocked QQ transport; live account acceptance is recorded separately.

### Wrong vs correct

Wrong: `str(exc) == "msgid已经过期,不能回复"` as the only known expiry branch. Correct: exact membership in both observed SDK messages, inside the existing `except ServerError` and opt-in guards. Broad matching such as `"过期" in str(exc)` is still incorrect: an unrelated rejection or ambiguous outcome must not trigger another send.

## Reference Image Failure Messages

### Scope and error ownership

- The downloader (`services/images/web.py`) preserves structured error kind and optional HTTP status on `WebImageError`. Keep it compatible with existing catches and message-only construction. Do not classify using exception string matching, reference age or URL text.
- The reference loader (`services/images/reference.py`) translates download outcomes into the shared `ReferenceImageUnavailable` in `aiqq.exceptions`. It tries each existing candidate once. Later success wins; if all candidates fail, only a homogeneous recognized reason gets specific wording. Mixed or unknown outcomes become `download_failed`; an empty candidate list becomes `missing`.
- The workflow (`logic/conversation.py`) catches the shared error before its generic image-processing catch, maps it to fixed user copy and a stable `reference_image_<kind>` code. A legacy/alternate loader returning `None` becomes `missing`. Do not import service/HTTP exception types into logic.

### Error and user-copy matrix

| Reference outcome | Kind | Fixed user message |
| --- | --- | --- |
| All candidates return HTTP 404/410 | expired | 引用的图片已过期或已失效，请重新发送原图后再试。 |
| No candidate / loader returns None | missing | 未找到可用的原图，请重新发送图片后再试。 |
| All candidates time out | timeout | 读取原图超时，请稍后重试，或重新发送图片。 |
| All candidates return HTTP 401/403 | access_denied | 无法访问原图，请重新发送图片后再试。 |
| All candidates have non-image/invalid image content | invalid_image | 原图无法读取，请重新发送有效的图片后再试。 |
| All candidates exceed existing input byte limit | too_large | 原图过大，读取失败，请压缩后重新发送。 |
| Other/mixed/unknown failure | download_failed | 原图下载失败，请稍后重试，或重新发送图片。 |

### Invariants and regressions

- `ConversationResult.full_text` and `.summary` must both contain the complete fixed message; the normal QQ summary path must not hide the actual failure. Return no images and stop before quota reservation or generation. Unknown kinds map to the safe generic reference-download wording and code.
- HTTP 404/410 establishes an unavailable original resource; do not claim a proven expiration mechanism. A generation-provider HTTP 404 retains the existing `image_service_not_found` behavior and never becomes reference expiry.
- Classify both asyncio timeout and actual aiohttp timeout exceptions. A timeout raised inside the response context must not be swallowed by a broader OSError/ClientError handler and relabeled as a connection failure. Propagate cancellation through all layers without trying the next candidate.
- Retain URL/DNS/redirect/content/byte-size validation, existing retry rules, media TTL and QQ upload behavior. Logging includes safe stage/kind/status/request correlation only, never raw exception messages, response bodies, URLs, prompt/image contents or credentials.
- Test actual downloader -> reference loader -> workflow 404 conversion, 410, timeout/permission/invalid/oversized cases, no candidates, homogeneous/mixed candidates, later-candidate success, cancellation and no generation/quota use. Retain provider-404 regression and safe logging checks.

### Observed failure and prevention

The 17:44:50 request on 2026-09-15 selected record 609, whose temporary media returned 404. The former loader returned None for every failure; the workflow then raised RuntimeError and displayed 图片处理暂时不可用，请稍后再试。 Preserve the failure category across both boundaries so actionable information is not lost while raw errors remain private.

## NovelAI Configuration and Readiness

- Packaged runtime requires an explicit `NOVELAI_MCP_URL` plus `NOVELAI_MCP_TOKEN` or `NOVELAI_MCP_TOKEN_FILE`. The old root adapter's personal endpoint/default token path are not inherited. Migration must explicitly restore intended settings; never embed credentials or a personal endpoint default into source. Token files must satisfy the existing 0600 check.
- Startup distinguishes unconfigured and failed discovery with fixed safe diagnostics. A configured client is ready only after tools/list advertises `generate_image`; `steps` remains optional. Do not treat a missing generation tool as a usable service with no steps support.
- A shared exception contract carries `not_configured` / `not_ready` to the NovelAI workflow. Logic does not import adapter exceptions. Fixed messages are `NovelAI 生图服务未配置，请联系管理员。` and `NovelAI 生图服务连接未就绪，请稍后再试。`, identical in full text and summary. Unknown errors retain the existing generic NovelAI message. Never expose raw errors, URLs, prompts or credentials.
- Health exposes a read-only `novelai` snapshot containing configured/ready booleans; it performs no remote calls. Preserve existing overall QQ/database HTTP status semantics. Readiness means successful discovery, not proof that a later paid generation will succeed.
- Failed generation and cancellation release image admission without success accounting. No new automatic generation retry is introduced. Keep model, prompt safeguards, steps, quota and QQ compression/delivery contracts unchanged.
- Regression coverage includes real bootstrap wiring from an explicit URL and temporary 0600 credential file with mocked MCP discovery, missing configuration, failed/missing-tool discovery, fixed safe failure messages, and current health snapshot. A fake injected service alone cannot catch deployment configuration omissions.
- In the observed 2026-09-15 failure, configuration was empty, the client was None, and generation failed locally before reaching MCP. Old credentials still worked for initialize/tools/list. Record actual generation verification separately from connection/discovery checks.
