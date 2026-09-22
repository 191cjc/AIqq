# Journal - chenjiachun (Part 1)

> AI development session journal
> Started: 2026-09-14

---


## 2026-09-16 — R11 complete chat records and on-demand vision

Implemented authorized v4: durable full gateway events/versions, group-scoped message identity, complete50+current context, older history/vision skill scripts, isolated Codex tools and bounded Responses compatibility. URLs/metadata only; no original-image persistence. Preserved existing GPT/NovelAI, QQ compression and pure-image expiry fallback. Added deterministic vision-read error summaries.264 full tests plus75 final targeted pass; real gpt-6-astra two-card OCR probe passed76.36s. Deployed16:09:51 CST; QQ/NovelAI healthy, all808 original message rows/columns verified unchanged. Evidence: .trellis/tasks/09-14-fix-gpt-image-failures/research/chat-vision-plan/deployment-result.json. No commit; unrelated dirty changes preserved.


## Session 1: GPT image custom dimensions
<!-- trellis-session: v=2 fp=025d5a89de7879fc -->

**Date**: 2026-09-17
**Task**: GPT image custom dimensions
**Branch**: `main`

### Summary

Added required-nullable AI-selected dimensions, native aspect-ratio selection, exact output normalization, tests, and service verification.

### Git Commits

| Hash | Message |
|------|---------|
| `950a41f` | feat: support custom GPT image dimensions |

### Status

[OK] **Completed**


## Session 2: Textual bot mention trigger
<!-- trellis-session: v=2 fp=35fb0281b7a8ea8b -->

**Date**: 2026-09-17
**Task**: Textual bot mention trigger
**Branch**: `main`

### Summary

Added runtime-name textual @ mention dispatch and normalization while preserving raw persistence, native mention handling, and raw/SDK dedup.

### Git Commits

| Hash | Message |
|------|---------|
| `eab1da9` | feat: support textual bot mentions |

### Status

[OK] **Completed**


## Session 3: NovelAI quick image generation
<!-- trellis-session: v=2 fp=0c6566e8eff66fa2 -->

**Date**: 2026-09-22
**Task**: NovelAI quick image generation
**Branch**: `main`

### Summary

Added the NovelAI快速生图 panel command, shared no-session prompt authoring, fixed first-option generation through the existing style/quota/notice workflow, focused regressions, README/spec updates, and committed the accumulated NovelAI/QQ workflow changes. Full 368-test unit suite passed; no deployment or live generation was performed.

### Git Commits

| Hash | Message |
|------|---------|
| `02dc66d` | feat: expand NovelAI and QQ image workflows |
| `697215c` | chore: record active task artifacts |

### Status

[OK] **Completed**
