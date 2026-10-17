# Contributing

- Your tickets are in your pinned queue issue: Dev A @ochodolo #26, Dev B @AJChaikovsky #27, Dev C @RishiSujit #28. Work them top to bottom.
- `main` is always runnable: `pytest` passes.
- One branch per ticket, named in the ticket (e.g. `b/B3-matcher`). When the ticket's **Test checkpoint** passes, merge your own branch into `main` with `Closes #<issue>` in the merge commit message. No PR review is required.
- Commit messages start with the card ID: `B3: sibling points only on 1st choice`.
- `core/types.py`, `agents/messages.py`, and the signatures in `core/stubs.py` are frozen. Adding a field with a default or a new code is OK; announce it in chat. Renames and removals need all three devs.
- If you change a fixture in `tests/fixtures/`, say so in chat; other devs test against it.
- One owner per file (see the docstring at the top of each module).
- Secrets go only in `.env`, never in git.
- Merge windows at 8:30 PM and 10:30 PM go B → C → A. After any merge, everyone pulls `main` and runs `pytest` (from Phase 3 also `python scripts/replay_golden.py`).
