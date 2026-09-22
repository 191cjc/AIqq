# Verified Danbooru tag MCP interface

2026-09-20: read-only tools/list and suggest_tags on the application's configured NovelAI MCP endpoint succeeded. No image generation performed; no credentials recorded.

Tool: suggest_tags, args {query: string, limit: integer (default 20)}. Server advertises generate_image/img2img/suggest_tags/check_subscription.
Returns MCP result with structuredContent={result: [{tag, label, category, post_count}, ...]}, plus text content blocks containing individual JSON objects. isError=false on success. Output schema requires result array; valid empty result is an empty match, not a transport failure.

Observed blue_hair lookup limit=5: blue_hair general 1205780; blue_hairband general 32423; remaining matches character category. Category strings: general, character, copyright, artist, meta. Query concepts independently; relevance takes priority over post_count.

Use application-owned MCPClient and existing configured credentials, not the user's home skill defaults. Share the existing transport (closed by NovelAIService) without changing image generation readiness. Tag calls require no image generation or quota, and should not enable broad Codex web search/MCP access. Timeout/bad response/remote isError must become a typed tag-query failure. No silent ungrounded fallback on failure.
