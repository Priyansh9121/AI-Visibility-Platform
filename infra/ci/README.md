# infra/ci

The gate in front of `main` — Epic 18. The workflow itself is
`.github/workflows/ci.yml`; this directory holds what it asserts against.

`floors.json` records the last known passing count of every suite and the
ceiling on `mypy`'s error count. `assert_count.py` reads a suite's log and
fails the job if the count is below its floor (or above its ceiling). Read the
`_why` in the JSON before touching a number.

Two rules the workflow follows, both learned here the hard way
(north-star.md §4.3):

1. **No test or lint command is ever piped.** Each writes to a file and its own
   exit status is checked; only then is the file read.
2. **The count is asserted, not only the exit code.** An exit code cannot tell
   "all green" from "two-thirds of the suite never ran".

Bumping a floor is part of the commit that adds the tests. Lowering one needs a
sentence in `build-log.md` saying which tests went and why.
