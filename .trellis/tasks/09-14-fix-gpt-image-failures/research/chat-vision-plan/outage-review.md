# Independent chat-outage repair review

Reviewed the four-file repair against the exact pre-repair snapshot at
`/var/lib/aiqq/backups/chat-outage-20260916T162521/`, the R11 v4 artifacts,
`outage-repair.md`, and the current chat-read contract. Unrelated dirty workspace
changes were not treated as part of this repair.

## Findings (fixed)

No production or test corrections were required by this review.

## Findings (not fixed)

No blocking or nonblocking code issues found in the scoped repair.

- `chat_isolation.py` changes the umask only in the forked child, after successful
  confinement and before the genuine CLI executes. The parent and trusted
  supervisor retain their inherited mask. Filesystem, network and metadata
  syscall rules are byte-for-byte unchanged from the pre-repair implementation.
- The backend creates both outer turn directories with `mkdtemp` (0700) and
  protects their roots as 0700. Child-created 0644 files remain inside these
  private directories; the child cannot chmod them or the outer directories.
  Parent bridge keys remain outside the child environment. `chat_runtime.py`
  is unchanged from the snapshot.
- The SDK diagnostic emits only the exception type and one of two fixed reasons.
  Known initialization denial is recognized only for `CodexExecError`; original
  stderr, prompts and signed URLs are not logged. Failure cleanup and existing
  context-limit/cancellation behavior are retained.
- The native regression executes the genuine CLI, actual read scripts and both
  view-image calls under an inherited 0077 mask. It checks parent-mask/file and
  outer-directory modes, child-created mode, continued host/cross-turn/symlink/
  proc denial, chmod/fchmod denial, full history transport, both pixel inputs,
  orphan-child reaping and final workspace cleanup. The separate confinement
  test also proves fchmod denial on a host file descriptor opened before
  confinement.

## Verification

- SDK backend module: **13 tests passed**.
- Chat runtime module: **12 tests passed**, including the genuine installed CLI
  and local synthetic Responses endpoint; no paid model or QQ calls by reviewer.
- Scoped `compileall`: passed.
- `git diff --check`: passed.
- Lint and TypeCheck: no dedicated tools/configuration declared in this project;
  syntax compilation is not claimed as a static type check.
- Main-session evidence reviewed separately: `outage-live-result.json` reports
  both prepared-card pixel texts correctly read by `gpt-6-astra` in 27.52 seconds
  with all production systemd restrictions, both production feature flags on,
  no generation/QQ sends/production DB writes, and both turn directories cleaned.
  This reviewer did not restart or modify the production service.

## Reviewed source hashes (SHA-256)

| File | Hash |
| --- | --- |
| `src/aiqq/services/ai/chat_isolation.py` | `56b5f37c91f2ea52a6df4bf13667a0493826e00df7d01d855ee510c9c9765dd6` |
| `src/aiqq/services/ai/codex_sdk.py` | `6863b5216c0c26bf1436594a3dadfa1c67323ce607fb2638d500208819fbc6bd` |
| `tests/unit/services/test_chat_runtime.py` | `2a9306135bb8be48a22efd22d2eb33fdd6ae15c582dfa06e5c9b0b428ae20292` |
| `tests/unit/services/test_codex_sdk_backend.py` | `c824e3b0f2abbd7a7db1cde83bfbde38e66d6ed6dc843e8685a8cf3a3f66acf5` |
