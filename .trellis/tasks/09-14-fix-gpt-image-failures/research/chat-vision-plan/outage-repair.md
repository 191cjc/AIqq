# Chat outage after R11 deployment

User report: chat is unavailable following R11 deployment (2026-09-16).

## Change boundary

The gateway, durable storage, audit and QQ error delivery are working. The chat
CLI fails during in-process app-server initialization before any model request.
An offline synthetic Responses endpoint reproduces this under production systemd
restrictions; removing only `UMask=0077` restores both chat feature variants.

Repair the child launcher compatibility in `chat_isolation.py`, preserving the
parent service restrictions and enforced filesystem/network boundary. Extend the
native runtime regression in `test_chat_runtime.py` to exercise restrictive
inherited permissions. Add fixed, secret-free initialization failure diagnostics
in `codex_sdk.py` and a corresponding SDK test. Record service-mode verification
and the deployment correction here and in the chat-read spec. No database/schema,
message-history range, image-generation behavior or QQ delivery changes.

Pre-repair source snapshot:
`/var/lib/aiqq/backups/chat-outage-20260916T162521/`.

## Diagnosis so far

- Production failure: 16:14:19, CodexExecError, followed by the generic unavailable
  reply. Current message and 50 full historical records were assembled first.
- `codex --version` works within the same confinement; this is insufficient as a
  readiness check for actual model turns.
- Normal local synthetic execution succeeds. With all production properties,
  the exact safe synthetic error is `failed to initialize in-process app-server
  client: Operation not permitted (os error 1)`; zero upstream requests.
- Removing mount protection, kernel protection, or systemd seccomp-related
  property groups independently does not fix it. Removing only the umask does.
- The earlier native and live vision tests ran outside the systemd unit; health
  checked gateway/database availability but did not execute chat initialization.

## Root cause and repair

Synthetic errno substitution isolated the fatal call to `fchmod(fd, 0644)`.
CLI 0.152.1 attempts this permission repair when new files inherit `0077`;
the metadata syscall is deliberately denied because Landlock does not scope it.
The PATH-alias warning concerns `chmod` and is nonfatal on successful turns.

After fork and confinement, the child launcher sets its own umask to `0022`
immediately before exec. Parent/supervisor retain the original mask; work/runtime
turn directories remain `0700`. No syscall, filesystem, network or systemd rule
has been removed. The child can create its expected internal file modes without
requiring permission-changing syscalls. The fix targets installed CLI 0.152.1;
CLI upgrades require the same production-mode verification.

## Verification

- All 12 runtime tests pass both normally and under all production systemd
  properties (transient unit `aiqq-umask-regression-probe`). The native CLI test
  inherits `0077`, executes history/image scripts, transports two viewed images,
  preserves the full >30,000-character history, and cleans orphaned children.
- Regression proves parent `0077` / parent-created `0600` files, both turn
  directories `0700`, child `0022` / tool-created `0644` files. Host DB/config/proc,
  other turn and symlink reads are denied; chmod/fchmod remain denied, including
  fchmod on an external descriptor opened before confinement.
- All 265 packaged tests pass (15.399 seconds); compileall and diff checks pass.
  No separate lint or type checker is configured.
- Real gpt-6-astra ChatAgent ran under every production systemd property,
  including `UMask=0077`, with both production feature flags enabled. Two cards'
  pixel-only text was correctly recognized in 27.52 seconds, two sources read,
  no generated image, no QQ send, no production DB write, no leftover turn dirs.
  See `outage-live-result.json` and `outage-live-probe.py`.
- Pre-restart SQLite backup contains 816 rows. After restart, all 816 rows match
  every original column exactly; the current database has 817 rows.
- Independent review passed with no findings, including 25 independently run
  SDK/runtime tests; see `outage-review.md`.

## Corrective deployment

Restarted `aiqq.service` at 16:33:04 CST on 2026-09-16, PID 1308272. QQ connected
at 16:33:06; local/public health passed, NovelAI ready, zero pending projections
and storage write failures. Environment and systemd unit hashes are unchanged;
the unit still has `UMask=0077`. Reviewed repair source hashes match the files
loaded on restart. See `outage-deployment-result.json`.

The real service-mode model probe and native tool regression are distinct from
HTTP health. No manual QQ test message or replay of the failed user request was
sent. No source commit or database rollback was performed.
