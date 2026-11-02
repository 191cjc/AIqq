# Complete Reply Pages

## Scope / Trigger

Read when changing the packaged complete reply page in
`src/aiqq/interfaces/web/replies.py`. The legacy root reply_store is separate.

## Signatures

`render_reply_page(content: str, *, pending: bool = False, prompt: bool = False) -> str` renders escaped
Markdown using Mistune. `ReplyHandler.serve` owns the expiring `/reply/{file_name}`
HTTP route and existing privacy/security headers.

## Contracts

- Render a responsive reading page; no whole-reply copy action or hidden raw
  Markdown document. Fenced and indented block code each get an independent
  button and a language label, defaulting to 代码 when no language is provided.
- Copy the selected rendered code element's textContent only. Preserve leading
  whitespace, internal/trailing newlines, Unicode and HTML-like characters.
  Do not copy Markdown fences, language labels, toolbars or surrounding prose.
- Use the browser Clipboard API on click, fall back to an ephemeral textarea
  and browser copy command, then select only that code block if copying fails.
  Show scoped feedback and an accessible status announcement; restore focus to
  the copy button after its temporary disabled state so keyboard users keep their place.
- Escape raw HTML, code contents and language labels. Keep scripts/styles local
  and inline so the existing CSP continues to work without third-party resources.
- Pending pages retain a three-second refresh; completed pages do not refresh.
  Keep no-store, no-referrer, nosniff and noindex behavior and expired-link 404s.
- The same route with `?view=prompt` renders a completed document as one literal
  copyable prompt, titled `本次提示词`, using the existing toolbar and clipboard
  fallback. Bypass Markdown interpretation, escape the full raw string and copy
  only its exact code textContent, without adding a trailing newline. Weights,
  Markdown-like syntax, HTML-like text and Unicode must survive unchanged.
  Pending documents still render their normal refreshing progress page.
  Ordinary reply copying and security headers remain unchanged.

## Validation and Error Matrix

| Condition | Behavior |
| --- | --- |
| Several code blocks | One independent copy action per block |
| Inline code only | Readable inline styling without an extra copy action |
| Clipboard write succeeds | Confirm copied; retain exact selected code text |
| Clipboard denied/unavailable | Attempt the browser copy fallback |
| All copy methods fail | Select that block only and explain manual copy |
| Untrusted HTML / language text | Escape; never execute or add event attributes |
| Narrow viewport / long code | Scroll inside the code block, not the whole page |

## Good / Base / Bad Cases

- Good: two Python/shell blocks copy independently with indentation intact.
- Base: a prose-only reply shows the reading page with no copy button.
- Bad: using innerText, stripping code whitespace, copying the toolbar, or
  retaining a hidden full-document copy control.

## Required Tests

- Server rendering: independent fenced/indented blocks, hostile HTML/language
  text, no whole-reply copy, and pending/completed refresh behavior.
- Real browser: exact copied payload, success/denial/unavailable clipboard paths,
  manual selection scoped to a block, accessible feedback and mobile overflow.
- Inspect desktop/mobile renders using synthetic content only, not stored user
  replies. Preserve the handler's existing security headers and storage tests.
- Prompt view: literal Markdown/HTML, weights, Chinese/internal newlines, exact
  clipboard payload, fallback/manual selection and existing route expiry.

## Wrong vs Correct

Wrong: copy a code wrapper's innerText or the reply's raw Markdown document.
Correct: copy the selected `pre > code` element's textContent unchanged.
