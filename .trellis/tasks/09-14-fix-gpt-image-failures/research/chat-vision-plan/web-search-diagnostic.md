# Web search failure diagnosis — 2026-09-16

User asked to investigate recent web-search failures. No production source edits,
service restart, real model invocation or QQ test send in this investigation.

## Evidence

- Runtime config enables web search and web image search; model gpt-6-astra.
- Saved bot records854 (17:53:25),1161 (18:34:45),1242 (19:31:57),1248
  (19:44:39) explicitly report inability to search/verify, with the last reporting
  repeated interruption when looking for a game-character image. Times are CST.
- Application/journal logs since16:33 contain no conversation startup failure.
  These records preserve the answer, not the original web HTTP error; do not
  falsely claim the original production traces contain an observed404.
- Offline diagnostic with genuine CLI and synthetic SSE explicitly invokes
  function `run` in namespace `web`. In both a normal process and the exact
  production systemd sandbox, it sends POST `/v1/alpha/search` to the bridge,
  receives404, then continues the Responses turn with tool output `aborted`.
  Only two `/responses` requests reach the fake upstream; no search does.
- This is deterministic evidence of the current missing-route bug. Existing
  bridge registers only `/v1/responses`, `/history`, `/image` and has access
  logging disabled. The model continues normally, so no CodexExecError is raised.
- Prior vision validation enabled search but never executed it. It therefore did
  not prove that the standalone search endpoint was reachable.

## Repair boundary

Add explicitly authenticated forwarding for the exact `/v1/alpha/search` route
(and only independently confirmed additional native web routes, if any). Use
parent-owned upstream credentials, finite request/size/time budgets, preserve
service isolation and feature gating, and keep the model payload/history mutation
logic separate from standalone search payloads. Record fixed failure kind and
status without query/body/token leakage. Add a real-CLI synthetic web roundtrip
under all service properties, including non200 cases, plus a bounded successful
real search check before calling it restored. A local route repair by itself does
not establish current upstream search availability.

See `web-search-probe.py` and `web-search-diagnostic.json`.
