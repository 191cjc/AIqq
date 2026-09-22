# Bug Analysis: valid QQ mention triggered the wrong provider

## 1. Root Cause Category
Cross-layer contract and test coverage gap: trigger detection trusted mentions[].is_you, but string cleanup assumed only angle-ID or plain-name representations. A bracketed display representation passed the first boundary and failed command-prefix parsing at the next.

## 2. Why Earlier Coverage Missed It
The September 17 text-mention change covered manually entered @runtime-name, native angle-ID mentions and raw/SDK dedup separately. It did not test a captured bracketed native mention through final command routing. SDK GroupMessage._User also drops the raw mention username/is_you, so a raw-only repair could be defeated by SDK-first delivery.

## 3. Prevention Mechanisms
- One ingress normalization contract for raw/SDK inputs and one name-prefix matcher for recognition/removal.
- Keep original QQ receipt immutable; normalize only the business input.
- Use actual SDK constructors in tests and assert parsed novelai_image plus no chat/GPT call, with both callback orders.
- Cover bracketed name, native angle-ID, plain current name, wrong targets, following mentions, empty/image-only and multiline content.

## 4. Systematic Expansion
Only the QQ ingress boundary needs modification. Prompt generation, image provider, panel rendering, database contents and usernames are unrelated to the defect. Generic command routing is the dependent consumer; testing only message delivery without asserting the selected command is insufficient.

## 5. Knowledge Capture
Backend chat-read contract and README updated with valid bracket representation, SDK projection loss and ingress-only normalization. This repository has no generated spec template mirror. No historical message replay or paid generation is needed to validate the fix. No commit requested.
