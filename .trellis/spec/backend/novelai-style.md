# NovelAI and Nai v5 User Style Contract

## Scope / Trigger

Read when changing either the NovelAI or Nai v5 style command, sender identity
routing, provider-specific state storage, prompt composition, or the QQ feature
panel. Runtime code is under `src/aiqq`; legacy root modules are not part of this
feature.

## Signatures

```python
class NovelAIStyleStore(Protocol):
    async def load(self, member_openid: str) -> str | None: ...
    async def save(self, member_openid: str, prompt: str) -> None: ...

NovelAIStyleRepository(connection, provider="novelai")  # novelai | nai_v5
NovelAIStyleWorkflow(styles=store, provider_name="NovelAI",
                     command_name="NovelAI画风", provider_id="novelai")
async def run(self, prompt: str, *, member_openid: str) -> ConversationResult: ...

NovelAIImageWorkflow(prompt_auditor=auditor, image_service=service,
                     image_usage=usage, styles=store)
async def run(self, prompt: str, *, conversation_key: str,
              member_openid: str) -> ConversationResult: ...
```

`NovelAIStyleRepository` selects one of two fixed tables in the existing
`AIQQ_STATE_DB`: `novelai_user_styles` for the default `novelai` provider and
`nai_v5_user_styles` for `nai_v5`. Each has `(member_openid TEXT PRIMARY KEY,
prompt TEXT NOT NULL)`. Reject unknown provider selectors before any SQL; table
identifiers come only from this fixed mapping, while values remain bound parameters.
No new environment keys. Nonempty saves use a transactional UPSERT; empty saves
DELETE the row. Bootstrap creates and initializes both repositories, sharing each
only between the corresponding setting and image workflows. Existing NovelAI
rows stay in their original table. Do not migrate, copy or fall back to them for
Nai v5; its new table starts empty.

## Contracts

- `NovelAI画风 <English style>` or `/NovelAI画风 <English style>` replaces and enables
  the sender's style. A bare or whitespace-only argument clears it idempotently.
- `Nai v5 画风 <English style>` or `/Nai v5 画风 <English style>` does the same for
  Nai v5 only. Parsing maps it to `nai_v5_style`; the router passes the actual
  sender to its separate workflow, never to the original NovelAI workflow.
- Each provider reads, replaces and clears only its own style. A legacy-only
  saved style must not affect Nai v5. A missing Nai v5 style workflow returns
  an explicit unavailable result without falling back to the legacy workflow.
- Owner is the incoming QQ `member_openid`, never command content or a parsed
  conversation key. The same ID uses the same setting across groups. Quota keys
  remain `group:<group>:member:<member>`.
- Settings have no TTL, survive restart, trim outer whitespace, and preserve
  internal newlines and weighted tags. No image call or quota consumption for settings.
- Style alone is not a scene: do not run the scene-effectiveness auditor when
  saving. Each image workflow rejects an empty image prompt and reads its own
  style once. Original NovelAI normalizes fullwidth ASCII and common CJK punctuation
  in the scene prompt; Nai v5 only trims its scene prompt's outer whitespace and
  preserves Chinese content and punctuation. Both prepend `style + ", " + prompt`
  when a style is set, audit the combined prompt for nonexempt members, then send that same composed
  prompt to their generator. The audit uses `require_english=True` for original
  NovelAI and `require_english=False` for Nai v5.
- Explicit drawing members in `AIQQ_IMAGE_AUDIT_WHITELIST` skip the combined
  prompt audit and any legacy Chinese/quick pre-translation audit, using their
  actual QQ identity. Keep style reading/composition and local nonempty/language
  checks. Their scene determines layout through the local rule documented in
  [Drawing Audit Exemptions](./image-services.md#drawing-audit-exemptions).
- The existing service still prepends `rating:general, safe, sfw`. Model, image
  dimensions, negative prompt, quota, and failure cleanup behavior remain unchanged.
- Prompt-option buttons carry raw scene prompts. Read the current style when
  generating, so previously generated options do not accumulate or retain stale styles.
- Successful NovelAI image results retain the composed prompt submitted to the
  generator. For a Chinese `NovelAI生图` request, the QQ completion keyboard links
  to an expiring temporary reply page with that prompt; failed requests and
  direct English generation do not expose this button. The image message stays
  image-only, and temporary storage failure must not block image delivery.
- Both generation and revision instructions restrict prompt options to scene content
  (subject, appearance, clothing, action, composition, setting and lighting). Exclude
  quality labels such as `best quality`/`masterpiece`, rendering styles/media and
  artist/style-imitation tags even if present in input or existing options. Revisions
  remove inherited unwanted tags; actual depicted art objects remain scene content.
  These are model instructions, not an exhaustive deterministic tag filter. Personal
  style settings and direct English generation prompts retain their existing
  behavior; original NovelAI Chinese QQ image commands route through quick
  authoring first. Nai v5 Chinese commands use its direct image workflow without
  quick authoring, tag conversion or translation.
  Prompt authoring now requires Danbooru lookup via `suggest_tags`, as described in
  [NovelAI Tag Lookup](./novelai-tags.md); general web search remains disabled.
- Both QQ panel and feature menu expose each provider's style command. Menu
  keyboard actions use `enter=False`; selecting one must not automatically
  submit a clear operation.
- Keep QQ panel descriptions within 20 characters. The 22/25-character style
  descriptions triggered HTTP 400, code 30013 / err_code 40030013 (超出数量限制).
  Reducing the item count to five did not fix it; shortening the descriptions
  allowed all six commands, including 菜单, to synchronize successfully. Do not
  infer an item-count cap from this generic error. The feature keyboard has five rows.

## Validation & Error Matrix

| Condition | Result |
| --- | --- |
| Nonempty ASCII style | Persist replacement, then acknowledge enabled |
| Empty/whitespace style | Delete, then acknowledge cleared |
| Non-ASCII style | Reject with English-prompt requirement; keep previous style |
| Save/delete error | `unavailable`, `<provider>_style_unavailable`, provider-labelled save/clear failure and command |
| Read error before generation | Same error code; explicit read failure; no audit, quota or generation |
| No style for the requested provider | Generate with its scene prompt only; never use the other provider's style |
| Unknown repository provider | Raise `ValueError`; do not access an arbitrary table |
| Empty image argument with saved style | Existing image usage message; no style-only generation |
| Combined prompt rejected/unavailable | Existing audit response; no quota or generation |
| Cancellation | Propagate cancellation; never convert it to a save/generation success |

Storage logs include only fixed event and exception type. Do not log prompt text,
member/group IDs, raw exceptions or credentials. Full text and summary both retain
the specific user-visible error cause.

## Good / Base / Bad Cases

- Good: save `watercolor`, replace with `oil painting`, generate `white cat` →
  audit/generate `oil painting, white cat` once.
- Good: the same member has NovelAI `watercolor` and Nai v5 `pixel art`; each
  generator gets only its own prefix. Clearing either setting leaves the other.
- Base: user without a saved style generates the original prompt unchanged.
- Bad: using the original NovelAI repository as a fallback for unset Nai v5
  preferences, or copying old styles when creating the new table.
- Bad: storage is unavailable → explicitly stop; do not silently omit the style.

## Required Tests

- SQLite replacement, idempotent clear, sender isolation, weighted/multiline values,
  reopen persistence, initialization on an existing state database.
- Initialize the Nai v5 table with existing legacy styles: legacy data is unchanged
  and Nai v5 is unset. Independent replace/clear and restart preserve both stores.
- Save/clear failures and invalid style cannot report success; logs are sanitized.
- Image audit sees the same composed text as generation; audit rejection and read
  failure consume no quota; empty scene does not generate a saved style alone.
- Router passes actual sender separately from quota key; manual prompts and old
  option buttons use the current style; GPT signature remains unchanged.
- Bootstrap shares a repository only within a provider and uses distinct repositories
  across providers, initializes both and preserves state after rebuild. Run both
  commands through the real router/storage/image workflow and assert the actual
  audited/generated prompts, including Chinese Nai v5 scenes.
- Panel synchronization is idempotent and retains unrelated entries in shared panels.
- Public panel retains both providers' style commands and existing menu command;
  assert descriptions stay within 20 characters to prevent provider rejection.

## Wrong vs Correct

Wrong: attach a style to each generated option, then attach it again when generating.
This duplicates style text and makes cached options retain old preferences.

Correct: keep option content unchanged; compose exactly once in
`NovelAIImageWorkflow.run` before auditing the final prompt.

Wrong: inject `novelai_styles` into both providers or clear both settings from one
command. Correct: inject `novelai_styles` into original NovelAI and `nai_v5_styles`
into Nai v5, with one command and table per provider.
