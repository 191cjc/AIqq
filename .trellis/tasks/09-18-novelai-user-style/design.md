# Design

## Boundary
The missing behavior spans QQ command entry, a durable user preference, and final NovelAI generation. Implement it where the prompt is audited, not in the prompt option generator or remote MCP adapter. No image provider/model changes, new paid calls, chat-history changes, or unrelated refactors.

## Contracts and flow
Introduce logic port NovelAIStyleStore with async load(member_openid) -> str | None and save(member_openid, prompt) -> None. Empty save deletes the row. Database NovelAIStyleRepository uses the existing shared state SQLite connection, CREATE TABLE IF NOT EXISTS, primary key member_openid, and transactional UPSERT/DELETE. No expiry. Store trimmed outer whitespace and preserve inner text.

NovelAIStyleWorkflow(styles=store).run(prompt, *, member_openid) -> ConversationResult validates nonempty English/ASCII style and persists save/clear. Logs only event and exception class. No audit on style alone: valid style tags need not describe a complete scene. Combined generation still receives the existing full audit.

NovelAIImageWorkflow requires styles=store and run(prompt, *, conversation_key, member_openid). Reject a missing image prompt before style lookup. Load once and combine as f"{style}, {normalized}" if a saved style exists, then audit and generate that exact prompt. A failed style load returns unavailable before consuming quota. Sender identity is explicit; never parse quota keys or accept identity from user content.

Router defines separate protocols for NovelAI image and style workflows, passes message.member_openid, and keeps GPT ImageWorkflow unchanged. Bootstrap injects one repository into both workflows and registers initialization on the existing state connection. Panel + menu command input is populated without auto-send. Generated prompt option buttons retain unstyled content to avoid duplication and reflect latest saved settings at generation time.

## Files and ownership
Core implement agent: src/aiqq/database/novelai_styles.py, database/__init__.py, logic/ports.py, logic/novelai.py, logic/novelai_style.py; focused repository/workflow tests.
Parent: interfaces/qq/commands.py, command_router.py, ui.py; services/qq/panel.py; bootstrap.py; corresponding integration/UI/router/bootstrap tests; README and new backend style spec.
Independent check agent reviews all feature files and may fix within scope.

## Rollout
Run focused and full local tests without remote generation. Back up existing source and state DB before restart. Restart aiqq.service to initialize the additive table and synchronize the panel; inspect startup health without manually sending messages. Source rollback leaves the unused additive table safe; never reset unrelated dirty changes.
