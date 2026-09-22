# Verification

## Original-event offline reproduction
Read-only SQLite retrieval of records 3914 and 3918, no business replay. For each captured raw envelope:
- Constructed actual installed botpy GroupMessage from original data.
- Ran raw-data and SDK-only _incoming with the confirmed current bot name, plus normalized_group_message_data.
- All three paths produced identical normalized content and parse_command returned novelai_image.
- Argument exactly matched original suffix following /NovelAI生图, retaining the multiline argument on record 3918.
- Deep comparison confirmed the original raw envelope was not mutated.

No model/image requests or QQ messages were sent. Focused handler/client/router tests: 37 passed. Actual SDK integration asserts one NovelAI workflow and zero GPT/conversation calls for raw-first, SDK-first and concurrent delivery; raw events, multiline arguments and attachments preserved. compileall and git diff --check passed. Independent review found no issues; full unit suite: 337 tests passed in 17.670 seconds. compileall and git diff --check passed. No lint/type checker configured.

## Deployment
Restarted aiqq.service at 2026-09-20 13:43:18 Asia/Shanghai, PID 2739071. Active/running, NRestarts=0. HTTP health returned 200/status ok; QQ gateway connected, database open, NovelAI configured and ready. Startup at 13:43:21 logged panel unchanged and aiqq_ready. Existing group records were not replayed; no paid generation or manual QQ messages sent. Changes remain uncommitted.
