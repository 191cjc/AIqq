# Design and boundary

The behavior belongs at the admitted image-provider boundary in the three
business workflows, not at the top of command parsing. Add an optional no-arg
async `on_image_start` callback (a named port type if helpful) to explicit GPT,
NovelAI and conversation workflow calls; invoke after validations/admission and
inside the existing admission try/finally. Share the small invocation helper in
`logic/image_generation.py`: catch/log only ordinary callback failure with safe
exception type; allow cancellation to propagate. No provider calls for rejected
requests and no claims of starting on prompt-option generation.

Keep QQ transport and wording in the QQ interface. Add one plain-text start
notice method to `ConversationReplySender`, using existing sender delivery and
member context for expired-msgid recovery. The explicit command router supplies
a per-request one-shot callback, reserving a reply sequence before the send and
using the following sequence for the final response, even when the start send
failed ambiguously.

For conversation, supply a distinct callback rather than passing start notices
through capped/temporary model progress. The existing reporter is the sequence
owner: expose a synchronous reserve method and have temporary progress reserve
before awaiting transport too. Start notices reserve from the same owner, are
not recalled, and still work after temporary progress is closed on deferral.

Deferred completion must obtain the current next sequence when finishing,
rather than capture a stale integer before a later start notice. A narrowly
scoped optional next-sequence getter on deferred reply completion (and the
handler's no-link completion path) preserves old callers. Reserve the deferred
notice's sequence once; a small handler-local lock serializes the start notice
with awaited deferred setup and fallback delivery, so a late start cannot send
a higher sequence before the earlier reserved deferred notice. On setup failure,
consume that reservation and allocate a fresh fallback-notice sequence. Keep
final sequencing correct on late callbacks by reading the live sequence owner.
Generation finishes before final delivery, so no image-start callback remains
once final result sends begin.

Expected source files: `logic/ports.py`, `logic/image_generation.py`,
`logic/novelai.py`, `logic/conversation.py`, `interfaces/qq/command_router.py`,
`interfaces/qq/progress.py`, `interfaces/qq/reply.py`, `interfaces/qq/handlers.py`,
plus their relevant tests/fakes. Add no database/config/model/provider changes.
Parent owns task artifacts and `.trellis/spec/backend/image-services.md` updates.

## Amendment: visible original-message quote

User clarified that the start notice must visibly reply to the original message.
Recent live notices were passive replies with source associations but lacked
`message_reference`. Installed botpy `BotAPI.post_group_message` supports this
field and serializes it into the group-message request; `Reference` contains
`message_id` and `ignore_get_message_error`.

Add `quote_source: bool = False` to the text-sender port and implementation.
Only `ConversationReplySender.send_image_start` opts in. For an opted-in
nonempty source, send `message_reference={"message_id": source_message_id,
"ignore_get_message_error": True}` in addition to existing `msg_id`/`msg_seq`.
Keep it in recorded successful payloads and in the existing expired passive
fallback; ignoring missing original-message retrieval allows the status to
remain deliverable if the source was deleted. Do not introduce new retries or
alter all other text/Markdown/image messages. Scope: sender port, QQ text sender,
start presenter, installed-SDK payload regression and existing notice coverage.

## Amendment: preserve QQ content rejection in user copy

Observed source records 4519/4598 generated and uploaded successfully, followed
by QQ send-message rejection 40034006, `消息内容违规`. Botpy drops numeric
codes and raises ServerError with that message; the presenter currently catches
all image-send exceptions and discards the specific cause.

Smallest change: add a shared `ImageContentRejected` exception in
`src/aiqq/exceptions.py`. At the QQ transport `_deliver` boundary, after existing
failed-attempt recording, translate only exact `ServerError("消息内容违规")`
for upload_image or send_message with msg_type 7 to that exception using a fixed
safe internal message. Other calls and errors propagate unchanged. This covers
both initial sends and an existing expired-reply fallback without adding retries.

`interfaces/qq/reply.py` catches that shared type in its image-send branch and
selects fixed rejection copy; unrelated failures retain the current generic text.
Copy refers to the QQ response, makes no unsupported claim about which visual
element violated rules, and works for generated or retrieved images. No refactor,
SDK replacement, schema migration, retry, prompt or provider-setting changes.

Owned code/test paths: exceptions.py, services/qq/sender.py,
interfaces/qq/reply.py, tests/unit/services/test_qq_sender.py and
tests/unit/services/test_conversation_reply.py. Parent owns artifacts/spec sync.
