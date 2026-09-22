# Danbooru-grounded NovelAI scene prompts

## Requirements
User requires generated prompt options to exclude best quality and similar quality, art-style, or artist tags. User then explicitly requested Danbooru tag lookup to improve prompt quality. Apply both requirements to initial generation and revisions.

The flow must look up real Danbooru tag candidates before writing the three final options, preserving requested subjects/actions/composition. Rank relevant candidates using category and popularity; do not replace a requested concept merely with a more popular unrelated result. Exclude artist/meta categories from retrieved candidates and instruct the model to exclude quality, rendering style/media, and artist/imitation labels even when user descriptions or old options contain them. Preserve actual depicted art objects.

Queries cover independent English scene concepts derived from the description or old options plus the latest modification. Limit query work and delays. An actual service failure must produce an explicit Danbooru lookup error, not silently skip searching. A successful lookup with no matches permits concise English scene descriptions without pretending they are verified tags.

Preserve personal NovelAI画风, manually entered generation prompts, model, safety review, 3 options, 500-char limit, prompt session scope/TTL and image quota. No image generation or quota use during prompt authoring. Do not enable general web search or arbitrary tools.

## Acceptance
- Real suggest_tags call occurs before final authoring on both paths; lookup candidates and counts reach the author model with untrusted-data instructions.
- Response parser validates structuredContent and observed text JSON fallback, empty results, category filtering, deduplication, malformed/remote errors.
- Lookup is bounded and cancellation propagates; lookup failure prevents final authoring/session success and yields a <=50-char specific user summary.
- Revision re-queries updated concepts, no stale search results shared among users.
- Bootstrap injects shared configured client; no config duplication or hard-coded endpoints/credentials.
- Core agent/schema, adapter, workflow, bootstrap tests and full unit suite pass. Verify real read-only lookup; no paid image generation or manual QQ messages.
