# NovelAI quick image generation

## Goal

Add a public QQ panel command named `NovelAI快速生图` that accepts a scene
description, completes NovelAI prompt authoring, and immediately continues to
image generation without asking the sender to choose one of the three
prompt-option buttons.

## Background

- The existing `NovelAI提示词 <description>` flow audits a non-empty scene
  description, performs bounded Danbooru tag lookup, authors three distinct
  English prompt options, stores a revision session, and returns option buttons.
- The existing `NovelAI生图 <prompt>` flow loads the sender's saved style,
  audits the combined English prompt, applies quota/admission rules, sends the
  persistent image-start notice, invokes NovelAI once, and delivers the result.
- The QQ panel and in-message feature menu are separate surfaces backed by the
  same command parser and router.

## Requirements

- Add `NovelAI快速生图` to the public QQ command panel. The sender supplies a
  scene description in the same way as `NovelAI提示词`, using
  `NovelAI快速生图 <description>` (with or without the leading slash supported
  by the shared parser).
- One invocation must author a NovelAI prompt and, when authoring succeeds,
  continue directly to NovelAI image generation without returning prompt-option
  or revision buttons.
- Prompt authoring must retain its existing three-option contract. The quick
  command must always pass the first authored option to image generation; it
  must not select randomly or make another model call to choose an option.
- An empty scene description must return a command-specific usage message and
  must not call prompt authoring or image generation.
- Reuse the current prompt-authoring and image-generation policies, including
  input safety review, Danbooru lookup, ASCII prompt validation, per-sender
  NovelAI style, quota/admission, image-start notice, provider errors, and final
  delivery behavior.
- Do not make duplicate provider calls or consume image quota when prompt
  authoring fails.
- Do not create a prompt-revision session for the quick command because no
  option or revision controls are returned. The ordinary prompt command must
  continue creating its scoped 15-minute session.
- Preserve the existing `NovelAI提示词`, `修改NovelAI提示词`, `NovelAI生图`,
  and `NovelAI画风` behavior.

## Acceptance Criteria

- [x] The QQ panel exposes exactly one managed `NovelAI快速生图` entry and panel
      synchronization remains idempotent for managed and shared panels.
- [x] The command parser and router distinguish the quick command from existing
      NovelAI commands.
- [x] The quick command preserves the sender's complete trimmed scene
      description and rejects an empty description before any model/provider
      work.
- [x] A successful invocation performs prompt authoring before exactly one
      NovelAI generation using the first authored option, sends no intermediate
      prompt text or prompt-option keyboard, and creates no revision session.
- [x] The generated image uses the invoking sender's current saved style and
      existing quota, safety, notification, and delivery behavior.
- [x] Prompt planning, tag lookup, authoring, audit, style, quota, and provider
      failures stop at the correct boundary and retain their current user-facing
      diagnostics.
- [x] Existing NovelAI commands and tests remain compatible.

## Out of Scope

- Changing the three-option interactive prompt workflow or revision sessions.
- Changing NovelAI model parameters, safety rules, styles, quotas, or retries.
- Changing GPT image generation or ordinary conversation image actions.
- Adding `NovelAI快速生图` to the separate in-message feature-menu keyboard.
- Restarting or deploying the running service, sending a real QQ message, or
  invoking paid image generation as part of automated verification.
