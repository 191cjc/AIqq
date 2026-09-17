# Recent image request diagnosis — 2026-09-16

## Scope and current state

User requested investigation of recent image failures. This iteration reads logs/database/code and performs an offline sender reproduction only. No application/config changes, restart, paid generation, historical replay or QQ messages.

At inspection, the newest group record is 682 (2026-09-16 10:55:00, an ordinary image/emoji message). No new group generation request appears after the NovelAI restoration at 10:54:11. Service PID 1185136 remains active, NRestarts=0; local health reports QQ connected, database open and NovelAI configured/ready true. These snapshots do not prove delivery of a new group generation request.

## Correlated requests

| Request record / time (+08:00, September 15) | Observed result |
| --- | --- |
| 620 / 17:44:50, retry earlier image | Reference record 609 returned HTTP 404 at 17:50:15, before generation. The reference was this application's temporary media from 16:40:33. Expired-reply fallback for QQ code 40034031 succeeded and recorded the generic failure as record 621. R8 now improves that wording, but does not extend media retention. |
| 627 / 17:56:49, apply the earlier edits | Reference download succeeded. GPT edit started 18:03:00.617 and finished 18:04:16.462 with outcome=success, HTTP 200, 75845 ms. QQ rejected the following markdown at 18:04:17.084 with code **40034005**, message **回复消息msg_id已过期**. No fallback attempt or bot response was recorded for this source. The uncaught error reached the gateway, which reconnected at 18:04:22. |
| 636 / 20:35:18, draw Ruan Mei | Stored mentions contain is_you=false and bot=false. The user mentioned a group member; raw-event routing correctly did not invoke the bot. No generation or conversation context event for this request. |
| 641 / 20:39:44, explicit NovelAI command | Known missing configuration; failed at 20:40:06 and replied as record 642. Restored and independently verified in R9, including one real image generation. |
| 646 / 20:41:19, find Ruan Mei art | Web image search succeeded, with text record 649 and image record 650 at 20:43:14. This is existing artwork retrieval, not generation. |

Primary log evidence: `/var/lib/aiqq/aiqq.log.2026-09-15`, lines 485–490, 495–501 and 585. Group records were read via SQLite mode=ro; mention IDs, credentials and image URLs are omitted here.

## Remaining reproducible defect

`src/aiqq/services/qq/sender.py:17` defines only `msgid已经过期,不能回复` as the known expiry message. At line 185 the sender compares the SDK ServerError text using exact equality. The installed SDK discards the numeric QQ error code, so the alternative definite rejection `回复消息msg_id已过期` (40034005) is not recognized.

`ConversationReplySender.send` saves prepared image bytes but awaits the initial markdown before reaching the image-send loop. That unrecognized expiry therefore prevents both the text and image from being delivered. `GroupMessageHandler.handle` does not catch the final reply-send exception, explaining the gateway error and reconnect. Saved bytes still use temporary retention and are not a durable failed-delivery queue.

The request lasted about 7m28s overall, including about 6m12s before the image API began and 75.845s for the image API itself. It was below the current 600-second foreground threshold but above QQ's 5-minute passive reply window. Increasing foreground wait does not extend QQ's reply validity.

## Offline verification

Invoked the current production `QQMessageSender.send_markdown_reply` using existing test FakeApi/FakeRepository, the original-member fallback option and mocked ServerError outcomes. No code or test files changed and no network calls occurred:

- `msgid已经过期,不能回复`: two mock send calls, successful proactive fallback, one saved message.
- `回复消息msg_id已过期`: one mock send call, uncaught ServerError, zero saved messages.

Existing sender tests cover only the first valid expiry variant. The current deployed source still has this defect; the NovelAI restoration did not modify it.

## Concrete repair recommendation

1. Recognize both observed exact SDK expiry messages (or preserve structured QQ codes at a narrowly scoped transport boundary). Retain one proactive fallback attempt, original-member mention, media reuse and no retries for ambiguous send failures.
2. Add offline regressions for both variants through the real SDK error parser and for the presenter sequence (initial markdown expiry followed by image delivery), including non-expiry and failed-fallback behavior.
3. Contain terminal delivery exceptions at the foreground handler boundary with a safe delivery-failed diagnostic so a send failure does not escape into gateway handling. Do not report a delivered result or repeat generation after a delivery rejection.
4. Treat source-image retention separately: the observed 15-minute lifetime belongs to this application's temporary `/media` store; it does not establish a universal expiry duration for QQ-hosted attachments. R8 changes the error wording, not the storage policy.

No repair was applied in this diagnosis-only iteration.
