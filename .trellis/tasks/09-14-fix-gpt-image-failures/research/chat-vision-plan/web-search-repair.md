# Standalone web search repair

User explicitly requested repair and restart on 2026-09-16 after diagnosis.
Active task remains `09-14-fix-gpt-image-failures`.

## Boundary / contract

Current gap: the confined genuine CLI calls `/v1/alpha/search`, but the local
bridge exposes only Responses/history/image routes. It returns404 before any
upstream search and the model receives `aborted`.

Change `services/ai/chat_runtime.py` to proxy only the exact authenticated search
route with parent-held upstream credentials, disabled-feature enforcement,
finite request/response/time budgets and secret-free status/reason diagnostics.
Pass the existing feature flag from `codex_sdk.py`. Preserve search request shape
and native headers without applying Responses-only model/history transformations.
Keep redirects disabled and preserve inherited filesystem/network restrictions.
Add dedicated route/native-CLI tests in `tests/unit/services/test_chat_search.py`.
No changes to message data/schema, QQ delivery, generation, CLI umask or systemd.

Implementer owns those production files and new search tests. Parent owns bounded
real probe, spec/evidence and deployment. Independent checker follows; do not
undo concurrent or earlier uncommitted work.

## Verification / deployment

Test authentication, disabled search, bounded requests/input/output, HTTP/network
failures with safe diagnostics, unchanged search payload, native CLI actual tool
execution/result roundtrip, under all production systemd properties. Run full
packaged suite, compile and diff checks. Perform bounded live search with actual
configured backend and production flags, synthetic query/no QQ send, then restart
when idle and verify health/QQ/config/source hashes.

Before-change snapshot: `/var/lib/aiqq/backups/chat-search-20260916T201857/`.
No broad commit or rollback of existing chat storage.

## Implementation and validation

- Added exact POST `/v1/alpha/search`, with chat-token authentication and the
  existing `enable_web_search` flag (default false when constructing the bridge).
- Parent substitutes its upstream key and preserves native headers/JSON bytes.
  No Responses-only history or model rewriting is applied. No redirects followed.
- At most16 requests per turn,64 MiB request,16 MiB response,60seconds per call.
  Search requests carry native conversation context, so the initial256KiB limit
  was corrected to match the existing64MiB Responses capacity. The genuine-CLI
  regression now includes >256KiB complete history and verifies it is preserved.
- Fixed `event=chat_web_search_finished` logs contain only status/reason/request
  number. Cases cover authentication, disabled search, malformed/large input,
  budgets, upstream statuses, network errors, timeout and output size.
- Full packaged suite272 passed16.516s. After final request-limit/large-history
  adjustment,7 final search tests passed under all production systemd properties
  in1.964s; implementer independently ran7 in that environment plus12runtime and
 13SDK checks. compileall and diff checks pass; no separate lint/type-check tool.
- One native synthetic-model probe confirmed the real upstream search endpoint
  returns200 and valid CLI-readable results. Then the actual configured
  gpt-6-astra completed the bounded web/search-open/image-query prompt through
  the repaired code under all production service restrictions in39.79s. All3
  standalone requests returned200 and the answer contained a document title/URL
  and a real image URL. Work/runtime turns removed; no QQ send, generation or
  production database write. See `web-search-live-result.json`.
- Pre-deployment backup refreshed:1262 message rows, no active child processes;
  service unit and environment hashes unchanged.

## Review and deployment completed

Independent review reports no findings.32 targeted tests,3 extra boundary checks,
9 production-mode search/vision/isolation checks, and final7 search regressions
under all systemd properties pass. See `web-search-review.md` for exact hashes.

Restarted `aiqq.service` at20:34:04 CST on2026-09-16, PID1393579. QQ reconnected
20:34:06. Local and public health are normal, NovelAI ready, zero pending
projections/write failures. The1262 pre-restart rows match every column exactly;
1264 current rows include subsequently received messages. Environment/unit hashes
unchanged; reviewed production source hashes match. No manual QQ send or request
replay, no source commit. Evidence: `web-search-deployment-result.json`.
