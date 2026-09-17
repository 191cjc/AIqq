# NovelAI restoration — 2026-09-16

## Authorization and latest scope

User now explicitly requests restoring the diagnosed broken NovelAI function. Restore configuration and deploy/restart after checks; this supersedes the previous diagnosis/code-only hold for this restoration. Restart will also load the already-reviewed R8 reference-image wording (203 tests previously passed); retain those changes. Do not replay historical QQ requests, send test QQ messages, or change model, prompts, quota, retries, timeouts or unrelated legacy files.

## Concrete implementation

1. Parent restores only these .env settings, keeping the existing token in its 0600 file: NOVELAI_MCP_URL=https://www.fsstudy.com.cn/mcp and NOVELAI_MCP_TOKEN_FILE=/home/ubuntu/.config/novelai-mcp/token. Keep packaged config explicit rather than embedding a personal service address/token-file default in public source. Back up exact pre-change files and .env privately.
2. Implementation worker adds typed classification for NovelAI not_configured / not_ready using a shared exception contract (existing ImageGenerationUnavailable may be reused; keep service-specific types out of logic). Preserve NovelAIError compatibility and generic fallback for other existing failures. User copy: `NovelAI 生图服务未配置，请联系管理员。` and `NovelAI 生图服务连接未就绪，请稍后再试。`; full_text and summary identical. Safe fixed diagnostics for initialization/configuration; never log credentials, URL, prompt or raw exception message. Keep cancellation and quota release/no-success accounting.
3. Add a read-only NovelAI configured/ready snapshot to health via bootstrap callbacks. Preserve current overall QQ/database HTTP status semantics; do not do network probes or generation from health. A configured client must advertise generate_image before claiming ready; steps remains an optional capability. No automatic generation retries or new network recovery loop.
4. Add a bootstrap regression using a temporary 0600 credential file plus explicit MCP URL, mocked tools/list/transport, and assert the NovelAI workflow is wired to a configured/ready service. Also cover absent config, failed/missing-tool discovery, safe user errors, success accounting/cancellation preservation, and health snapshot freshness/no secrets. Keep tests focused; no new framework or broad transport error taxonomy.

## Ownership

Worker owns src/aiqq/exceptions.py, services/images/novelai.py, logic/novelai.py, interfaces/web/health.py, bootstrap.py; tests/unit/services/test_novelai_image.py, test_health_web_app.py, unit/logic/test_novelai.py, unit/test_bootstrap.py. Preserve prior modifications to exceptions.py and unrelated files. Parent owns .env, README/spec/task records, validation artifact, restart and live verification. Do not edit deployment/config or invoke real services in worker/reviewer tests.

## Validation and rollout

Worker runs targeted tests; independent check reviews and runs the packaged unit suite and diff checks. Parent validates effective settings without printing secrets, checks no active workflow before restart, and verifies local/public health plus QQ connection and NovelAI ready. Perform at most one real safe 1024x1024, 28-step NovelAI generation through the production adapter, save and decode the result, and check shared QQ preparation. No extra variants or automatic generation retry. If a timeout/ambiguous failure occurs, stop and inspect available outputs before considering another generation. This verifies the actual adapter/image result without sending a manual QQ message. Keep precise restoration notes and rollback limited to this change.

## Verified result and deployment

- Restored the two explicit NovelAI settings using the existing private credential file; token values were not copied into source or logs.
- Independent review passed. 22 targeted tests and 212 full packaged unit tests passed (8.659 seconds); eight relevant source/test files passed AST parsing, and `git diff --check` passed. No standalone lint/type-check is configured.
- A single real production-adapter generation succeeded from 2026-09-16 10:50:52 to 10:51:09 +08:00: valid 1024×1024 PNG, 344313 bytes. The shared QQ image preparation preserved it below the 2 MiB limit. Decoded and visually checked the result. No additional generation or manual QQ message was sent; group-visible delivery awaits a user-initiated request.
- Confirmed no child workflows before restart. Restarted `aiqq.service` at **2026-09-16 10:54:11 +08:00**, new PID **1185136**, active/running, NRestarts=0.
- Deployed startup logged `novelai_connection_ready configured=true ready=true supports_steps=True` at 10:54:14.273. Both local and public health endpoints returned HTTP 200 with QQ connected, database open, and NovelAI configured/ready true.
- This restart also activates the previously reviewed R8 reference-image error wording. Earlier foreground 600-second timeout, 10000-character history, GPT image setup, QQ compression, and expired-reply fallback remain in place.
- Exact pre-change backups, the restoration-only patch, live-check artifacts, and `deployment-result.json` are in `/var/lib/aiqq/backups/novelai-restore-20260916T104204/`. Roll back only the backed-up restoration files/configuration, preserving earlier work and unrelated dirty files.
- No new commit was created. The broader image-services task remains in progress for user-initiated QQ delivery acceptance.
