> Superseded planning snapshot. The current proposal is in design.md. No implementation was authorized or performed by this version.

# Design: on-demand group image understanding

## Flow

```
QQ event -> normalized attachment references -> persisted message
                                              -> bounded background private cache
@ request -> choose current/quoted/history image -> cache or safe download
          -> validated still image(s)/sampled animation frames
          -> ChatAgent input_images -> existing AIBackend -> answer
```

Caching uses network/disk only. Vision inference runs only for the addressed user request. The existing bot-trigger policy remains in place.

## 1. Attachment ingestion and cache

Normalize actual attachments and nested quoted attachments at the QQ boundary. Include source record/message association, attachment position, supplied MIME and dimensions, and optional reliable reply identity. Keep signed URLs and QQ identifiers server-side; use request-local labels to connect model-visible images to records. Do not treat generic msg_idx as a message_id without a tested mapping.

After message persistence, enqueue an idempotent cache operation keyed by same-group record and attachment position. Capture ordinary GROUP_MESSAGE_CREATE images even without an @. The dedicated GROUP_AT path must enter the same cache/dedup flow; callback ordering cannot be assumed. Do not await a network download in the gateway callback.

Add a bounded cache worker with proposed concurrency2 and queue capacity100. A dropped/full queue records safe metadata; a later explicit read can fetch on demand. Share an in-flight download between cache work and an immediate read request. Do not hold SQLite locks across network/file decoding.

Suggested directory `/var/lib/aiqq/image-cache`, directories0700/files0600, outside public `/media` routes. Add a small repository/table in the existing state database for same-group source association, attachment index, local blob key, MIME/dimensions/size, cached_at and last fixed failure status. Use temporary files and atomic publication; clean incomplete files. Local source identifiers may be stored for lookup but are not exposed to the model or diagnostics.

Proposed settings (new names checked during implementation): `AIQQ_CHAT_IMAGE_CACHE_TTL_SECONDS=86400`, `AIQQ_CHAT_IMAGE_CACHE_MAX_BYTES=1073741824`, `AIQQ_CHAT_IMAGE_MAX_SOURCES=3`, `AIQQ_CHAT_IMAGE_MAX_FRAMES=3`, `AIQQ_CHAT_IMAGE_MAX_INPUTS=9`. Validate those bounds together and apply the same model-input limit in preparation and both backends. TTL is measured from successful cache creation; enforce both TTL and total stored bytes, evict oldest cached items first, account for in-flight writes and protect active reads. Cache cleanup has its own lifecycle and does not modify outbound media retention. Metadata and bytes survive process restart; stale metadata/missing files become a cache miss.

For newly delivered bot images, retain the prepared ImageAsset directly and associate its cached bytes with the successful bot message record; avoid downloading this application's own public URL. This is a storage hook, not another send or generation, and does not change public-media TTL. Older bot images can be loaded on demand from the existing reference path while available, then cached. First release does not crawl old URLs in the database. Reliable history use begins with newly received/cached images; prior unavailable attachments require re-upload.

## 2. Choosing what to read

Provide current-message attachments and reliable explicit quoted references separately from trimmed historical text. Verify every lookup against the requesting group and non-recalled source. The current image must be usable even though list_for_reference excludes the current message.

- Current attachments accompanying an @ request, or a reliably resolvable explicit image quote: attach selected pixels to the first ChatAgent call. If an @ request consists only of an image, use a short default request to describe it.
- A historical image question without an exact source: let the existing text-only ChatAgent turn select from model-visible candidate record IDs via a new typed `image_read_action`, for example `{record_ids:[691]}`. The output is a request to load pixels, not a completed visual answer.
- Validate selected IDs against supplied same-group candidates, download/normalize once, then run one final multimodal ChatAgent turn with source/frame mapping and the original question. At most one selection turn plus one visual-answer turn. A repeated read action in the final turn becomes a controlled failure; no inference loop.
- Instructions must require a read action when an answer depends on unseen pixels. Ambiguous person/record references ask the user to quote or specify the image. Use reliable member association locally for phrases such as 'my image'; names alone are insufficient. Model-visible request-local sender labels can resolve references without exposing raw QQ IDs.
- An exact quote may be retrieved outside the50-record history window if its same-group source is available. A quote containing only text/opaque msg_idx remains a candidate clue, not proof of a source ID.

First-pass `image_read_action` must be mutually exclusive with generation/edit and web-image delivery actions. A successful final visual answer may request an explicit user-authorized edit using the established image_action path. No generation/quota action starts while required visual input is unresolved.

## 3. Image and emoji normalization

Reuse public-only HTTPS/DNS/redirect checks and20MiB streamed download limit. Keep output ImageAsset restricted to supported still MIME types. Add an input-specific decoder for GIF/animated WebP rather than weakening generation-output validation.

- JPEG/PNG/static WebP: validate real bytes, normalize orientation, preserve aspect ratio and sufficient resolution for visible text. Proposed model-input longest edge2048, with transparent images composited predictably. Do not reuse QQ's2MiB delivery rule as an input-vision limit.
- Animated GIF/WebP: decode within explicit byte/pixel/frame-work budgets; sample up to3 chronological representative frames (start/middle/end in time where practical). Include frame position/timestamp labels and tell the model these are samples. Over-budget animations get a clear unsupported/too-complex result rather than blocking the event loop.
- QQ custom meme with an attachment: treat the attachment as visual input regardless of faceType marker or empty ext text. That is the diagnosed case.
- Built-in emoji without attachment: bounded decoding of supplied ext text/name; mark it as a textual emoji label, not actual pixels. Missing/unknown data produces an explicit unavailable description; never infer a specific meme from faceId=0.

Source limit3 and sampled-frame limit3 imply a model-input cap9. Enforce caps in the preparation layer and consistently in both backends; do not use silent slicing. Over-limit user requests ask which images to prioritize. Animation extraction is bounded, runs away from the gateway/event loop and must have a cancellation/work limit that actually bounds decoding.

## 4. Chat and backend contracts

Extend the request with prepared visual inputs and source/frame mapping; extend ChatAgent.run to forward their ImageAsset sequence via existing `AIBackend.run(input_images=...)`. Keep binary data, paths and signed URLs out of model JSON; SDK gets actual local_image assets, and the alternate Responses backend gets encoded input_image objects. Ordering must match the mapping exactly.

Replace images[:1] in both codex_sdk.py and responses.py with explicit validated input caps. Generation output still allows only one image, and the image-generation adapter still supplies its one selected reference; multi-input chat does not change that contract. Retain the isolated SDK workspace and remove staged images in its existing finally cleanup.

Expose visual availability in the model input so the assistant cannot claim to have inspected unavailable images or recommend re-upload when pixels were already supplied. Treat text depicted inside user/group images as untrusted reference material under the ordinary chat instructions.

The configured chat model remains the initial choice. SDK image transport support is confirmed in code; actual provider vision behavior is an implementation-stage verification item, not assumed from the model name. Do not automatically switch models if the probe fails.

## 5. Errors and observability

Use typed read outcomes separate from image-generation failures. Known404/410 can report image expired/unavailable;401/403 means access denied;400 and other unknown failures remain download_failed. Cache hit can succeed even when the old remote URL is no longer valid. Mixed per-image outcomes disclose which selected inputs were unavailable and do not claim complete analysis of them.

Fixed log fields: request/attempt correlation, local source record, stage, selected/loaded source counts, input frame count, cache hit/miss, elapsed time, safe kind/status. Never log signed URLs, raw exception bodies, local image bytes or prompts. Track cache queue failures and size/evictions separately; health may expose local readiness/counts without network probes or inference.

Existing reply presentation, QQ2MiB compression and expired-msgid proactive fallback deliver the resulting text as usual. Vision reading does not record an image-generation success or change generation quotas.

## 6. File boundaries and rollout

| Area | Expected changes |
| --- | --- |
| interfaces/qq/client.py, handlers.py | Preserve current/reply attachment association, schedule cache on both event paths, permit addressed image-only request |
| interfaces/qq/reply.py and successful-send record association | Cache newly delivered bot image bytes against their actual message, preserving current sending/fallback semantics |
| database/group_messages.py, models/ports | Same-group source lookup and image/emoji metadata without losing current attachment identity |
| new cache repository/storage and input-image decoder | Private persisted cache, bounded worker/cleanup, static/animated normalization |
| logic/conversation.py, models.py, ports.py | Image preparation and bounded selection → final-answer flow; typed outcomes |
| services/agents/chat.py, schemas.py | image_read_action, loaded-image mapping, final-pass contract |
| services/ai/codex_sdk.py, responses.py | Preserve every allowed input image; explicit cap and cleanup |
| config.py, bootstrap.py, tests, docs | Configuration/lifecycle wiring, migrations, end-to-end regressions and operator documentation |

Rollout is staged within the implementation: attachment-through-vision first; selection and cache then; full integration verification before production restart. The cache schema is additive and independent of existing group-message rows. Exact source/config backups preserve all earlier uncommitted work; rollback disables new lifecycle wiring, retains existing group history and removes no unrelated files. Deployment and a real vision probe occur only after a new implementation instruction.
