# NovelAI quick image generation implementation

## Implementation Checklist

- [x] Add the shared quick-command constant, command literal, and parser branch.
- [x] Add exactly one managed public-panel item and preserve shared-panel
      replacement/idempotency behavior.
- [x] Refactor `NovelAIPromptWorkflow` so prompt authoring can be reused without
      persisting a revision session, while preserving ordinary create/revise
      behavior and diagnostics.
- [x] Add `NovelAIQuickImageWorkflow` to validate input, select the first authored
      option, and delegate to the existing NovelAI image workflow.
- [x] Wire the quick workflow through bootstrap and the command router without
      adding an in-message feature-menu button.
- [x] Update README command documentation and the owning NovelAI backend spec.

## Focused Tests

- [x] Parser: slash/non-slash forms, complete multiline description, empty
      argument, and no collision with existing commands.
- [x] Panel: command presence, exact count, managed-panel update, shared-panel
      deduplication, and unchanged-panel idempotency.
- [x] Logic: empty input, prompt failure boundaries, first-option selection,
      no session creation, style/audit/quota/provider reuse, notification order,
      cancellation, and single generation call.
- [x] Router/bootstrap: correct scoped IDs and callback, no option keyboard,
      reply sequence after start notice, and shared workflow wiring.
- [x] Regression: ordinary prompt sessions/options/revisions and manual NovelAI
      generation remain unchanged.

## Validation

```bash
uv run python -m unittest \
  tests.unit.logic.test_novelai \
  tests.unit.services.test_command_router \
  tests.unit.services.test_qq_commands_ui \
  tests.unit.services.test_qq_panel \
  tests.unit.test_bootstrap
uv run python -m unittest discover -s tests/unit
uv run python -m compileall -q src tests/unit
git diff --check
```

Do not send real QQ messages, call paid image generation, restart the service,
or discard unrelated dirty-worktree changes during validation.

## Risk and Rollback Points

- Refactoring prompt authoring can regress ordinary session persistence; keep
  existing create/revise tests green before wiring the quick path.
- Reply sequencing can regress if the quick path sends an intermediate result;
  assert one final send and sequence advancement only from image-start notice.
- Panel managed-command matching can duplicate entries in shared panels; cover
  a pre-existing quick command in the shared-panel fixture.
- Before rollback or any broad formatting, isolate this task's diff from the
  substantial pre-existing worktree changes.
