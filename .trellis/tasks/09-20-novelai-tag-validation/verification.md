# Verification

## Reproduction before repair

The original description was `光之美少女的索拉`. The recorded three failures
occurred at 15:18, 15:20 and 15:22 Asia/Shanghai on 2026-09-20; surrounding
requests for other characters succeeded. A bounded independent live probe
passed image-prompt audit, then received the planner output
`["sora_harewataru", "hirogaru_sky!_precure"]`. The planner parser raised
`InvalidAgentOutput` at `queries` before adapter execution. Both old validators
independently rejected `hirogaru_sky!_precure`. Direct remote `suggest_tags`
accepted it and returned the copyright and related character candidates.

## Live verification after repair

Used the deployed model/configuration with isolated temporary Codex runtime and
work directories. No production prompt session writes, QQ sends, image calls,
or quota reservations. Diagnostic output contains only stage/aggregate data.

- The actual adapter accepted ` Hirogaru Sky! Precure `, normalized it, and
  retrieved the exact `hirogaru_sky!_precure` copyright among four candidates.
- Audit of the original description passed.
- The live planner again included an exclamation mark in a two-query plan.
  Both lookups completed (three and four candidates).
- Generation produced three distinct ASCII options, lengths 287/285/289, within
  the existing 500-character limit. Elapsed audit/lookup/generation: 66.3 seconds.
- Revision (`保留索拉，改成在晴空下挥手`) planned four concepts, again including
  the exclamation mark. All four lookups succeeded (4/5/3/7 candidates), and
  authoring returned three distinct ASCII options of lengths 280/287/263.
  Total live probe elapsed time: 108.9 seconds.

## Automated validation

- Implementer: 49 focused schema, tag-adapter, prompt-agent and NovelAI workflow
  tests passed. `compileall` for source/tests and `git diff --check` passed.
- Regression exercises the observed Sora queries through the actual adapter
  with only the MCP transport mocked, for both generation and revision.
- Invalid plan, invalid/non-ASCII author output, safe stage logging, existing
  unavailable/tag-failure mapping and absence of failed sessions are covered.
- No linter or static type-checker is configured by this project.
- Independent Trellis check found no issues and made no changes. Full suite:
  343 unit tests passed in 18.060 seconds. Source/test compilation and
  `git diff --check` passed.

## Deployment

Restarted `aiqq.service` at 2026-09-20 15:43:46 Asia/Shanghai using the existing
systemd unit. New PID 2795345, active/running, NRestarts=0. Startup logged
`aiqq_ready` at 15:43:50. HTTP health returned 200/status ok; QQ gateway connected,
database open, NovelAI configured and ready. The unit/configuration were not
changed. Existing dirty work was retained and no commit was made.

## Root-cause prevention

The defect was a restrictive local assumption about valid tag punctuation,
combined with a coverage gap: generic scene smoke tests did not include real
copyright names. Duplicated validators meant a planner-only fix would still
fail in the adapter. Both boundaries now share one rule, and the regression
exercises their combined behavior with observed real tag spellings. Stage logs
and typed output-validation errors make a future format failure distinguishable
from provider unavailability. The existing tag contract documents these cases.
