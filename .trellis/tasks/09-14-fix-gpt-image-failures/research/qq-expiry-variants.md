# Expired QQ reply variants — 2026-09-16

## Authorization and behavior boundary

The user reiterates the previously authorized requirement: when msgid expires, send the original reply and images as a direct message mentioning the original member. Continue that existing repair/restart scope, with no manual QQ messages or historical request replay. Diagnosis: `research/recent-image-request-diagnosis.md`.

The smallest gap is QQMessageSender recognizing only code40034031's exact SDK message, missing observed code40034005's exact message. Retain the current SDK and recognize both exact ServerError texts. Do not add substring matching, network retries, regeneration, persistent delivery queues, config changes, timeout changes or reference-retention changes.

## Implementation and ownership

Implementation worker owns only:
- src/aiqq/services/qq/sender.py: centralized immutable known expiry-message collection, exact SDK ServerError membership check. Both observed variants trigger existing single proactive @ fallback, msg_id/msg_seq=None, reuse uploaded media. Preserve opt-in, no source/progress behavior, recording and cancellation.
- src/aiqq/interfaces/qq/handlers.py: contain final foreground conversation send failures in a narrow Exception handler, emit fixed `event=group_reply_delivery_failed error_type=...`; return handled as other delivery paths do, no retries or user-visible success claim. CancelledError must propagate. Do not restructure command paths or deferred machinery.
- tests/unit/services/test_qq_sender.py, test_conversation_reply.py, test_qq_handler.py: regression tests described below. No unrelated files or cleanups.

Parent owns README, image-services spec, task records, backup, deployment and health checks. Preserve all existing dirty changes; worker is not alone in the codebase.

## Verification

- Both recorded QQ400 error payloads pass through installed botpy `_handle_response` and actual BotAPI payload defaults; verify one original rejection + one direct attempt, @ original user once, msg_id/msg_seq absent/null, original source association retained, saved proactive metadata.
- Exercise text and image fallback for both known variants, one upload reused; no fallback for unrelated errors, similar/suffixed text, wrong exception class, timeouts or cancellations; failed proactive attempt stops without false success.
- Actual presenter plus actual sender: foreground final reply and deferred completion with the new variant deliver original text and image; one prepared image/upload, no generation dependency. Keep bounded sends.
- Foreground delivery failure is contained and logged without raw errors/identifiers/content; cancellation propagates and workflow is not rerun.
- Worker runs affected unit files, independent reviewer runs full packaged unit suite, diff/syntax checks. No network in tests, no manual QQ send or paid generation.

## Rollout

Back up precise pre-change files, complete independent review/tests, inspect no active user workflows, restart aiqq.service, verify local/public HTTP200, QQ connected and NovelAI configured/ready. Record exact PID/start time and limits: actual direct QQ image acceptance for this error variant awaits a subsequent user request. No new commit requested.

## Implementation, validation and deployment result

- Sender recognizes both observed exact ServerError expiry texts via immutable `EXPIRED_REPLY_MESSAGES`; retains one proactive original-member mention, null passive reply fields and existing uploaded-media reuse. Foreground final sends now contain ordinary exceptions with a safe `group_reply_delivery_failed` event; cancellations propagate.
- 35 targeted tests passed (0.199s). Independent review found no issues and made no changes. Full packaged suite: 212 tests passed (8.668s). Five modified Python files passed AST parsing; `git diff --check` passed. No standalone lint/type-check configured. Regression cases include both actual SDK error payloads across markdown/text/images, foreground and deferred notice/final images, unrelated/near-match failures, no duplicate sends/uploads, cancellation and safe logging.
- At 11:14:06, no active service child workflows were present; latest user record remained 682 at 10:55:00. Reviewed seven-file hashes matched the deployment snapshot.
- Restarted **2026-09-16 11:14:38 +08:00**, PID **1192195**, active/running, NRestarts=0. QQ connected at 11:14:40. NovelAI connection-ready logged at 11:14:41.745. Both local/public health endpoints returned HTTP200, QQ connected, database open and NovelAI configured/ready true.
- No manual QQ messages, real generation, historical replay, configuration changes or new commit. Live direct image acceptance for this expiry variant awaits a subsequent user request; unit success does not establish external platform acceptance.
- Exact before-change files, restoration-only patch, baseline/deployment SHA256 manifests and deployment JSON: `/var/lib/aiqq/backups/qq-expiry-variants-20260916T110412/`. Roll back only those seven files if needed; preserve the restored NovelAI setup, earlier image behavior and unrelated dirty work.

## Subsequent live acceptance — 2026-09-16 14:01

User-initiated request757 generated successfully at14:01:15 (70356ms image edit). QQ rejected passive text and image with40034005; fallback logs show successful proactive text at14:01:17.205 and image at14:01:20.325. Bot records758/759 have QQ message IDs and original request association. This verifies actual direct image acceptance for the previously missing error variant. The image caption included a literal member mention; the user then requested removing that image-only caption in R12 (`research/qq-image-only-delivery.md`). No manual message or historical replay was initiated.
