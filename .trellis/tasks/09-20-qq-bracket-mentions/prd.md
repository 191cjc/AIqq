# Normalize valid QQ bot mention representations

## Requirement
The user's QQ @ interaction is normal. Fix the application ingress adapter so native bracketed content such as [@CurrentBot] /NovelAI生图 ... routes identically to <@bot-id> /NovelAI生图 ... and existing plain @CurrentBot content. Do not require a different QQ user action or change original stored content.

## Evidence
2026-09-20 record 3914 at 10:45:24 has native angle-ID prefix and mentions[].is_you=true, correctly routed to NovelAI. Record 3918 at 10:48:12 has [@runtime-name] and the same target marker, but normalization leaves the bracket prefix, command parsing returns None, and ordinary conversation generated via GPT. The two requests have distinct IDs. Reproduction removing only the exact bracketed mention parses novelai_image even with original newlines. Both text replies had menu buttons; the defect is routing. User requests the application fix.

## Acceptance
- Both observed forms route to novelai_image, never ordinary conversation/GPT; preserve full multiline command arguments.
- Reuse current runtime bot name or authoritative is_you metadata; no hard-coded deployed name, no broad removal of arbitrary [@person] content.
- Structured mentions still trigger; leading exact textual bracketed runtime-name mention can use the existing text-mention convention. Wrong, partial, old and mid-message text remains stored without dispatch.
- Native metadata can recognize a known display name; preserve leading/trailing cleanup convention and untouched inner text/attachments/unknown fields.
- Raw and SDK callbacks normalize consistently before shared command routing and deduplicate by group+message regardless of callback order. Avoid stripping twice through layered adapters.
- SDK GroupMessage._User drops mention username/is_you (inspected installed SDK); SDK-only ingress must handle the current name without depending on those lost fields.
- Original raw persistence/version/history stays intact. No existing message replay, new generation, quota change or manual QQ test send.
- Focused regression, offline reproduction of the two actual stored events, independent full unit suite, then restart and health verification.

## Change boundary
Only interfaces/qq/handlers.py and client.py plus relevant handler/client tests own this fix; parent updates chat-read spec, README and task evidence. Centralize interpretation at ingress, keeping handler input normalized. If needed remove duplicate cleaning in handler and update direct-handler fixtures to honor that internal contract; prove real SDK/raw native mentions through integration tests. No database, provider, image-service, prompt or panel changes.
