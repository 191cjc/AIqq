# GPT Image Service Contract

## Web Image Replies

- Chat instructions and strict output use `selected_web_image_urls`, a required
  ordered array of direct public HTTPS image URLs. Select relevant images according
  to the user's requested count, capped at `logic.models.MAX_REPLY_IMAGES` (5).
  Requests above five receive at most five with an explanation. With no requested
  count, select only useful matching images; no selection is an empty array.
- The parser validates every URL using the existing public HTTPS restrictions,
  rejects malformed/oversized arrays, and removes duplicate URLs in order.
  `ChatAgentOutput` enforces the same selection cap; `ConversationResult` allows
  at most five images. GPT/NovelAI generation counts remain unchanged.
- `ConversationWorkflow` downloads selected web images concurrently through the
  existing validated downloader. Per-image timeouts still apply; returned images
  retain selection order even if downloads finish in another order. Failed images
  are omitted, successful images survive, and the full answer explains partial or
  total failure. Cancellation propagates. Log exception types and request IDs only.
- Web image provenance includes the successful `source_urls` in image order.
  QQ presentation keeps its existing loop, compression, storage, individual
  fallback and sequential `msg_seq` behavior after the final text reply. Web image
  downloads do not call generation, charge quotas or emit generation notices.
- Verify empty/one/five selections, six-image rejection, duplicate and unsafe URLs,
  reverse completion order, partial/total failure, cancellation, and all five
  selections passing through the actual agent/workflow/QQ presenter.

## Drawing Audit Exemptions

### Scope, identity and configuration

- `AIQQ_IMAGE_AUDIT_WHITELIST` parses comma-separated QQ member IDs using the
  existing strict `_member_ids` validator. Default is empty. This is independent
  of `AIQQ_NAI_V5_WHITELIST`, which affects quotas only; neither implies the other.
- Bootstrap constructs one immutable `logic/image_audit.py::ImageAuditPolicy`
  and injects it into explicit GPT, original NovelAI, Nai v5 and drawing-related
  prompt authoring workflows. Exact nonempty incoming `member_openid` membership
  determines exemption across groups, not a display name, prompt text, account
  type or parsed quota/conversation key. Bot senders can be legitimate members.
- The router passes actual sender identity to `GPTImageWorkflow.run`, whose
  added optional `member_openid=""` keeps old callers audited. Original NovelAI
  and Nai v5 already receive actual identity separately from their quota key.
- `Nai v5 <scene>` and `/Nai v5 <scene>` are short aliases for `nai_v5_image`,
  sharing the canonical `Nai v5 作图` workflow, including audit exemptions,
  styles, quotas, recall/receipt and scene-prompt access. Parse the explicit
  `作图` and `画风` subcommands first; preserve rejection of malformed subcommand
  prefixes lacking their required whitespace boundary. The alias itself also
  requires whitespace before its scene. A bare alias follows the existing
  empty-image input response.

### Workflow and layout contract

- An exempt explicit drawing request skips the entire local image-prompt audit
  call, including safety and scene effectiveness. Ordinary senders retain the
  exact existing rejection/unavailable/cancellation behavior.
- Legacy Chinese/quick drawing carries the real sender into
  `NovelAIPromptWorkflow.author(description, *, member_openid="")`; both its
  pre-translation audit and the final combined scene/style audit are skipped
  for the same policy. Translation/tag authoring still runs. Standalone prompt
  options call author without an exemption identity and remain audited.
- Without a model audit, NovelAI/Nai v5 select orientation locally from the
  scene only via `infer_image_orientation`. Recognize standalone English
  `lying`/`reclining` or explicit Chinese lying hints as landscape; `full body`
  (including underscores or whitespace) or 全身 as portrait; otherwise square.
  Lying takes priority. Do not mistake `flying` for `lying` or 卧室 for a pose,
  and do not infer layout from saved artist/style text. This recognizes explicit
  hints, not the audit model's full natural-language interpretation. Explicit
  GPT requests use automatic sizing through the direct Images API adapter below.
- Keep nonempty-input checks, original NovelAI character/language requirements,
  personal style composition, service parameters/instructions, reference/image
  validation, quota admission/accounting, notices, delivery and cleanup. This
  whitelist does not alter provider or QQ moderation and adds no retry/replay.
- General conversation auditing is not bypassed by this drawing-command policy.
  Configuration takes effect after the normal service restart; never hard-code
  user identities in production source or log prompts/secrets.

### Required regression coverage

- Default/strict configuration, independence from the quota whitelist, exact ID
  matching including absent/spoofed identity, and local orientation precedence.
- Real router/bootstrap/config/workflows for both configured accounts, across
  groups and both bot/human-style identities. Mock rejecting/unavailable audits
  and paid generation: exempt paths must make zero audit calls, while ordinary
  and quota-only senders still fail before generation.
- Exercise legacy direct, Chinese/quick (both stages), direct Chinese Nai v5
  and explicit GPT commands. Preserve style composition, scene-only prompt
  access, callbacks, empty/language checks, quotas and failure/cancellation
  cleanup. Check standalone prompt options retain auditing and policy survives
  application rebuild. No live QQ messages or paid generations are required.
- Test actual command text through QQ ingress/handler/parser, not only manually
  constructed `ParsedCommand` values. An unrecognized `/Nai v5 <tags>` previously
  fell into ordinary conversation audit despite a correct drawing whitelist.
  Cover native/text bot mentions, raw/SDK callbacks, bot-member identity, short
  aliases, canonical style precedence and ordinary senders retaining auditing.

## Image Generation Start Notice

The admission-time notice contract below applies to GPT. Explicit NovelAI/Nai v5
commands now use the immediate combined receipt described in
"Immediate NovelAI Receipt, Request Recall and Prompt Access" below, suppressing
their later start callback's message to avoid a duplicate receipt.

### Scope and signatures

Explicit GPT/NovelAI workflows and conversational GPT generation/edit accept an
optional no-argument async `on_image_start` callback. This is an application
event, separate from model-generated/capped temporary progress messages. The QQ
interface owns the fixed plain-text message `已收到提示词，开始作画。`.

```python
ImageStartCallback = Callable[[], Awaitable[None]]
async def notify_image_start(callback: ImageStartCallback | None) -> None: ...

class QQProgressReporter:
    def reserve_msg_seq(self) -> int: ...

class ConversationReplySender:
    async def send_image_start(
        self, *, group_id: str, source_message_id: str,
        member_openid: str, msg_seq: int,
    ) -> None: ...

    # Optional getter resolves at completion; old callers remain valid.
    async def start_deferred(
        self, *, group_id: str, source_message_id: str,
        member_openid: str, first_msg_seq: int,
        next_msg_seq: Callable[[], int] | None = None,
    ) -> DeferredReply: ...

# GroupMessageSender/QQMessageSender text API adds an opt-in quote flag:
async def send_text_reply(
    *, group_id: str, source_message_id: str, content: str, msg_seq: int,
    progress: bool = False, origin: str = "conversation",
    fallback_member_openid: str = "", quote_source: bool = False,
) -> BotSentGroupMessage: ...
```

### Contracts

- Invoke after input/audit/reference checks and successful image admission,
  immediately before the provider call, inside the admission cleanup boundary.
  Prompt-options generation, style settings, plain chat and web image search do
  not emit this event. Do not add image calls, retries or quota charges.
- The originating group receives one persistent notice per admitted request.
  It is not recalled as temporary progress, and hitting the temporary-progress
  cap must not suppress it. Panel image options follow the same command path.
- Start notices explicitly opt into `quote_source=True`. With a nonempty source,
  the text sender includes `message_reference={"message_id": source_message_id,
  "ignore_get_message_error": True}` along with passive `msg_id`/`msg_seq`.
  `msg_id` alone authorizes a passive reply; it does not request the visible
  original-message quote. Preserve the reference in successful stored payloads
  and existing expired-msgid fallback; if the original cannot be retrieved, the
  notice remains deliverable. Other text calls default to no explicit quote.
- Notice delivery failure logs only fixed event and exception type and does not
  prevent generation. Cancellation propagates and releases image admission.
- Reserve reply sequence numbers before awaiting sends, including ambiguous
  failed sends. Commands use the next sequence for final output. Conversation
  progress, image-start and deferred notices share the same sequence owner.
- Serialize image-start with deferred setup/fallback sends to preserve actual
  send ordering across awaited storage/transport calls. Deferred completion and
  no-link completion read the current sequence after the workflow completes;
  never capture a stale final sequence before a late image-start notice.
- Use existing original-member context for expired passive-message fallback.
  Final image messages retain their image-only content contract.

### Validation and error matrix

| Condition | Behavior |
| --- | --- |
| Admitted explicit GPT/NovelAI or chat GPT generate/edit | Notice awaited before provider call; final output uses later sequences |
| Empty input, failed audit/reference, denied quota | No start notice and no generation |
| Notice fails | Consume reserved sequence; continue once; safe diagnostic only |
| Notice cancelled | Propagate cancellation, release admission, no subsequent generation |
| Ordinary progress exhausted / conversation deferred | Start notice still sent to group, final sequence resolved dynamically |
| Deferred setup fails | Consume reservation, allocate a fresh fallback-notice sequence, preserve final delivery |

### Good / base / bad cases

- Good: explicit request sends notice at sequence 1, final text at 2 and image
  at 3; all belong to the original user message and group.
- Base: failed notice still allows generation, with final text starting at 2.
- Bad: route the start notice through temporary progress that is already capped,
  or reuse a deferred final sequence after a later notice consumed it.

### Required tests

- Actual workflow/provider boundary ordering for both explicit providers and
  conversational generation/edit; failed validation/admission and non-image paths.
- Notice failure and cancellation, admission release, deduplication and one-shot
  notice behavior; final text/image sequence numbers through actual presenter.
- Capture the installed `BotAPI` HTTP payload to verify the original message
  reference on start notices, its persistence and retention after both observed
  expiry errors. Unrelated text/Markdown/image paths retain their old payloads.
- Exhausted progress, delayed generation after deferral, failed deferred setup,
  and a late image-start during a blocked pending-page creation. Mock QQ/provider
  transport; do not send synthetic group messages or generate paid test images.

### Wrong vs correct

Wrong: announce drawing before audit/quota rejection, or let a failed status
message abort an otherwise admitted image request.

Correct: publish one application-owned event at the admitted provider boundary,
with a persistent QQ notice and shared reply sequencing.

Wrong: infer visible quoting from a successful passive reply or from a stored
`source_message_id` alone. Correct: opt in to the explicit `message_reference`
field and verify it survives installed SDK serialization.

## Immediate NovelAI Receipt, Request Recall and Prompt Access

- Nonempty `novelai_image`, `novelai_quick_image` and `nai_v5_image` commands
  reserve one sequence and call `ConversationReplySender.acknowledge_image_request`
  before style loading, auditing, translation or quota admission. It attempts
  `QQMessageSender.recall_member_message` once for the incoming group/message ID,
  then sends ONE receipt containing both received and recall status. Legacy
  Chinese translation wording belongs to that same receipt. Suppress the later
  start callback's message, even after an ambiguous failed receipt send.
- Do not quote the recalled source or include its prompt in the receipt. Empty
  commands, prompt authoring, style settings, GPT and ordinary chat do not recall
  user messages. Gateway deduplication also prevents duplicate recall/receipt.
- Only API success reports `原消息已撤回`; permission/expiry/other failures use
  fixed explanatory wording and continue generation. Receipt failure consumes
  its sequence and does not trigger a second receipt. Cancellation propagates
  before generation/admission. The existing bot-progress recall retains its
  local ownership check and two-minute window.
- QQ official group recall requires the bot to be a group administrator and the
  sender to be an ordinary member, within two minutes of sending. Use `d.id` from
  the incoming event and `DELETE /v2/groups/{group_openid}/messages/{message_id}`.
  Errors 40062003/40064004 mean permission/expiry. botpy 1.2.1 discards response
  codes, so also recognize its exact retained messages `无操作权限` and
  `已超出消息撤回时限`; HTTP 403 is permission denial. Never expose raw errors.
  Reference: https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages_message_id.delete.html
- Successful recall records a trusted member confirmation in history. A local
  persistence failure cannot change an already successful remote recall to a
  failed claim. See the member recall contract in `chat-read.md`.
- Successful images from all three commands enable the existing prompt link.
  `ConversationResult.generation_prompt` contains the scene captured BEFORE
  saved style composition; auditing/generation still use the composed prompt.
  Legacy Chinese input exposes the authored English scene; V5 preserves its
  direct input. Do not subtract style tags from the completed string, because
  users may intentionally include the same words. Save the raw scene to the
  existing reply store; append `?view=prompt` to request literal copy rendering.
  Provider-added safety/negative prefixes never enter this scene field.
- Verify immediate recall/receipt order, success and permission/expiry failures,
  one notice including translation, sequence consumption, cancellation, dedup,
  every command's final link, independent saved styles and exact copied text.

## Direct GPT Image API (Production)

- Bootstrap shares one `GPTImageService` between explicit `/GPT生图` and
  ordinary-chat generation/edit. It directly calls `/images/generations` for
  generation and multipart `/images/edits` for reference edits. It does not use
  a Codex image thread, a Responses driver, or the `image_generation` tool.
- `OPENAI_IMAGE_MODEL` defaults to `gpt-image-2.5-flare`; image key and base URL
  retain their independent fallbacks to chat configuration. The old
  `OPENAI_IMAGE_DRIVER_MODEL` remains parsed for compatibility and is unused by
  production image generation.
- Both operations explicitly request `response_format="b64_json"`. The configured
  provider returned HTTP 200 without a usable Base64 result when this field was
  absent. A successful status alone never establishes that an image was received.
  Use `n=1`, quality high, `size="auto"` and the existing safe instruction; edits
  retain high input fidelity and validated, unchanged reference bytes. Do not
  request an output format or compression level.
- Decode Base64 and validate the actual image, retaining the exact original bytes,
  MIME type, canvas, transparency and metadata. Do not reencode, transpose EXIF,
  flatten alpha, crop, resize or pad. Keep image admission, quotas, audit, notices,
  safe error classification and selective 502 retries.
- Both generation and editing use automatic sizing, including older actions with
  valid explicit width/height fields. These fields remain schema-compatible but
  never override `size="auto"`. Native-size selection and JPEG normalization are
  retained exclusively for the unwired legacy Responses adapter.
- Chat instructions and the GPT image action skill set both dimensions to null.
  Include user dimension/aspect/composition requests in the prompt without
  promising a matching canvas. Do not infer a request to crop or pad a reference.
- Accept valid returned images even when their dimensions or aspect ratio differ
  from the reference or user preference. There is no `size_mismatch` rejection or
  special `unsupported_size` classification. Log reference/request/output
  dimensions without prompts, credentials or image data.
- The QQ presenter bypasses `prepare_qq_image` for results with GPT provenance.
  Store and upload original bytes even above QQ's usual 2 MiB preparation limit;
  if QQ rejects delivery, the existing viewing link serves that same original.
  Preserve upload compression for other image providers.
- Live long-image probe on 2026-10-23 used a synthetic 1080x5037 reference.
  The configured provider rejected exact `size=1080x5037` with HTTP 400 and
  `The requested image size is invalid.`. A separate `size=auto` probe returned
  895x1757 and changed the aspect ratio. The user's selected policy is automatic
  sizing and displaying the valid returned result unchanged, with no dimension
  matching rejection or local transformation.
- Verify outgoing generation JSON and edit multipart fields through a real SDK
  with MockTransport, and verify bootstrap injects the direct adapter into both
  image workflows. Never add a second provider call for a successful generation.
- Live validation on 2026-10-21: the configured image key generated a validated
  1024x1024 PNG through `/images/generations` with `gpt-image-2.5-flare` and
  explicit Base64 output. The production adapter also completed a reference edit
  through `/images/edits`, returning a validated 1024x1024 RGB JPEG with the
  requested added leaf. The same model on the previous `/responses` route
  returned HTTP 404. Do not log credentials, signed image URLs or raw responses.

## Legacy Responses Adapter (Reference)

### Scope / Trigger

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

- The retained `CodexResponsesImageService` uses genuine Codex SDK/CLI → authenticated temporary loopback bridge → configured HTTPS `/responses`. It is no longer wired by production bootstrap. Both generate and edit declare only `image_generation` and force that tool. Edit forwards the SDK-encoded reference image in Responses input.
- The current provider restricts the image key to official Codex clients. Preserve genuine SDK headers; do not forge an official client identity on an ordinary HTTP call. Bridge requests accept only the per-attempt token, fixed path and configured driver model; the actual image key stays in the bridge.
- This legacy adapter's constructor retains `gpt-6-astra` as the driver and `gpt-image-2` as its tool model. Production configuration now defaults to the direct model documented above. Historical provider tests showed `gpt-5.6-sol` routing through Responses Lite, which rejected `image_generation`; this was a provider-specific observation, not a universal model claim.
- Ordinary-chat `image_action` owns optional custom `width` and `height`. Both fields are required-nullable in structured output: `null/null` means the deterministic 1024x1024 request; integers must be paired, each be within 256..2048, and total at most 4,194,304 pixels. Missing, partial, non-integer or out-of-range values are invalid agent output. Historical `ImageAction` construction without dimensions remains compatible with the square default.
- The provider still accepts only `1024x1024`, `1024x1536`, or `1536x1024`. Custom dimensions select the native request size with the nearest symmetric aspect-ratio distance; they do not prescribe a local output canvas. Do not send arbitrary dimensions or provider `auto`. The explicit `/GPT生图` workflow has no skill dimension fields and requests 1024x1024. Image audit orientation remains for NovelAI and does not overwrite GPT action dimensions.
- Tool defaults remain `quality="high"`, `output_format="jpeg"`, compression 85. Editing retains `input_fidelity="high"`. Do not pass Images API fields such as `n` or `response_format` to the tool.
- Require `response.completed` and a completed `image_generation_call.result`; validate its Base64 and actual image, then convert to an RGB JPEG while preserving the returned dimensions and composition after EXIF orientation. Do not resize, crop, or add canvas padding to match requested dimensions. Transparent pixels retain the existing white alpha background for JPEG conversion. `ImageAsset` metadata must match the actual encoded image. QQ's separate upload size limit may still require proportional compression. HTTP 200, an image-start event or text alone is not success. Parse SSE with bounded storage and correct fragmented UTF-8/event handling.
- `OPENAI_IMAGE_API_KEY` and `OPENAI_IMAGE_BASE_URL` fall back independently to chat configuration. Verify the effective pair and model when diagnosing the provider; do not log the configuration object or credentials.
- The adapter retries only HTTP 502, at most twice after delays of 0.5 and 1 second, for at most three generation/edit POSTs per business attempt. Each retry reuses the same model, prompt and reference bytes. All other HTTP statuses, timeouts, connection errors and invalid image responses stop without retry.
- Image-only SDK mode disables request and stream retries and web tools. Image success comes from the captured Responses stream, independently of the SDK's final structured acknowledgement. Default chat/audit mode is unchanged and cannot invoke native image generation. Selective retries belong to the bridge and apply only to HTTP 502 before streaming. Accept one logical SDK request per attempt; reject additional SDK requests locally to avoid duplicate generation.
- A stream error, interrupted SSE, failed/incomplete terminal, absent/invalid image or timeout is never retried. `OPENAI_IMAGE_TIMEOUT_SECONDS` bounds the whole image attempt. Timeout, cancellation and service close must stop the SDK, upstream HTTP operation and loopback listener.
- Production uses the direct Images API adapter above. Neither adapter automatically falls back to the other.
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

- Good: a 1920x1080 chat action becomes a `1536x1024` request; a returned 1536x1024 image keeps that size and its full composition. `null/null` and direct-command generation remain deterministic 1024x1024 requests, with output dimensions taken from the actual returned image.
- Base: a provider 404 records operation, attempt ID, status, provider request ID and a safe diagnostic, while QQ receives fixed text.
- Bad: a provider echoes credentials, prompts, image data or HTML; raw content must not reach logs or QQ. Diagnostic metadata is bounded and cleaned.

## Required Tests

- Use a fake SDK sender plus MockTransport to assert actual outgoing Responses tool schema, driver model, image credentials and reference input forwarding. Retain old AsyncOpenAI JSON/multipart tests for the legacy adapter.
- Cover required-nullable custom dimensions, boundary and invalid combinations in the skill, chat Schema and domain object. Across ordinary-chat generation/edit and both adapters, assert that native request selection is nearest by aspect ratio while actual JPEG dimensions and `ImageAsset` metadata match the returned image. Verify default and custom requests preserve edge content without cropping or canvas padding. Keep explicit generation, historical actions and NovelAI regression coverage.
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

## Audit Rejection Feedback

### Scope / Trigger

Apply when presenting rejected ordinary-chat input, explicit GPT/NovelAI image
prompts, NovelAI prompt authoring, or an image action requested by ordinary chat.

### Signatures

`PromptAuditResult.reason` and `ImagePromptAuditResult.reason` are validated,
nonempty audit outputs. `audit_rejection_summary(reason)` fits the reason into
the 50-character `ConversationResult.summary`. `image_audit_rejection_result`
reuses the same copy for explicit and ordinary-chat image rejection.

### Contracts

- For rejected input, keep the category-specific refusal and append `原因：<reason>`
  to `full_text`; `summary` carries the brief reason instead of fixed refusal copy.
- For rejected image prompts, retain the safe `可改为：` suggestion in `full_text`,
  and show the audit reason in `summary`. An English-only rejection retains its
  fixed English requirement as the reason. Trim long summaries with an ellipsis.
- Ordinary-chat image-action audit rejection is `rejected`, not an unavailable
  provider failure. Do not reserve quota or invoke generation after rejection.
- Ask auditors for concise, specific, non-graphic reasons without quoting
  user-supplied content or proposing policy bypasses. Do not log audit reasons,
  prompts, or raw exceptions. Audit outages retain fixed unavailable messages.

### Validation and Error Matrix

| Case | User-visible result |
| --- | --- |
| Input or image audit rejects | Main reason in QQ summary; full reason and applicable suggestion in full output |
| Audit reason exceeds summary limit | At most 50 characters with ellipsis; full reason preserved |
| Audit provider unavailable | Existing fixed unavailable copy; no fabricated rejection |

### Good / Base / Bad Cases

Good: `effective=false` with reason "缺少可绘制的画面内容" exposes that reason
without generating. Base: accepted audit leaves the normal reply path unchanged.
Bad: only show "图片提示词未通过审核" while storing the actual reason behind a link.

### Required Tests

Cover rejected input, GPT/NovelAI and quick-image audits, embedded chat image
actions, long reason clipping, preserved safe suggestions, and no quota or image
call on rejection. Existing successful generation and unavailable paths still pass.

### Wrong vs Correct

Wrong: use a fixed `summary` after attaching the reason only to `full_text`,
or treat an image audit rejection as a provider outage. Correct: put the bounded
reason in the QQ-visible summary and preserve the full detail separately.

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
| Valid original but encode/resize/cannot-fit failure | Save the validated original for a temporary public viewing button only; never upload unprepared oversized bytes |
| Invalid oversized image / temporary storage failure | Fixed QQ image-delivery failure text without a broken viewing button; no upload |
| Cancellation during preparation | Propagate cancellation; no subsequent upload |

### Cases and required tests

- Good: the observed 4,999,518-byte PNG becomes roughly 385 KB at quality 90, preserving 1797×1773 dimensions.
- Base: an asset of exactly 2,097,152 bytes passes unchanged.
- Bad: changing only the MIME or uploading the original large bytes after compression failure.
- Test PNG/JPEG/WebP inputs, below/exact threshold preservation, actual oversized high-entropy image reduction, EXIF orientation, transparent background and failure before upload. Delivery tests must check actual stored bytes and MIME/extension consistency, not only mocked compressor calls.

### Wrong vs correct

Wrong: treat QQ `850027` (remote media upload timeout) as a GPT generation error, or compress only images produced by one provider.

Correct: enforce the size limit at the shared QQ delivery boundary. The observed web image was downloaded successfully; QQ fetched only 4,734,601 of 4,999,518 bytes before reporting timeout. Local compression reduces transfer size without repeating generation.

## Temporary Image Viewing After Delivery Failure

### Ownership and contract

- The shared final presenter in `interfaces/qq/reply.py` retains the media URL
  independently of readiness to upload. Preparation success stores the prepared
  image once and reuses its URL when QQ upload/send fails. Preparation failure
  revalidates the source bytes off the event loop and stores the valid original
  for viewing only, deriving MIME and dimensions from those bytes.
- A stored image's failure notice is Markdown with a `查看图片` URL button,
  using the existing `_link_row` action type 0 and public `/media/{token.ext}`
  route, just like `查看完整输出`. Keep the fixed generic or QQ content-rejection
  explanation, mention the original member, explain temporary expiry, and
  retain available full-output and generation-prompt links in the same keyboard.
- The failure notice uses the image outcome's existing sequence and original
  request context; preserve expired-passive recovery and successful-history
  recording. No additional image upload, generation, quota charge or replay.
- Invalid source bytes, generation without a usable image, or failed media
  storage cannot produce a valid image link: keep the plain failure notice
  without a fabricated URL. Do not use input/reference images as substitutes
  for an unavailable generated result.
- Reuse the configured media TTL (default 900 seconds), file permissions and
  HTTP headers. Expired links return 404. Do not introduce permanent storage,
  a new page or third-party host. Log fixed event/type metadata only.
- Cancellation during preparation, fallback validation/storage, upload or
  notice delivery propagates and stops subsequent operations. A failed button
  notice is not retried. Preserve image storage before the first text send.

### Required regression coverage

- Feed real SDK upload/send rejection and expired-passive fallback payloads
  through foreground/deferred completion, checking the URL button, fixed copy,
  original context, sequence, recorded successful text and single upload.
- Exercise actual encoding/resize exceptions with valid oversized source
  bytes; confirm that only the original is stored and QQ is not called. Verify
  the original's MIME/extension and byte-for-byte HTTP response.
- Check the QQ upload failure button opens the actual prepared JPEG within
  the upload limit. Cover expiry 404, invalid images, storage failure, existing
  prompt/full-output buttons, no broken links and cancellation/no retry.

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

## QQ Image Content Rejection Copy

### Scope and signature

Applies to `services/qq/sender.py` image delivery and the shared foreground /
deferred presenter in `interfaces/qq/reply.py`. The shared application exception
is `aiqq.exceptions.ImageContentRejected(AiQQError)`; it carries a fixed safe
message, not provider response text. Public sender signatures remain unchanged.

### Contract

- The installed botpy SDK drops numeric codes. QQ response 40034006 is observed
  as exact `ServerError("消息内容违规")`. In `_deliver`, translate that exact
  exception only for `operation="upload_image"` or `operation="send_message"`
  with `msg_type=7`, after the existing failed-attempt record is written.
- The presenter catches `ImageContentRejected` for image delivery and uses
  `QQ 返回“消息内容违规”，图片未能发送。请调整画面内容后重试。`.
  Attribute the classification to QQ; do not assert which depicted element was
  responsible, that generation failed, or that every image was AI-generated.
- Unknown send failures and local preparation failures keep the generic text.
  A successfully stored image adds the temporary viewing notice/button defined
  above; the QQ-specific explanation remains intact.
  No additional send/upload/generation retry, billing or quota change. Existing
  expiry recovery, image-only payloads, original source/member association and
  cancellation propagation remain in force. Do not expose raw exception text.

### Validation and error matrix

| Condition | Behavior |
| --- | --- |
| Exact SDK content rejection on image upload/send | Shared rejection exception; fixed QQ-specific notice plus temporary image viewing button when stored |
| Content rejection on the existing proactive image fallback | Same notice; no further fallback/upload |
| Text/Markdown rejection, other exception class or changed error message | Preserve existing exception behavior |
| Unknown image error or preparation failure | Existing generic image failure notice plus viewing button when a valid image is stored |
| Cancellation | Propagate; no notice or additional call |

### Cases

Good: generated image uploads successfully, QQ rejects sending it with 40034006,
and the user sees QQ's reason. Base: unknown transport failure still gets a safe
generic notice. Bad: classify all ServerError exceptions as content rejection.

### Required tests

Feed the observed response through the installed SDK parser, sender and
presenter. Cover upload/send boundaries, foreground/deferred, rejection after
expiry fallback, strict type/message/image-only matching, unknown failure and
cancellation. Check actual request counts, final notice and source association,
no false successful image record, and unchanged quoting/expiry regressions.
Use mocked transport only; do not replay real failed requests.

### Wrong vs correct

Wrong: inspect arbitrary error text in the presenter or label every delivery
failure as a generation/upload error. Correct: classify the known rejection at
the QQ boundary and preserve it as a shared exception to the fixed user copy.

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

## Provider-specific NovelAI Negative Prompts

- The packaged adapter `services/images/novelai.py` owns two independent fixed
  strings: `NovelAIService.generate()` sends `SAFE_NEGATIVE_PROMPT`, while
  `NaiV5Service.generate()` sends `NAI_V5_NEGATIVE_PROMPT` in the MCP
  `generate_image` argument `negative_prompt`.
- The legacy preset contains only the existing adult-content exclusions. V5
  retains these exclusions except `loli`, `cleavage` and `shota`, and adds the
  user-requested quality, anatomy, artifact, text/watermark and style exclusions.
  `child sexualization` remains present in both presets.
- Deduplicate the fixed v5 preset by tag when editing it. If plain and weighted
  versions coexist in the requested list, retain the weighted version only.
  Forward brackets and numeric weights verbatim, including `[abstract]`,
  `{{{{unskillful}}}}` and `3::anatomical nonsense::`. No runtime tag rewriting
  or negative-prompt command is introduced.
- These are exclusions, so tags such as `lineart`, `retro artstyle`,
  `depth of field` and `old` also suppress those requested styles or features.
- Verify actual outgoing arguments for both adapters using fake MCP clients:
  v5 omits the three removed tags and retains the requested weights; legacy
  retains its preset and receives no new quality or anatomy exclusions. Mocked
  argument verification does not establish rendered quality or remote weighting
  behavior. Preserve the existing input audit and positive safety prefix.

## Nai v5 Explicit Random Seeds

- `NaiV5Service.generate()` samples a fresh nonzero unsigned 32-bit seed per
  admitted invocation using `secrets.randbelow(2**32 - 1) + 1`, and sends that
  integer in the MCP `generate_image` argument `seed`. Do not cache the sampled
  seed on the client or service, or use prompt-derived seeds. Separate random
  samples may theoretically collide; no uniqueness guarantee is introduced.
- Validate configuration, readiness and orientation before sampling. Each
  actual provider invocation records `event=nai_v5_generation_requested seed=N`
  inside the existing semaphore boundary. Log only the numeric seed, never
  prompts, image data, sender identifiers, authorization or response bodies.
- In the 2026-10-09 investigation, all five retained V5 PNGs reported seed 0.
  AiQQ's requests hard-coded 0, and the public upstream tool/request/payload
  code forwarded that value unchanged even though its docs said `0 = random`.
  The live tool schema advertised integer/default 0 without promising a
  conversion. Prefer observed metadata and executable behavior over a default
  value or prose description when validating provider randomness.
- Regression coverage must submit the same prompt repeatedly through the real
  adapter with a fake MCP client and deterministic random samples, assert that
  each outgoing request receives its sampled seed, and cover the lower and
  upper bounds `1` and `4294967295`. No paid generation is required. Keep the
  legacy adapter's `seed=None` behavior and V5's other generation parameters.
