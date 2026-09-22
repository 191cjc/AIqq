# Repair NovelAI prompt tag validation

## Problem
The request `光之美少女的索拉` repeatedly receives a generic prompt-service error.
A live reproduction passes audit, then plans `sora_harewataru` and
`hirogaru_sky!_precure`; local validation rejects the legitimate exclamation mark
before any tag lookup. A direct `suggest_tags` request succeeds for that title.

## Requirements
- Accept legitimate exclamation marks inside Danbooru tag names on initial
  prompt creation and revisions, preserving the spelling for real retrieval.
- Keep query validation consistent at the planner and adapter boundaries.
- Preserve query bounds, operator/control-character rejection, mandatory tag
  retrieval, scene-only authoring, and the separate personal-style feature.
- Distinguish invalid model output from service unavailability in user replies,
  with safe logs identifying the planning or authoring stage.
- Preserve all pre-existing uncommitted work.

## Acceptance
- Actual Sora planner output reaches the real adapter and final author in tests,
  on both create and revise paths.
- Ordinary supported punctuation still works; unsafe/invalid queries stop
  before transport and do not produce an options session.
- Invalid planning/final output has stage-specific diagnostics without raw
  prompts, query strings, identifiers, endpoints, or exception messages in logs.
- Targeted regressions and the full unit suite pass.
- A bounded live text-only probe of the original description produces three
  options with real Danbooru retrieval; no QQ messages or images are generated.
- Restart after validation and verify service readiness.
