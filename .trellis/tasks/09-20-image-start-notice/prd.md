# Image generation start notice

## Request
Before NovelAI or GPT drawing starts, send a group message:
`已收到提示词，开始作画。`

## Behavior
- Cover direct GPT/NovelAI commands (with or without slash), panel option
  commands, and conversational GPT generation/edit actions.
- Send exactly one persistent plain-text notice to the originating group and
  message after required prompt/audit/reference/quota checks pass, immediately
  before calling the generation service. Do not send for rejected/empty/quota
  denied requests, prompt-options/style operations, chat, or web image search.
- The notice must visibly quote the original triggering message using QQ's
  explicit message-reference field, not only its passive-reply `msg_id`.
- Preserve final text/image delivery and monotonically increasing reply
  sequences, including after ordinary progress updates, long-task deferral,
  a failed start notice, or a failed deferred-link setup.
- QQ notice failure must not prevent generation. Cancellation must propagate
  and release admission; do not add retries or duplicate provider calls.
- Retain all existing dirty work, provider settings, prompt/style/safety rules,
  quota accounting, image-only final delivery and expired-message recovery.

## Acceptance
- Tests demonstrate the actual event order: checks, group notice, provider call,
  then final reply; each provider and conversational generate/edit are covered.
- Installed botpy serialization includes `message_reference.message_id` equal
  to the triggering message ID. Preserve the reference on expired-msgid fallback
  while clearing passive fields and mentioning the original member. Other
  messages do not automatically gain quoting.
- Tests cover no-notice cases, notification failure/cancellation, deduplication,
  reply sequencing, progress-cap exhaustion and deferral with later image start.
- Full unit suite, compileall and diff checks pass; independent review passes.
- Restart the service after verification and check readiness. Do not send
  synthetic QQ messages or generate paid images merely to test this change.

## Follow-up: image rejection copy

For the observed QQ content rejection, replace the generic delivery error with
`QQ 返回“消息内容违规”，图片未能发送。请调整画面内容后重试。`
Only definite QQ image-content rejection receives this text. Unknown delivery
errors retain the existing generic copy; ordinary text errors are not image
errors. Cover foreground and deferred completion and existing expiry fallback.
Do not change image prompts, styles, moderation, generation, retries or quotas.
