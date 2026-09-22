# Execution

1. Add admitted-attempt callbacks to the three workflows and test ordering,
   rejection, failure and cancellation semantics.
2. Wire persistent QQ notices for command and conversation paths, retaining
   one-shot behavior and sequence allocation across progress/deferred outcomes.
3. Add boundary tests using fake providers and QQ transport; adapt existing
   mocks only as needed. Test actual send ordering and sequence numbers.
4. Independent review runs full unit suite, compileall and git diff --check;
   fix concrete scoped issues before deployment. No linter/type checker configured.
5. Parent updates the contract, restarts aiqq.service and checks local health.
6. Record results without committing unrelated existing changes.

## Visible quote amendment

7. Implement opt-in text reference for the start notice. Verify real installed
   botpy serialization, persistence and expired passive fallback without any live
   QQ sends; preserve no-quote defaults and existing sequence/generation behavior.
8. Independent scoped review and full checks, then restart/check health and
   record the follow-up deployment.

## Content rejection copy amendment

9. Translate the observed exact SDK image rejection to a shared exception and
   present the fixed QQ content-rejection message. Preserve all other errors.
10. Regress actual SDK error parsing through sender/presenter, image-only scope,
    upload/send rejection, foreground/deferred completion, expiry fallback
    rejection, unknown errors and cancellation. Prove no repeat upload or send,
    and no false successful image record. Run focused tests, compileall/diff.
11. Independent review runs the full suite; parent syncs spec, restarts when
    idle, checks health and records results. No live QQ or paid image tests.
