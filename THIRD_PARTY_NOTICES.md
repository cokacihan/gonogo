# Third-party notices

## NASA F Prime (F´)
`data/fprime_dataset.json` and the Relate store in `data/fprime/` contain requirement IDs, requirement text,
verification methods, file paths, component/instance names and port connections extracted from
[nasa/fprime](https://github.com/nasa/fprime) (commit recorded in the dataset's `source.commit` field).

Copyright California Institute of Technology. Licensed under the Apache License, Version 2.0
(https://www.apache.org/licenses/LICENSE-2.0). The extracted text is reproduced unmodified; the graph
structure (nodes, relationships, derived findings) is produced by TraceGraph. F´ source code is not
redistributed in this repository; `python -m tracegraph.fprime --repo <checkout>` re-extracts it.

Findings that TraceGraph reports on F´ (for example requirement IDs cited by tests but not defined in an
SDD table) are produced automatically from repository text and may include parser false positives. They are
not statements about the quality or correctness of F´.

## Relate
TraceGraph uses [Relate](https://github.com/relatedb/relate) (`relate-db` on PyPI, `@relate/relate-wasm` on npm),
Apache License 2.0.
