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
