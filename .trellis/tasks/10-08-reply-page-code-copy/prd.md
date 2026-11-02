# Improve full reply page and add code block copy

## Goal

Make the complete-reply page easier to read and let users copy individual code
blocks without a whole-reply copy action.

## Requirements

- Remove the whole-reply copy button and hidden raw-Markdown textarea.
- Improve responsive reading typography, spacing, tables, quotes and code styling.
- Give every fenced or indented code block its own copy button and language label.
- Copy only that block's exact text, preserving indentation, newlines and symbols.
- Provide copy confirmation and a fallback for browsers without clipboard access;
  if copying remains unavailable, select only the requested code block.
- Retain escaped Markdown rendering, existing privacy headers and pending refresh.

## Acceptance Criteria

- Multiple code blocks have independent buttons; inline code has no extra button.
- Copied text matches the selected block and excludes surrounding prose and fences.
- Successful, denied and unavailable clipboard paths work in a real browser.
- Desktop and narrow mobile layouts remain readable without page-wide overflow.
- Raw HTML, code text and language labels cannot inject executable HTML.
- Existing pending pages refresh until completion; completed pages do not refresh.
- Focused/full tests and diff checks pass, then the service is restarted and healthy.

## Notes

The change belongs to the packaged reply renderer and its tests/documentation.
Reply storage, retention, QQ message behavior and unrelated workspace edits are
outside this task. Existing generated-prompt pages use the same renderer.
