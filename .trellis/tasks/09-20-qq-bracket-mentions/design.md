# Design

Share the leading-name matching rule for plain @name and bracketed [@name], and use that same recognition to remove the prefix exactly once. Prefer authoritative mentions[].is_you names when available, otherwise current runtime name. Retain native angle-ID recognition. Process only a leading mention, never search/replace throughout content. Preserve raw envelope before business normalization.

Normalize raw and SDK-derived content at the shared ingress boundary before GroupMessageHandler command parsing. _incoming can use raw_data when available for richer metadata/attachments, and the current runtime name for SDK projections. Do not normalize once in raw path and again in handler, since that can remove later mentions from the real body. GroupMessageHandler's IncomingGroupMessage is an internal normalized input; direct unit fixtures must reflect this if moving all cleanup to ingress. Existing parse_command stays unchanged.

SDK/raw duplicate races must be tested with each callback first, using real botpy GroupMessage construction. A mock command handler or router with fake workflows is sufficient; do not call models or send QQ messages. Native mention metadata is the trustworthy target indicator, and bracketed display is a valid QQ representation, not a user typo.

Rollback uses a pre-edit private snapshot of only the two source files; no DB changes. Preserve all prior uncommitted NovelAI style/tag and GPT work. Source can be restored without touching that work.
