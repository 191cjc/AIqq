> Superseded planning snapshot. The current proposal is in implement.md. No implementation was authorized or performed by this version.

# Implementation proposal — not started

The user has requested a plan and selected on-demand reading. Do not change application code/configuration, start model calls, restart, or dispatch implementation until the proposal is approved in a subsequent request.

## Ordered work

- [ ] Capture exact baselines and read backend/image/group-message specs; curate the active implement/check manifests for this scope before any worker dispatch.
- [ ] Add same-group current/quoted attachment normalization and typed visual-input/read-selection contracts. Protect event dedup and image-only @ messages.
- [ ] Implement bounded static/animated normalization. Keep generated-image validation and outbound2MiB compression distinct.
- [ ] Pass prepared images through real ChatAgent and both backends, preserving all allowed images and source/frame order. Remove silent single-image slicing.
- [ ] Implement one optional history-selection pass, validate IDs and enforce the final-pass no-loop rule. Handle text-only system emoji and ambiguity.
- [ ] Add private persisted cache/repository/configuration, bounded asynchronous ingestion and lifecycle cleanup. Verify immediate request/in-flight cache coordination and restart behavior.
- [ ] Wire retrieval to prefer cache and report typed source failures; retain separate image generation/quota/delivery paths.
- [ ] Review full cross-layer behavior independently; run focused and complete packaged tests and configured static checks. Update actual image/vision contracts in specs and README.
- [ ] Under implementation authorization, perform at most one initial real vision probe with two locally prepared harmless test cards whose identifying text appears only in the pixels. Verify configured chat model actually reads both. No image generation or manual QQ message. If unsuccessful, inspect evidence before proposing another call/model change.
- [ ] Back up additive database/config/source changes, inspect active workflows, deploy/restart, verify QQ/NovelAI and local/public health. User-initiated group requests verify visible image/meme answers.

## Required offline acceptance cases

1. Current image+@ text and image-only @ both supply actual pixels. Duplicate raw/AT events do not duplicate inference/cache writes.
2. Send meme first then ask about it; the selected historical attachment reaches the final model input. Test the diagnosed faceType6/empty-ext plus JPEG payload shape.
3. Reliable explicit quote, text-only quote with opaque msg_idx, same-name members, missing original and ambiguous history. Never guess or read a different group's record.
4. Multiple source images survive actual SDK local_image input construction and alternate Responses serialization; cap errors are explicit. Image-generation adapter still uses one reference and output remains one image.
5. GIF/animated WebP frame order/sampling/budgets, static images, malformed or oversized input, orientation and transparency; no unbounded decoder work.
6. Cached picture remains readable after mocked original URL404; delivered bot image stays readable after its public-media TTL; fresh URL fallback; failed download vs expiry/denial/timeout; uncached historical image failure.
7. TTL and total-cap eviction, restart persistence, corrupt/missing files, queue pressure, cancellation, cache-write/read race and in-flight dedup. No network while holding database locks.
8. Zero real model calls during ingestion and cache work; one selection + one vision call at most, no generation quota use for reading, no recursive selection. Supplied pixels vs missing-pixels status reaches user wording accurately.
9. Existing GPT/NovelAI, replies, compression, deferred tasks and expired-msgid fallback regressions remain green. No automatic QQ sends or paid calls in tests.

Suggested full suite remains `.venv/bin/python -m unittest discover -s tests/unit -q` plus `git diff --check`; precise focused commands depend on final module names. The project has no configured standalone lint/type-check runner.

## Plan status

PRD, design and execution checklist are drafted. On-demand product behavior is confirmed. Numeric bounds are proposed defaults, not active settings. No implementation or deployment has occurred.
