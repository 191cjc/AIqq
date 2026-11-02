# Return up to five requested web images

## Requirements

- Ordinary web-image replies follow the user's requested quantity, capped at five.
- The model selects relevant, distinct direct public HTTPS image URLs in order.
- Missing selections produce text only. Partial download failure preserves usable
  images and explains the missing images; total failure preserves the text answer.
- Downloaded web images do not consume generation quota or emit generation notices.
- Do not restart or reload the service before the user explicitly requests it.

## Change boundary

The single-image contract currently lives in the chat output schema/instructions,
`ChatAgentOutput.selected_web_image_url`, the conversation downloader and the
`ConversationResult` length check. QQ presentation already iterates over images.
Replace the scalar selection with an ordered list and lift only the shared reply
limit. Keep GPT/NovelAI generation counts, source-link limits, image validation,
network restrictions, upload compression and existing unrelated edits intact.

Expected files: `logic/models.py`, `services/agents/{schemas,chat}.py`,
`logic/conversation.py`, affected unit-test fixtures/regressions, `README.md`, and
the backend image-service spec. No local refactor is required.

## Acceptance criteria

- Empty, one-image and five-image selections pass strict schema validation;
  six images, invalid URLs and malformed selections fail validation.
- Repeated selections are sent once, retaining order.
- A five-image selection reaches QQ presentation intact; each image gets a
  separate sequence number after the final text reply.
- Failed downloads do not suppress successful downloads, leak raw errors/URLs,
  or prevent the original answer from being returned. Cancellation propagates.
- Focused regressions and packaged unit discovery, compile checks and diff checks
  pass. Tests use local/mock services and send no real QQ messages.

## Confirmed scope

The user confirmed this request applies to web-image search replies only.

## Validation and deployment

- Focused suite: 72 tests passed.
- Full packaged unit discovery: 459 tests passed in 21.506 seconds.
- `compileall -q src tests/unit`, `git diff --check` and task context validation passed.
- Tests cover actual agent parsing, reversed download completion, QQ presenter
  storage/order/sequence, duplicate URLs, partial/total failures and cancellation.
- The user subsequently authorized restarting after modification. Restarted
  `aiqq.service` on 2026-10-16 at 15:33:32 Asia/Shanghai; MainPID changed from
  3096924 to 3461883. The unit is active/running with no automatic restarts.
- After restart, `/health` returned HTTP 200 / `status=ok`, QQ gateway connected,
  database open, zero unprojected events and zero storage write failures.
- Existing unrelated uncommitted work was preserved; no commits were created.
