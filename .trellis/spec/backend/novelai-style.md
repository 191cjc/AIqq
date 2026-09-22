# NovelAI User Style Contract

## Scope / Trigger

Read when changing the NovelAI style command, sender identity routing, shared state
storage, prompt composition, or the QQ feature panel. Runtime code is under
`src/aiqq`; legacy root modules are not part of this feature.

## Signatures

```python
class NovelAIStyleStore(Protocol):
    async def load(self, member_openid: str) -> str | None: ...
    async def save(self, member_openid: str, prompt: str) -> None: ...

NovelAIStyleWorkflow(styles=store)
async def run(self, prompt: str, *, member_openid: str) -> ConversationResult: ...

NovelAIImageWorkflow(prompt_auditor=auditor, image_service=service,
                     image_usage=usage, styles=store)
async def run(self, prompt: str, *, conversation_key: str,
              member_openid: str) -> ConversationResult: ...
```

`NovelAIStyleRepository` creates `novelai_user_styles(member_openid TEXT PRIMARY KEY,
prompt TEXT NOT NULL)` in the existing `AIQQ_STATE_DB`. No new environment keys.
Nonempty saves use a transactional UPSERT; empty saves DELETE the row. Bootstrap
initializes the repository and shares it between the setting and image workflows.

## Contracts

- `NovelAI画风 <English style>` or `/NovelAI画风 <English style>` replaces and enables
  the sender's style. A bare or whitespace-only argument clears it idempotently.
- Owner is the incoming QQ `member_openid`, never command content or a parsed
  conversation key. The same ID uses the same setting across groups. Quota keys
  remain `group:<group>:member:<member>`.
- Settings have no TTL, survive restart, trim outer whitespace, and preserve
  internal newlines and weighted tags. No image call or quota consumption for settings.
- Style alone is not a scene: do not run the scene-effectiveness auditor when
  saving. The image workflow rejects an empty image prompt, reads the style once,
  prepends `style + ", " + prompt`, audits the combined prompt with
  `require_english=True`, then sends that exact prompt to the generator.
- The existing service still prepends `rating:general, safe, sfw`. Model, image
  dimensions, negative prompt, quota, and failure cleanup behavior remain unchanged.
- Prompt-option buttons carry raw scene prompts. Read the current style when
  generating, so previously generated options do not accumulate or retain stale styles.
- Both generation and revision instructions restrict prompt options to scene content
  (subject, appearance, clothing, action, composition, setting and lighting). Exclude
  quality labels such as `best quality`/`masterpiece`, rendering styles/media and
  artist/style-imitation tags even if present in input or existing options. Revisions
  remove inherited unwanted tags; actual depicted art objects remain scene content.
  These are model instructions, not an exhaustive deterministic tag filter. Personal
  style settings and manual generation prompts retain their existing behavior.
  Prompt authoring now requires Danbooru lookup via `suggest_tags`, as described in
  [NovelAI Tag Lookup](./novelai-tags.md); general web search remains disabled.
- Both QQ panel and feature menu expose the command. Menu keyboard action uses
  `enter=False`; selecting it must not automatically submit a clear operation.

## Validation & Error Matrix

| Condition | Result |
| --- | --- |
| Nonempty ASCII style | Persist replacement, then acknowledge enabled |
| Empty/whitespace style | Delete, then acknowledge cleared |
| Non-ASCII style | Reject with English-prompt requirement; keep previous style |
| Save/delete error | `unavailable`, `novelai_style_unavailable`, explicit save/clear failure |
| Read error before generation | Same error code; explicit read failure; no audit, quota or generation |
| Empty image argument with saved style | Existing image usage message; no style-only generation |
| Combined prompt rejected/unavailable | Existing audit response; no quota or generation |
| Cancellation | Propagate cancellation; never convert it to a save/generation success |

Storage logs include only fixed event and exception type. Do not log prompt text,
member/group IDs, raw exceptions or credentials. Full text and summary both retain
the specific user-visible error cause.

## Good / Base / Bad Cases

- Good: save `watercolor`, replace with `oil painting`, generate `white cat` →
  audit/generate `oil painting, white cat` once.
- Base: user without a saved style generates the original prompt unchanged.
- Bad: storage is unavailable → explicitly stop; do not silently omit the style.

## Required Tests

- SQLite replacement, idempotent clear, sender isolation, weighted/multiline values,
  reopen persistence, initialization on an existing state database.
- Save/clear failures and invalid style cannot report success; logs are sanitized.
- Image audit sees the same composed text as generation; audit rejection and read
  failure consume no quota; empty scene does not generate a saved style alone.
- Router passes actual sender separately from quota key; manual prompts and old
  option buttons use the current style; GPT signature remains unchanged.
- Bootstrap shares the repository, initializes it, and preserves state after rebuild.
- Panel synchronization is idempotent and retains unrelated entries in shared panels.

## Wrong vs Correct

Wrong: attach a style to each generated option, then attach it again when generating.
This duplicates style text and makes cached options retain old preferences.

Correct: keep option content unchanged; compose exactly once in
`NovelAIImageWorkflow.run` before auditing the final prompt.
