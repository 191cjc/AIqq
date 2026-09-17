# R12: Pure-image QQ delivery after expiry

The user explicitly requests removing the literal @ encoding appended to fallback image messages: send only the image. Implement and deploy this bounded correction under the existing repair/restart scope. Text/Markdown final replies still mention the original user. The chat-vision R11 documents remain a proposal, not authorized for implementation.

## Boundary and ownership

- Worker owns only src/aiqq/services/qq/sender.py, tests/unit/services/test_qq_sender.py and test_conversation_reply.py. Preserve all existing dirty work; parent owns README/spec/task records/deployment.
- In the existing expiry branch, add mention text only for text/Markdown, never message_type7. The image wire request must contain media and no nonempty content; the real SDK may serialize content=None. Keep the local [图片] placeholder and source association, without embedding an @ in persisted image payload content.
- Retain fallback opt-in through original-member context, both exact expiry errors, one retry, null msg_id/msg_seq, same file_info and one upload. Existing non-expiry/cancellation and QQ quota behavior remain unchanged.
- Adjust existing SDK and actual presenter regressions to assert pure images on initial/fallback requests, text/Markdown mention retained, and local metadata stays consistent in foreground/deferred completion. No new standalone test framework or unrelated refactor.

## Verification and rollout

Worker runs the two affected test files. Independent reviewer checks exact delta and runs full packaged suite once, plus diff/syntax checks. No real QQ sends/generation/replay or config change. Parent checks no active user workflows, restarts after verification, checks local/public health and QQ/NovelAI readiness. Preserve an exact backup and scoped rollback. No new commit requested.

## Evidence

User-initiated request757 completed generation at14:01:15. Logs show40034005 fallback success for text and image; records758/759 were assigned by QQ. Image fallback currently sets content to a member mention, causing the user-visible literal encoding. This also supplies the previously pending live R10 fallback acceptance evidence; do not resend it.

## Verification before deployment

22 targeted tests and212 full packaged unit tests passed (8.834 seconds). Independent review found no issues and changed no files. Diff check and3-file AST syntax checks passed; no standalone lint/type-check configured. Five-file reviewed hash snapshot and restoration-only patch are in `/var/lib/aiqq/backups/qq-image-only-20260916T140658/`. Deployment briefly waits for user-initiated request763 (14:12:26, image search) to finish; no task will be interrupted.

## Deployment result

Request763 finished before restart: final text record765 at14:13:20 and image record766 at14:13:22, received14:13:23.511. No Codex child or newer user request was active at the idle check. All five reviewed file hashes matched immediately before deployment.

Restarted `aiqq.service` on2026-09-16 at14:16:21 +08:00, new PID1251315, active/running and NRestarts=0. Local and public `/health` returned HTTP200, QQ connected, group-message database open, NovelAI configured=true and ready=true. Startup logged `novelai_connection_ready` at14:16:24.623 and `aiqq_ready` at14:16:24.913.

The pure-image correction is deployed. No manual QQ send, generation, historical replay or configuration change was made; live display of a new expired-reply image remains for a subsequent user-initiated request. No commit or archive performed; broader task remains in_progress and R11 remains plan-only. Deployment evidence is saved as `deployment-result.json` beside the existing exact rollback patch; rollback must preserve unrelated dirty work.
