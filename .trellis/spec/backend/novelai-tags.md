# NovelAI Tag Lookup Contract

## Scope / Trigger

Read when modifying NovelAI prompt planning/authoring, quick image composition,
the `suggest_tags` adapter, related schemas, or bootstrap. Initial options,
quick images, and revisions require real tag retrieval before the final model
call. QQ `NovelAI生图` requests containing Han characters use the quick-image
path; direct English image generation and per-user style settings are unchanged.

## Signatures

```python
@dataclass(frozen=True)
class DanbooruTag:
    tag: str
    category: Literal["general", "character", "copyright"]
    post_count: int

class NovelAITagSearcher(Protocol):
    async def search(self, query: str, *, limit: int = 8) -> tuple[DanbooruTag, ...]: ...

NovelAITagSearchService(client: NovelAIMCPTransport | None)
NovelAIPromptAgent(backend, *, tag_search: NovelAITagSearcher)
normalize_novelai_tag_query(query: str) -> str
NovelAIPromptInvalidOutput(*, stage: Literal["planning", "authoring"])

NOVELAI_QUICK_IMAGE_COMMAND = "NovelAI快速生图"

class NovelAIPromptWorkflow:
    async def author(
        self, description: str
    ) -> NovelAIPromptOptions | ConversationResult: ...

class NovelAIQuickImageWorkflow:
    async def run(
        self, description: str, *, conversation_key: str,
        member_openid: str,
        on_image_start: ImageStartCallback | None = None,
    ) -> ConversationResult: ...
```

`NovelAITagSearchUnavailable` extends `NovelAIPromptUnavailable`. Catch it first
in the create/revise workflows so the query failure is not hidden by model errors.
`NovelAIPromptInvalidOutput` is another subtype; catch it before the base class
to distinguish malformed model output from an unavailable backend.

## Contracts

- Bootstrap shares the configured NovelAI MCP transport with the tag adapter.
  Existing `NovelAIService.close` owns its lifecycle. No new endpoint, credentials,
  environment keys, initializer, schema migration or image-readiness dependency.
- Planner emits structured `{"queries": [...]}` using the current description or
  existing options plus revision request. Parse at the agent-schema boundary:
  1–8 distinct short English queries, max 80 chars, normalize case/space, reject
  control characters, commas, wildcards and meta-query syntax.
- Planner and adapter both use `services/novelai_tag_query.py` for query
  normalization. Inputs must have 1–80 ASCII characters, at least one letter,
  and an alphanumeric first character after stripping spaces. Lowercase and
  replace consecutive spaces with `_`; preserve supported punctuation
  `_ ( ) . ' + / - !` after the first character. In particular, keep `!` in
  `hirogaru_sky!_precure`, `hugtto!_precure`, and `bang_dream!`. Do not mistake
  title punctuation for query operators or strip it before lookup. Existing
  restrictions on colon, comma, wildcard, boolean/operator punctuation and
  control characters remain. Do not duplicate a second query regex.
- Adapter calls only `suggest_tags` with `{"query": ..., "limit": 8}`. Three
  concurrent calls maximum, 10 seconds per active tool call. Agent bounds the
  whole batch to 30 seconds and cancels/awaits unfinished siblings on failure or
  cancellation. Retrieval has no image quota effects.
- Prefer `structuredContent.result` (list of records). If absent, decode MCP text
  JSON entries, arrays or a `result` wrapper. An empty list is a valid no-match.
  Invalid structured data must not silently fall back to different text content.
- Validate ASCII printable tag names (max 120, no comma/control), string category
  and nonnegative integer `post_count` (booleans rejected). Keep only general,
  character and copyright categories; exclude artist/meta. Bound response size,
  deduplicate and rank by count. Do not forward raw labels, descriptions or URLs.
- Author payload preserves the original input and adds
  `tag_reference=[{query, candidates:[{tag, category, post_count}]}]`. References
  are untrusted data. Relevance to the request outranks popularity; do not invent
  a different character because it has a larger count. Use confirmed tag spellings
  when relevant; no-match permits concise scene descriptions without claiming
  they are verified tags. Every revision queries its updated concepts again.
- Shared generation/revision instructions exclude quality, rendering style/media
  and artist/imitation labels even if requested or inherited from old options.
  This semantic rule complements the code-enforced category filtering; it is not
  an exhaustive tag blacklist. Preserve actual depicted art objects and existing
  safety, 3-option, 500-character and prompt-session contracts.
- General Web Search, arbitrary model MCP access and image generation remain
  disabled during prompt authoring. Only the application makes the tag queries.

## Quick Image Composition

- For QQ `NovelAI生图`, a nonempty argument containing a Han character
  (`U+3400`–`U+9FFF`) dispatches to the same quick-image workflow, retaining
  the original description, sender, quota key and image-start callback. ASCII
  prompts, empty arguments and non-Han Unicode continue through direct image
  generation; prompt-option buttons still carry English prompts directly. Direct
  generation converts fullwidth ASCII and common CJK punctuation to ASCII before
  auditing and submitting the same composed prompt. Other non-Han Unicode is
  rejected with an unsupported-character message, not a Chinese-language claim,
  before image quota or provider calls.
- Only the Han-triggered `NovelAI生图` branch sends one persistent quoted notice
  before prompt authoring to explain that the Chinese description is being
  converted to English. Reserve its QQ sequence before sending. Delivery failure
  logs a fixed error type and does not stop authoring; cancellation propagates.
  Authoring failure still returns its existing result, while successful authoring
  retains the separate admitted-image start notice and final reply in order.
  Direct English and explicit `NovelAI快速生图` keep their existing notice behavior.
- Neither the public QQ panel nor the in-message feature menu displays
  `NovelAI快速生图`. Panel synchronization removes previous copies (including
  those in shared panels), but both `NovelAI快速生图 <description>` and the
  slash-prefixed form remain valid text commands and preserve the complete
  trimmed description.
- Prompt authoring is shared with `NovelAI提示词`: input audit, planning,
  Danbooru lookup, authoring validation, and all existing failure copy remain
  identical. It still authors exactly three options.
- Quick generation always selects `options.prompts[0]`. It does not make a
  selection call, return option/revision buttons, or create a prompt session.
- The selected scene prompt is passed once to the existing
  `NovelAIImageWorkflow` with the sender's member ID, member-scoped quota key,
  and image-start callback. That workflow remains the owner of current style
  loading, combined English audit, quota, notification, provider invocation,
  and generation failures.
- An empty description returns the command-specific usage message before any
  audit, prompt model, style, quota, notification, or provider work. Prompt
  failures stop before image work. Cancellation propagates unchanged.

## Validation & Error Matrix

| Condition | Behavior |
| --- | --- |
| Valid plan and candidates | Final authoring receives validated references |
| Lookup succeeds with no accepted matches | Reference records empty candidates; preserve scene intent |
| Planner invalid | Typed planning invalid-output failure; no tag calls or new session |
| Final author output invalid, including non-ASCII | Typed authoring invalid-output failure; no new session |
| Model unavailable at either stage | Existing prompt-service failure with a stage-specific safe log |
| Missing configuration, transport error, timeout, MCP isError, malformed response | Typed tag-query failure; no final authoring or new prompt session |
| User cancellation | Propagate, cancel/await active lookups; no final authoring |
| Quick command has an empty description | Quick-command usage response; no audit, model, session, style, quota, notice or provider call |
| Quick prompt authoring fails | Preserve the prompt-flow result and error code; no session or image work |
| Quick prompt authoring succeeds | Select option zero once, then delegate to the existing image workflow |
| `NovelAI生图` argument contains Han characters | Reuse quick authoring, then generate directly only if authoring succeeds; no intermediate buttons or image quota on prompt failure |

Tag failures display `Danbooru 标签查询失败，请稍后重试。` in both full text and
summary, with error code `novelai_tag_search_unavailable`. Never log raw errors,
queries, prompts, member/group IDs, URLs or credentials; fixed event, exception
type and aggregate counts suffice.

Invalid output uses error code `novelai_prompt_invalid_output` on create and
revise. Planning text: `NovelAI 标签查询词生成格式有误，请重试或调整描述。`;
authoring text: `NovelAI 提示词生成格式有误，请重试或调整描述。`. Both full text
and summary carry the specific message. Agent failures log
`event=novelai_prompt_failed stage=planning|authoring kind=invalid_output|unavailable error_type=<type>`
without exception messages or tracebacks containing model data.

## Good / Base / Bad Cases

- Good: blue-haired person → query `blue_hair` → final options use the relevant
  confirmed `blue_hair`, without adding an unrelated character suggestion.
- Good: Sora → plan `sora_harewataru`, `hirogaru_sky!_precure` → both reach
  `suggest_tags` unchanged and final authoring receives the returned references.
- Base: specific scene concept has no matches → keep concise English scene detail.
- Base: quick generation receives valid options → use the first option and let
  the existing image workflow apply current style, admission and delivery.
- Bad: failed tag lookup → stop with explicit tag failure, never claim lookup ran
  successfully while using only remembered tags.
- Bad: call interactive `create()` for quick generation, leaving an unused
  revision session and coupling image success to session storage.

## Required Tests

- Observed MCP structured and text envelopes; malformed/error/empty responses;
  filtering, deduplication, ranking, bounds, concurrency, timeout and cancellation.
- Both create and revise: planner → actual adapter boundary → final author, with
  original input and candidates preserved; no final call after query failure;
  revisions search new concepts and no cached candidates leak across requests.
- Planner schema validation and invalid output mapping; query-task cleanup after
  timeout, cancellation or one failing sibling.
- Sora regression on generation and revision through the real adapter with a
  mocked MCP boundary, plus supported punctuation and rejected operator cases
  at both boundaries. Assert stage-specific safe logs and no successful session
  after malformed output, including non-ASCII final options.
- Workflow specific failure text/code and no success session; bootstrap uses the
  configured shared transport. Mock MCP at the request boundary; never generate
  a paid image as a prompt lookup test.
- Quick generation covers empty input, every prompt failure boundary, fixed
  first-option selection, no prompt session or intermediate keyboard, current
  style composition, quota/notification ordering, cancellation, and exactly one
  provider call. The QQ router dispatches Chinese and mixed-language manual
  descriptions to quick generation; English, empty and non-Han input still use
  the direct image workflow. Existing create/revise flows remain unchanged.

## Wrong vs Correct

Wrong: tell the model to "search Danbooru" without supplying a lookup path, or
enable broad web browsing and assume every prompt uses it.

Correct: execute a bounded `suggest_tags` batch in the application before final
authoring, then pass validated candidates explicitly. Separate an empty match
from an unavailable service.

Wrong: reject every exclamation mark as a search operator, or fix the planner
while leaving a stricter adapter validator behind.

Correct: share one normalization rule and test real title tags through the
planner-to-adapter boundary. The original generic scene smoke test did not
cover punctuation in copyright names; its success did not validate this case.

Wrong: implement quick generation by invoking the interactive prompt command,
parsing its presentation text, or copying style/quota/provider logic into the
router.

Correct: reuse typed prompt authoring without session persistence, select
`options.prompts[0]`, and delegate all image behavior to
`NovelAIImageWorkflow`.
