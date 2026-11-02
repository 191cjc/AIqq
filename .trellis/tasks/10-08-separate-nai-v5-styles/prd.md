# Separate NovelAI and Nai v5 user styles

## Goal

Give users independent personal styles for the original NovelAI service and Nai v5.

## Background

Both image workflows currently receive the same NovelAIStyleRepository in
src/aiqq/bootstrap.py. The existing NovelAI画风 command therefore affects both
providers, contrary to the requested separation.

## Requirements

- Keep NovelAI画风 attached to the original provider and existing saved styles.
- Add Nai v5 画风 to command parsing, the QQ command panel and feature menu.
- Each command saves, replaces or clears only its provider's persistent style.
- Each image workflow reads only its provider's style, without copying or falling
  back to the other provider. Nai v5 starts without a style.
- Preserve sender identity ownership, cross-group persistence, English style
  validation, outer trimming, weighted tags and internal newlines.
- Provider-specific acknowledgements and failures name the correct service.
- Validate and restart the running service to apply the change and panel entry.

## Acceptance Criteria

- A legacy-only saved style is applied by NovelAI and omitted by Nai v5.
- One member can save different styles for both providers; both survive restart.
- Replacing or clearing either style leaves the other style intact.
- Nai v5 Chinese scenes still reach its direct workflow with punctuation preserved.
- Both setting buttons fill the input without automatically submitting a clear.
- Setting styles triggers no provider generation or image quota charge.
- Focused and full unit tests and git diff --check pass; health is healthy after
  restart, with Nai v5 configured and ready.

## Out of Scope

Image provider settings, quota policies, prompt translation/tag workflows and
unrelated existing workspace changes.
