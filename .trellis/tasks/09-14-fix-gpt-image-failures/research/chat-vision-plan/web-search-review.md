# Independent standalone web search review — 2026-09-16

## Result

No blocking or other actionable findings in the search-only production delta.
No production code or implementer-owned test file was edited by the reviewer.
The final logging adjustment (`event=` and actual exhausted request number) was
re-read and included in the production-equivalent systemd verification below.

## Reviewed boundary

Compared the two production files against the exact baseline at
`/var/lib/aiqq/backups/chat-search-20260916T201857/`, rather than the repository's
unrelated dirty Git delta. Read the diagnosis, repair contract and backend
chat-read spec, and reviewed all seven new search regressions.

- Only the exact POST `/v1/alpha/search` route is added, with the existing feature
  flag passed from CodexSDKBackend. Responses logic, filesystem/network rules,
  service settings, databases, image generation and QQ delivery are unchanged.
- The route uses the model token, denies the read token and disabled feature,
  and sends only to the configured provider base plus `/alpha/search`. Incoming
  path/query cannot select the destination. Parent replaces Authorization; no
  real provider key is written to generated child runtime files.
- Search bytes and native headers are preserved without model rewriting or
  private-history insertion. Search has a separate 16-request counter, 64 MiB
  input limit (native search includes complete conversation, like Responses), 16 MiB decoded output limit and 60-second total handler deadline.
  Redirect following is explicitly disabled.
- Failure diagnostics contain fixed reason, HTTP status and request number only.
  Query/body/URL/header/token values are absent. Cancellation propagates, pending
  search handlers are tracked and closed on runtime exit, and native temporary
  work directories are removed.
- Spec now requires a real native web tool result under service restrictions;
  enabling the feature alone cannot satisfy the regression check.

## Verification performed independently

1. `python -m unittest tests.unit.services.test_chat_search
   tests.unit.services.test_chat_runtime tests.unit.services.test_codex_sdk_backend -v`:
   **32 passed**, 2.469 s. Includes actual CLI successful search result and failed
   HTTP search result, existing real read/view_image execution, file/network
   denial, SDK cancellation/environment and image profile regressions.
2. Three extra offline assertions using isolated synthetic upstreams:
   **3 passed**, 0.264 s. Checked fixed provider target despite a query URL,
   independent model/search request counters and absence of real key in runtime
   files; gzip-decoded output size and removal of stale Content-Encoding; and
   504 deadline for an incomplete client request body before any upstream call.
3. Transient `aiqq-chat-search-review` systemd unit, ubuntu user/group, same
   working directory and **all production restrictions**, including
   NoNewPrivileges, PrivateTmp, ProtectSystem=strict, ProtectHome=read-only,
   ReadWritePaths=/var/lib/aiqq, ProtectKernelTunables, ProtectKernelModules,
   ProtectControlGroups, LockPersonality, RestrictSUIDSGID and UMask=0077:
   all seven search regressions plus actual CLI image/history roundtrip and
   file/network restriction test: **9 passed**, 2.656 s. Only synthetic local
   upstreams; production service was not restarted by this reviewer.
4. Compileall for both changed production files and the new test file: pass.
   Scoped `git diff --check`: pass. No project lint/type-check tools or commands
   are configured in pyproject/workflow/spec; these are syntax/whitespace
   checks, not a claim that a static type checker ran.

Parent separately reported full packaged suite **272 passed** and provided
`web-search-live-result.json`: real configured gpt-6-astra under all service
restrictions, three search POSTs returning 200, final web/image results and no
leftover turn directories. That live provider check was performed by the parent,
not duplicated in this review. Reviewer made no QQ sends, real provider calls,
service restart or commit.

## Final adjustment verification

The implementer increased the initial 256 KiB input budget to 64 MiB after
confirming that native search includes the full conversation. This aligns search
with the existing Responses capacity and avoids rejecting large complete history
inputs. The final native test includes over 256 KiB of UTF-8 history and verifies
exact preservation in the real CLI search payload. Reviewed the final change and
re-ran **all seven search tests under all production systemd restrictions** in
`aiqq-chat-search-final-review`: **7 passed**, 1.966 s. No new findings.
The parent full-suite result above predates only this final bound/test change;
this last service-mode run verifies the final search implementation.

## Reviewed file hashes

- `src/aiqq/services/ai/chat_runtime.py`: `1370da23dab8cd16a0d3bf0d8da60b18e09db0d0f838b4d81439e76bfa1cace6`
- `src/aiqq/services/ai/codex_sdk.py`: `883539b27cd07ad366d1aac86bf3faa959d4dd26036b0bca0f2e2334b266009b`
- `tests/unit/services/test_chat_search.py`: `9ad9426292cc3f1842ab8ad12dde5c5a6d32e4556887da440b60c5597f328dfa`
