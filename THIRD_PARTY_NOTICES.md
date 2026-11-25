# Third-party notices

## NASA F Prime (F´)
GoNoGo works on text extracted from [nasa/fprime](https://github.com/nasa/fprime). Copyright California Institute of
Technology. Licensed under the Apache License, Version 2.0 (https://www.apache.org/licenses/LICENSE-2.0).

What this repository contains from F´:
- `data/fprime_dataset.json`, the Relate store in `data/fprime/`, the older releases in `data/releases/`, the pull
  request graphs in `data/replay/*.json` and the browser bundle in `web/data/`: requirement IDs, requirement text and
  verification methods from the SDD tables, file paths, component, test, helper, instance and port names, port
  connections, `#include` relations, and the words of identifiers and of `TEST_CASE()`/`COMMENT()` descriptions in the
  unit-test code (the commit is recorded in each dataset's `source.commit` field). The extracted text is reproduced
  unmodified; the graph structure and the derived findings are produced by GoNoGo.
- `data/replay/pr5170-suggested.patch`, `data/replay/*-check.md` and the generated README blocks: short excerpts of
  F´ unit-test source (the context lines of a patch) and of SDD text.
- `data/fprime_test_results.json`: names and outcomes of F´ unit tests, from a run of F´'s own unit tests in this
  repository's GitHub Actions.

F´ source code is not otherwise redistributed; `python -m gonogo import-fprime --repo <checkout>` re-extracts the data
from a checkout of nasa/fprime.

Findings that GoNoGo reports on F´ (for example requirement IDs cited by tests but not defined in an SDD table) are
produced automatically from repository text and may include parser false positives. They are not statements about the
quality or correctness of F´. GoNoGo is not affiliated with NASA, JPL or the F´ project.

## Fonts and CI actions
- The explorer (`web/index.html`) loads Atkinson Hyperlegible and Barlow Semi Condensed from Google Fonts
  (fonts.googleapis.com) when it opens; both are published under the SIL Open Font License 1.1. Nothing else in the
  explorer contacts a third party.
- The "F´ unit-test evidence" workflow runs F´'s own GitHub Actions steps (`nasa/fprime-actions`) to build and run the
  F´ unit tests; GoNoGo does not redistribute them.

## Relate
GoNoGo uses [Relate](https://github.com/relatedb/relate) (`relate-db` on PyPI; `@relate/relate-wasm` on npm, a copy of
which is vendored in `web/vendor/relate-wasm/` together with its `NOTICE.txt`), Apache License 2.0.
