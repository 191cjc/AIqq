> Superseded planning snapshot. The current proposal is in prd.md. No implementation was authorized or performed by this version.

# Ordinary chat image and meme understanding

Status: proposal only, 2026-09-16. The user requests a plan, not implementation or deployment. Existing task in_progress status covers earlier repairs and does not authorize implementing this extension.

## Goal and confirmed product decision

The bot should understand pictures and memes attached to the current request or selected from recent group chat: describe content, read visible text and explain the meme using the conversation context.

The user selected **on-demand reading**: cache group images when received, but invoke vision only when a user addresses the bot and supplies or asks about an image. Do not generate descriptions for every incoming picture or send unsolicited replies to ordinary image messages.

## Confirmed evidence

- `research/meme-reading-diagnosis.md`: record691 has a JPEG attachment, but requests692/696 sent only has_image=true and face-marker text to ChatAgent, with zero images. Both histories were untruncated.
- `src/aiqq/services/agents/chat.py:81` does not pass input_images; `src/aiqq/interfaces/qq/client.py:211` / IncomingGroupMessage omit current-message attachments.
- Both `services/ai/codex_sdk.py:_stage_input_images` and `services/ai/responses.py:_format_input` silently select images[:1]. The backend protocol already supports an image sequence.
- `services/images/validation.py` supports JPEG, PNG and WebP, with20MiB/4096²-pixel limits; GIF input needs a separate normalization step into supported still frames.
- A sampled QQ quote has msg_elements containing content, message_type and msg_idx; it does not establish a usable original message_id. Identity/quote resolution must use actual gateway evidence.
- The database retains attachment URLs rather than user-image bytes. The old signed URL returned HTTP400 during diagnosis; no conclusion about a universal QQ expiry interval follows.

## Requirements and acceptance

| ID | Requirement | Observable acceptance |
| --- | --- | --- |
| V1 | Current-message visual input | User sends an image with an @ request; the model receives real validated pixels and answers about them. An @ image with no text gets a brief description rather than the existing empty-prompt rejection. |
| V2 | Historical visual input | User sends a meme, then asks about it; the bot retrieves the selected same-group record and receives its cached pixels. Explicit resolvable references take precedence; ambiguous references produce a concise question rather than a guessed target. |
| V3 | Format coverage | JPEG/PNG/WebP pictures and image-backed QQ memes work; animated GIF/WebP are interpreted using up to3 labeled chronological sample frames. Text-only system emoji uses its supplied name/text, without inventing a picture from faceId=0. |
| V4 | Bounded cache | Incoming user images and newly delivered bot images enter a private cache without invoking the model. Proposed defaults:24hours retention,1GiB total, oldest-first eviction. Reading a cached image works after its original URL fails. Unavailable historical bytes are reported honestly. |
| V5 | Bounded model input | Up to3 source images per request, up to3 frames per animated source and9 model images total. No backend silently drops later images. An oversized/ambiguous selection prompts narrowing. Images are separately budgeted from the existing50-message/10000-character text context. |
| V6 | Clear failures | Missing attachment, cache miss plus failed source, known404/410, permission denial, timeout and unsupported/invalid image have appropriate fixed messages. Generic400 is not asserted to mean expiry. No visual claims are made when no pixels were supplied. |
| V7 | Preserve other workflows | On-demand vision does not consume image-generation quota, trigger image_generation or change the600-second foreground threshold, GPT/NovelAI settings, QQ compression or expired-msgid delivery behavior. Explicit generation/edit requests retain their separate existing workflow. |
| V8 | End-to-end verification | Offline tests trace gateway payload → cache/selection → actual SDK image inputs. After implementation approval, one bounded real vision probe verifies the configured chat model/channel before deployment; later group acceptance uses user-initiated messages. |

## Proposed defaults and scope

The retention, storage and input caps above are concrete implementation recommendations in this proposal, not changes applied to running configuration. Historical discovery uses the existing recent50 records; a reliably resolvable explicit quote may address a retained same-group record outside that window. Caching for24hours does not extend the ordinary textual history window automatically.

Support a short ordinary image question, visible text reading, and meme interpretation; sampled animations are not exhaustive video analysis. No continuous automatic captioning, historical bulk backfill, permanent archive, vector search, additional OCR provider, or arbitrary model access to stored files/URLs. Do not promise recovery of an old expired picture that was never cached.

No blocking product question remains for this proposal: on-demand behavior was explicitly selected, remaining numeric values are proposed defaults to review together. Implementation and rollout await a subsequent user instruction approving the plan.
