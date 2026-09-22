# Per-user NovelAI style

## Requirements
Add a NovelAI画风 entry to the QQ function panel and feature menu. The sender may save a style prompt. A nonempty value replaces and enables the previous style; empty/whitespace input clears it. This behavior was explicitly confirmed by the user.

The style belongs to the QQ sender (member_openid), persists across restarts, and is prepended once to each later NovelAI generation prompt. Manual prompts and prompt-option buttons both use the current saved style. Other users must not be affected. A style alone must not start generation. Combined prompts retain existing English and safety checks.

## Acceptance
- Panel and menu expose the command and avoid auto-submitting a blank style on a menu click.
- New content replaces; empty input clears idempotently. Weighted tags and internal whitespace survive storage.
- The same member ID uses the same style across groups; quotas remain per group/member.
- Save/clear gives an explicit acknowledgement only after database success; storage failures produce an actionable failure without leaking IDs or prompts into logs.
- Style is read once, combined before audit, and only an approved combined prompt reaches generation.
- English-only validation matches existing NovelAI behavior; invalid style must not replace a valid one.
- Restart preserves settings; unrelated GPT behavior and existing NovelAI safety prefix/quota/session behavior stay intact.
- Automated tests cover persistence, isolation, replace/clear, storage failures, audit and router wiring, plus full unit regressions.
