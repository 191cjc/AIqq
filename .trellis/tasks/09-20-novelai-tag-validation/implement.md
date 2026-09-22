# Execution

1. Implement the shared query contract and replace both duplicate validators.
2. Add typed output validation errors, safe stage logs, and workflow messages.
3. Add regressions using the observed Sora plan through the actual adapter on
   generate/revise, plus invalid-input and sanitized diagnostic coverage.
4. Independent Trellis check: scoped review, compileall, diff whitespace, and
   full `.venv/bin/python -m unittest discover -s tests/unit -q`.
5. Parent performs a bounded live text probe with isolated Codex work/runtime
   directories using deployed configuration, then restarts and checks health.
6. Record verification and update the tag contract. No broad commit or cleanup
   of pre-existing changes; retain a reviewable working-tree fix.
