# Contributing

- `main` is always runnable: `pytest` passes. Never push to `main` directly.
- One branch per ticket: `<dev>/<card-id>-<slug>`, e.g. `b/B3-matcher`. Small PRs, squash-merge, one reviewer.
- Put `Closes #<issue>` in the PR description. Commit messages start with the card ID: `B3: sibling points only on 1st choice`.
- `core/types.py`, `agents/messages.py`, and the signatures in `core/stubs.py` are frozen. Adding a field with a default or a new code is OK; announce it in chat. Renames and removals need all three devs.
- If you change a fixture in `tests/fixtures/`, say so in the PR and in chat; other devs test against it.
- One owner per file (see the docstring at the top of each module).
- Secrets go only in `.env`, never in git.
- Merge order at each gate: B → C → A. After a merge, everyone pulls `main` and runs `pytest`.
