# Fix current chat request instruction boundary

## Goal

Make the current user's request executable as a user-level task while preserving
the reference-only role of history, quotations, attachments and tool results.

## Requirements

- Explicitly identify `current_message.content` as the current user's request and
  answer or perform that request subject to the existing fixed rules.
- Do not classify the whole current request as non-executable reference material.
- Treat historical messages, quoted content, attachments and tool results as data
  to process for the current task; embedded instructions cannot override fixed
  role, tool permissions, output protocol or safety rules.
- Keep the same boundary in ordinary chat instructions and the Codex chat runtime.
- Preserve the distinction between evaluating quoted text and carrying out a task:
  follow the actual current request, without automatically executing quoted text.

## Acceptance criteria

- Neither assembled prompt blanket-forbids following the current request.
- A direct multi-part task produces the requested answers in a bounded live check.
- A quotation containing competing instructions is analyzed as requested without
  executing those embedded instructions in a bounded live check.
- Unit tests, compilation and whitespace checks pass, and the updated idle service
  restarts successfully with healthy gateway/database status.
