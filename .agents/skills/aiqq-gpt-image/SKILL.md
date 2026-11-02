---
name: aiqq-gpt-image
description: Convert an explicit AiQQ group-chat request to generate or edit a GPT image into the structured image_action field. Do not use for image questions, web image search, or requests without image creation or editing intent.
---

# AiQQ GPT Image Action

Use the output schema supplied for the current turn. This skill only determines the
`image_action` value inside that final response; it never generates or downloads an
image itself.

- For an explicit request to create an image, use `mode="generate"`, write a complete
  standalone visual prompt, and set `source_record_id=null`.
- For an explicit request to modify an available reference image, use `mode="edit"`
  and select a real `record.record_id` from the current message, supplied history,
  or a same-group history tool result with `derived.has_image=true` and no recalled
  state. Older protocol entries may use top-level `record_id` and `has_image`.
- Otherwise set `image_action=null`. Questions about images and requests to find an
  existing web image are not generation requests.
- Every non-null `image_action` must include `width` and `height`, both set to `null`.
  Generation and editing use automatic sizing (`size="auto"`). The application
  displays the returned image unchanged and does not guarantee requested or
  reference dimensions. Include user requests about dimensions, aspect ratio, or
  preserving composition in the prompt. Translation and retouching do not imply
  a request to crop, add padding, or change composition.
- `width` and `height` must either both be `null` or both be integers from 256 through
  2048, and their product must not exceed 4194304. Never output `auto`, a dimension
  string, only one dimension, or values outside these limits.
- Never invent a record ID, local path, credential, QQ identifier, API parameter, or
  image result. Application code performs safety review, quota reservation, image
  generation, validation, storage, and delivery after the structured turn completes.
