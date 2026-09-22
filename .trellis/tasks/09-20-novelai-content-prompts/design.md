# Design and change boundary

Add mandatory retrieval inside NovelAIPromptAgent before final authoring. A small structured planner model call converts the same create/revise payload into 1–8 distinct English queries. These are searched via a logic-owned NovelAITagSearcher port. Final existing generation/revision call receives the original payload plus tag_reference entries with query and typed {tag, category, post_count} candidates; returned data cannot override instructions. Quality/style rules already edited in this task remain shared across both operations.

## Contracts
- logic.models.DanbooruTag dataclass: tag:str, category:str, post_count:int; adapter restricts fields to validated safe values.
- logic.ports.NovelAITagSearcher.search(query:str, *, limit:int=8)->tuple[DanbooruTag,...].
- exceptions.NovelAITagSearchUnavailable extends NovelAIPromptUnavailable. Workflow maps it before generic model failure to fixed Danbooru 标签查询失败，请稍后重试。 and novelai_tag_search_unavailable.
- services/novelai_tags.py NovelAITagSearchService(client:NovelAIMCPTransport|None) shares existing transport; no independent initializer/close; 3 concurrent searches, 10-second per-call timeout (including semaphore wait if appropriate with 30-second whole batch).
- Agent uses asyncio.timeout(30) for the batch and cancels/awaits unfinished tasks on any error/cancellation. No background leaked lookups or final ungrounded authoring.
- Tool request suggest_tags {query,limit}; prefer structuredContent.result, otherwise decode text content JSON object(s)/array/result wrapper. Valid empty array is allowed. Reject malformed/error envelopes; filter artist/meta, allow general/character/copyright; validate ASCII printable non-comma tag names <=120 chars, integer nonnegative post_count, bounded record count. Deduplicate and order candidates by post_count, retaining query association. No descriptions/URLs from upstream in prompts or logs.
- Schema module owns planner parsing (1–8 short English queries, bounded length, normalized spaces to underscores, no control/comma/multi-query syntax). Invalid plan becomes existing prompt-model failure.

## Ownership
Parent: logic.models/ports, exceptions, workflow error mapping+tests, bootstrap+tests, docs/spec/task, deployment.
Implement agent: services/agents/novelai_prompt.py, services/agents/schemas.py, agent/schema tests and new dedicated prompt-agent tests.
Adapter agent: services/novelai_tags.py and new adapter tests only.

## Scope
Changes affect only NovelAI prompt authoring; manual generation and GPT APIs are unchanged. Preserve all existing uncommitted work. Do not add generic web browsing, persistent tag caches, configuration knobs, paid generation retries, or broad tag blacklists. Quality/style semantic exclusion remains a model rule; artist/meta search category exclusion is enforced in code.

## Rollout
Before restart, source snapshot preserves all pre-existing uncommitted feature work. No DB migrations. Restart current service after focused tests and independent full-scope check. Verify gateway/database/NovelAI health, read-only tag lookup and sanitized startup events. Rollback restores only this task's changed source and removes new adapter.
