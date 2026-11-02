# Remove research target count restriction

## Goal

Allow ordinary group-chat research, comparisons, rankings and batch lookups
regardless of the number of independent research targets.

## Requirements

- Remove the research-target count rejection, including its audit category.
- Do not reclassify ordinary batch lookups or comparisons as excessive complexity
  solely because they include many targets or require multiple searches.
- Preserve adult-content, persona-override and genuinely excessive-deliverable
  review, audit failure handling, and complete user input.
- Keep documentation and regression coverage consistent with the new behavior.

## Acceptance criteria

- Research-target count is absent from supported audit rejection criteria.
- Seven-character rankings and nine-chapter date lookups can pass ordinary audit.
- Valid safety/complexity rejections and invalid audit outputs still stop before
  history and chat.
- Packaged unit tests, compilation and whitespace checks pass.
- Restart the idle service after verification and confirm gateway readiness.
