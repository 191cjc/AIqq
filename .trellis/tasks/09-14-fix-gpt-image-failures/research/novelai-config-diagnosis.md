# NovelAI unavailable after migration — 2026-09-16

## Scope

User requested diagnosis. No source/config changes, restart/reload, real image generation, or QQ messages were performed. The service remains PID 938845, started 2026-09-15 17:42:58 CST. Earlier reference-image wording changes are still undeployed.

## Confirmed failure

- Group record 641: `/NovelAI生图 ...`, 2026-09-15 20:39:44.
- `/var/lib/aiqq/aiqq.log.2026-09-15:585`: 20:40:06, `novelai_generation_failed error_type=NovelAIError`.
- Bot record 642: 20:40:07, NovelAI 图片生成暂时不可用，请稍后再试。
- Record 640 at 20:38:52 successfully returned NovelAI prompt text through ordinary chat. It was not an invocation of the dedicated NovelAI prompt command.

## Root cause

The root-level legacy adapter defaulted to `https://www.fsstudy.com.cn/mcp` and loaded the token from `~/.config/novelai-mcp/token` when no explicit settings were supplied (`novelai_service.py:16,198,252`).

Packaged configuration now defaults NOVELAI_MCP_URL to an empty string and NOVELAI_MCP_TOKEN_FILE to None (`src/aiqq/config.py:139,226`). Bootstrap only creates the MCP client when both URL and loaded token are available (`src/aiqq/bootstrap.py:92`). The running process's launch environment and project .env contain no nonempty NovelAI settings. The former credential file still exists.

Reconstructing effective config from the process launch environment plus .env confirms URL, inline token, and token-file setting are absent. An offline `NovelAIService(None)` reproduces `is_configured=False`, `is_ready=False` and `NovelAI service is not configured` before any generation request. The workflow catches all generation errors and displays one generic message (`src/aiqq/logic/novelai.py:80`), hiding this configuration diagnosis.

Initialization with client=None silently returns False rather than warning; the general health endpoint does not report NovelAI readiness. Existing adapter tests inject a FakeMCPClient, while the bootstrap test constructs an application without NovelAI settings and tests shared quota wiring. Neither protects the previous deployment's default configuration behavior.

## Read-only remote verification

Used the installed novelai-image skill client and existing private token file; initialize and tools/list both succeeded. Tools advertised: generate_image, img2img, suggest_tags, check_subscription. generate_image requires prompt, supports steps and cfg_rescale, and advertises default model v4.5-full. No credentials or session IDs were printed or copied.

This establishes current MCP connectivity/authentication and tool discovery, not actual image generation/account quota. No billable image call was made. The observed local failure occurs before the MCP generation stage.

## Concrete repair proposal, not applied

Restore explicit configuration using the existing private credential file:

```dotenv
NOVELAI_MCP_URL=https://www.fsstudy.com.cn/mcp
NOVELAI_MCP_TOKEN_FILE=/home/ubuntu/.config/novelai-mcp/token
```

Also distinguish unconfigured service, unavailable MCP, timeout and invalid image outcomes in safe user copy/logging; expose NovelAI readiness and cover actual bootstrap configuration in a regression. No change to model, prompt rules or image retries is necessary for the diagnosed configuration omission. Deployment/restart and a live generation check remain separate work and were not performed during this diagnosis.
