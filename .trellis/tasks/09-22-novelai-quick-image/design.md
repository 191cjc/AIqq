# NovelAI quick image generation design

## Boundaries

The feature crosses the QQ command parser, public panel synchronization,
command routing, and NovelAI logic. It does not change provider adapters,
database schemas, prompt-agent schemas, model parameters, or reply transport.

The new command is panel-only. The separate in-message feature menu remains
unchanged.

## Data Flow

```text
QQ panel command with description
  -> parse as novelai_quick_image
  -> quick workflow validates non-empty input
  -> existing prompt authoring path
       input audit -> query planning -> Danbooru lookup -> three options
  -> select options.prompts[0]
  -> existing NovelAI image workflow
       load sender style -> combined audit -> quota admission
       -> image-start notice -> one provider call -> quota success
  -> existing reply sender delivers the final result without an option keyboard
```

## Logic Contracts

Split prompt authoring from interactive session persistence inside
`NovelAIPromptWorkflow`:

- A shared authoring method returns a typed result containing either validated
  `NovelAIPromptOptions` or the existing rejection/unavailable
  `ConversationResult`.
- `create()` uses that result, persists a revision session, and builds the
  existing three-option response.
- `NovelAIQuickImageWorkflow.run()` rejects empty input with quick-command
  usage text, calls shared authoring for non-empty input, selects the first
  option, and delegates generation to the existing `NovelAIImageWorkflow`.
- Quick authoring never persists a prompt session and never emits the
  intermediate three-option response.

The image workflow remains the sole owner of style loading, combined prompt
audit, quota accounting, image-start notification, provider invocation, and
generation error mapping. This prevents behavior drift between direct,
option-button, and quick generation.

## Interface Changes

- Define `NOVELAI_QUICK_IMAGE_COMMAND = "NovelAI快速生图"` once in the command
  module and include a new `novelai_quick_image` command literal/parser branch.
- Add the command to the public panel's managed set and desired command items.
  Existing copies in shared panels are replaced so synchronization remains
  idempotent and produces exactly one entry.
- Add a quick-workflow dependency to `ExplicitCommandRouter`. Its branch passes
  the member-scoped conversation key, member ID, and existing one-shot
  `on_image_start` callback, then sends only the returned final result.
- Wire one quick workflow in `bootstrap.py` using the same prompt and image
  workflow instances already used by the ordinary commands.

## Failure and Cancellation Semantics

- Empty descriptions stop before audit or model work.
- Input audit, planning, lookup, authoring, and output validation failures reuse
  their existing prompt-flow diagnostics and stop before style/quota/provider
  work.
- Style, combined audit, quota, notification, and provider behavior are exactly
  those of `NovelAIImageWorkflow`.
- Cancellation is not caught by the quick workflow; it propagates through the
  existing cleanup paths.
- A failed image-start notice remains non-blocking according to the existing
  notification helper.

## Compatibility

Existing commands retain their parsing, session persistence, keyboards, and
delivery. No migration or stored-data change is required. The panel update is
idempotent and is applied on the next normal application startup.

## Rollback

Remove the quick command constant/parser/panel entry, router dependency and
branch, quick workflow, and its bootstrap wiring. No data rollback is needed.
