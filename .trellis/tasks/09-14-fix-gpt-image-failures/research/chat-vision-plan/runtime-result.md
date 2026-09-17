# R11 runtime verification

Final reviewer verified the implemented chat runtime without provider calls,
QQ sends, historical replay, restart, or commit. No production runtime changes
were needed in this final pass; only regression assertions and review records
changed. Prior storage/security fixes are recorded in review.md.

## Verified behavior

- Ordinary chat stages the complete aiqq-chat-read skill directory, including
  executable Python scripts; audit and image-generation profiles retain their
  own tool restrictions. Both backends preserve all permitted image inputs and
  explicitly reject an oversized input-image list.
- Per-turn read/model bridge credentials are separate. The parent retains the
  real model key and forwards genuine CLI headers. Read calls expose neither
  an arbitrary group selector nor arbitrary URLs, files or database paths.
- Native shell output is bounded by the CLI. The history bridge therefore keeps
  each complete page and appends it as untrusted_chat_read_results to subsequent
  model requests. The shell receives a bounded model_context receipt with the
  message count, independent record/version/event cursors, has_more flags and
  page budget. The tested >10,000-character page retains exact content and nested
  null/false/zero values. Oversized pages return explicit capacity failures.
- Genuine installed CLI executes the staged history/image scripts and two
  view_image calls against a synthetic local Responses SSE endpoint. The final
  upstream request contains two distinct image data URLs and the complete
  requested page. This does not depend on guessing a view_image JSONL event.
- Linux Landlock and seccomp deny host DB/config/proc, other-turn files,
  symlink escapes, unrelated TCP ports, UDP and fresh UNIX sockets. TCP rules
  constrain destination ports, not addresses; this is not loopback-only network
  isolation. The launcher fails closed when required isolation is unavailable.
- Trusted directory descriptors and no-follow file operations prevent the
  child-writable read-images directory from redirecting privileged image writes.
  A parent-owned supervisor reaps detached descendants; cancellation stops active
  reads; completed/cancelled turns remove temporary images and workspaces. Crash
  cleanup only removes marked turns whose exact owner process is no longer live.
- Responses compatibility uses bounded function calls and complete JSON tool
  records plus actual input_image data. Schemas include all supported history
  filters and independent pagination fields with strict required parameters.
- Existing GPT 502 retries, image-only behavior, reference errors and QQ upload
  compression pass the targeted regressions. Chat read does not enable native
  image generation or reserve generation quota.

## Results

Command:

```bash
.venv/bin/python -m unittest \
  tests.unit.services.test_chat_runtime \
  tests.unit.services.test_codex_sdk_backend \
  tests.unit.services.test_responses_backend \
  tests.unit.services.test_codex_responses_image \
  tests.unit.services.test_gpt_image \
  tests.unit.services.test_reference_image \
  tests.unit.services.test_qq_image_upload -q
```

75 tests passed in 7.752 seconds. Parent final packaged discovery passed 264
tests in 14.757 seconds. compileall, diff whitespace check and skill validation
passed. There is no configured standalone linter/static type checker.

The already authorized live two-card probe is recorded separately in
live-vision-result.json: gpt-6-astra recognized both pixel-only markers in
76.36 seconds, with zero remaining turn directories. It was not repeated for
this review and does not claim QQ delivery acceptance.

Deterministic image-failure summaries were additionally checked across 298
combinations of up to three known error kinds; maximum length is 32 characters.
No blocking runtime finding remains. Deployment and post-restart health checks
belong to the parent session.
