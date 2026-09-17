# Meme/image reading diagnosis — 2026-09-16

## Scope

User requests diagnosis of recent attempts to read a member's meme. Read-only SQLite/log/source inspection, an offline ChatAgent backend-capture reproduction and one bounded read-only attachment GET. No model/generation calls, QQ sends, application/config changes or service restart.

## Exact records and symptoms

- Record 691, 11:43:48 +08:00: QQ `GROUP_MESSAGE_CREATE`, content `<faceType=6,faceId="0",ext="eyJ0ZXh0IjoiIn0=">`. Unlike a text-only emoji, the stored payload also has an attachment with a signed HTTPS URL, MIME image/jpeg, dimensions1079×1081 and reported size65831bytes. Raw URL, query credentials and member identifiers are intentionally omitted.
- Record 692, 11:44:00: correctly mentions the bot and asks whether it can see the image. Bot record693, 11:44:32, says it sees an image marker but received no actual image and asks for re-upload.
- Record696, 12:08:36: correctly mentions the bot and asks to read the preceding message as a meme. Bot record697, 12:09:20, describes faceType/faceId and the empty decoded text field but again lacks the pixels.
- Corresponding context logs at 11:44:15.484 and12:08:55.391 show all50 candidate history records included, 4102/2936 characters respectively, dropped_message_count=0 and truncated=false. This failure was not caused by the10000-character history budget.

## Root cause: ordinary chat never attaches image pixels

1. Gateway persistence retains attachments in `payload_json`, but `database/group_messages.py:_to_history_message` projects them to only `GroupHistoryMessage.has_image`. That history type has no image bytes or attachment URL fields.
2. `services/agents/chat.py:_history_message_to_wire` serializes record metadata, text and `has_image`; `ChatAgent.run` sends JSON text to the backend and never supplies `input_images`.
3. `services/ai/codex_sdk.py:CodexSDKBackend.run` already supports `input_images`, defaulting to an empty tuple. `_run_turn` adds SDK `local_image` inputs only when actual image assets are supplied. Ordinary chat therefore has zero visual input and cannot read the meme's pixels. The face extension's empty text cannot replace those pixels.
4. Current-message attachments are also absent from `IncomingGroupMessage` / `_incoming`. History retrieval excludes the current source message. Merely re-uploading a screenshot, as suggested in the bot's response, does not connect the missing visual-input path.
5. The image-edit path is separate: ConversationWorkflow resolves the selected reference only after ChatAgent returns an edit action, and `CodexResponsesImageService` supplies it as `input_images` to its image-only backend. That explains why record688's earlier edit generated successfully at11:43:30 and was delivered as image record690 at11:43:34, while ordinary image reading still failed.

## Offline reproduction

Reconstructed each request's historical reference selection from SQLite mode=ro, using production `_row_to_message` / `_to_history_message`, `prepare_group_context(..., char_limit=10000)` and the real ChatAgent with an AsyncMock backend that only captures arguments and raises a fixed offline exception.

Both record692 and696:

- Original image record691 included;50 reference messages; no truncation.
- Image wire fields: record_id, role, sender_name, sent_at, content, message_type, reply_summary, has_image.
- `has_image=true`, content retains the face marker.
- `input_images` argument absent; effective image count0. No real model call.

This verifies a wiring gap independently of provider vision capability or the current validity of the URL.

## Attachment availability limit

At13:41:10 +08:00, one GET through the actual bounded WebImageService to record691's stored attachment on multimedia.nt.qq.com.cn returned HTTP400, classified download_failed. No raw response, URL/query or credentials were printed. This proves the historical URL is not currently readable by that downloader; it does not establish expiry or its availability at11:44. The original chat never reached a download attempt. Do not classify all QQ-hosted attachments as expiring after the application's own15-minute temporary-media TTL.

## Repair direction, not implemented

- Connect explicitly requested image reading to actual image assets: select the current/requested historical record, safely download and validate its image, then pass it via ChatAgent/backend input_images with a clear record association.
- Include attachments on the current message as well as referenced history; constrain image count/size and distinguish absent/expired/denied/download-failed inputs from a successful visual read. Avoid attaching every image in the50-message history automatically.
- If reading older images must remain reliable, add a separately designed bounded cache/retention policy at receipt; the current database stores signed URLs, not original user-image bytes.
- Add a regression of gateway attachment → selected image → actual SDK local_image input for ordinary chat, plus historical-reference and unavailable-image cases. Existing image-edit tests do not cover ordinary visual input.

The service remains PID1192195, started2026-09-16 11:14:38 +08:00. This diagnosis does not deploy or enable ordinary-chat vision.
