# Design

The gap lives in query normalization at two service boundaries: the Pydantic
planner validator and the MCP tag adapter. Extract their stable shared rule into
`src/aiqq/services/novelai_tag_query.py` and import it from both. Preserve ASCII,
raw 1–80 character limits, at least one letter, alphanumeric first character,
existing punctuation, lowercase and space-to-underscore normalization; add `!`
after the first character. Keep commas, colons, wildcards, control characters,
query operators and non-ASCII rejected. Do not silently remove punctuation or
skip an invalid planned concept. The planner instructions should explicitly
allow exclamation marks with a legitimate title example.

Add a typed invalid-output error under `NovelAIPromptUnavailable`, carrying a
bounded planning/authoring stage. Agent parsing and the existing final ASCII
check map to this error and log only fixed stage/kind plus exception type.
Model transport failures keep existing unavailable semantics but also log the
stage. The workflow catches the invalid-output subtype before the base type and
maps it to safe actionable text and a stable error code on create and revise.
Actual tag lookup failure retains its existing distinct message/code.

Expected source scope: shared query module, `services/agents/schemas.py`,
`services/novelai_tags.py`, `services/agents/novelai_prompt.py`, `exceptions.py`,
`logic/novelai.py`; related schema/adapter/agent/workflow tests. Documentation:
existing `novelai-tags.md` and this task's artifacts only.

No model/provider/config, image generation, QQ routing, storage, retry policy,
or quality/style filtering changes. No broadened arbitrary punctuation rules.
Existing pre-turn dirty files are preserved. Restart only after verification;
deployment uses the existing service and environment.
