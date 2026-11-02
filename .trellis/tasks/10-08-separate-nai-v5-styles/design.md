# Design

## Storage and compatibility

Extend NovelAIStyleRepository with a fixed, validated provider selector (default
novelai). The original provider keeps novelai_user_styles unchanged; Nai v5 uses
nai_v5_user_styles with the same member_openid primary key and prompt column.
Only allow the two fixed table names. CREATE TABLE IF NOT EXISTS is idempotent;
no legacy rows are migrated, copied or deleted. This avoids rebuilding the live
legacy table while sharing the existing transaction and persistence behavior.

## Command and generation flow

Nai v5 画风 -> nai_v5_style command -> provider-labelled NovelAIStyleWorkflow ->
Nai v5 repository. Nai v5 作图 -> existing direct image workflow -> same Nai v5
repository -> combined prompt audit/generation. Original NovelAI commands and
option buttons continue to use the original repository.

Parameterize the existing style workflow's provider name/id and command name;
keep English style validation and weighted/multiline preservation. Match image
style-load errors to the image workflow's provider. Bootstrap creates and
initializes two repositories and injects each into only its provider workflows.

## Presentation and errors

Expose both style commands in the panel and menu; use enter=False for each menu
button. Save/clear/read failures use fixed provider-specific messages and codes,
with safe error logging. No style setting performs image admission/generation.

Live QQ verification rejected the 22/25-character style descriptions with HTTP
400 / code 30013 / err_code 40030013 (超出数量限制). A five-item panel was also
rejected. Shortening descriptions to at most 20 characters allowed all six
commands to synchronize successfully. Retain the existing menu panel command;
do not treat this generic error as an item-count limit.

## Deployment

Run focused and full unit tests before restarting aiqq.service. Startup creates
the new empty table and synchronizes the QQ panel. Check service process state
and /health. Existing configuration and secrets do not need modification.
