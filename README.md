# GoNoGo

**Is this flight-software requirement actually verified, and which tests must I re-run after this change?**
GoNoGo turns an [F´ (F Prime)](https://github.com/nasa/fprime) flight-software repository into one
[Relate](https://github.com/relatedb/relate) graph and answers with receipts: the Cypher it ran, the test cases it
followed, and the CI run that produced the evidence. It says **GO** only when a requirement is backed by passing tests,
and **INCOMPLETE**, with a reason, whenever it cannot know.

[![CI](https://github.com/cokacihan/tracegraph/actions/workflows/ci.yml/badge.svg)](https://github.com/cokacihan/tracegraph/actions/workflows/ci.yml)
[![F´ unit-test evidence](https://github.com/cokacihan/tracegraph/actions/workflows/fprime-unit-tests.yml/badge.svg)](https://github.com/cokacihan/tracegraph/actions/workflows/fprime-unit-tests.yml)
![Relate](https://img.shields.io/badge/Relate-embedded%20graph-1f6feb)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

<!-- gonogo:headline -->
- **561** requirements, **2309** unit-test cases, **124** components, **72** port types and the **49**-instance Ref deployment of [NASA F´](https://github.com/nasa/fprime) (commit [`6fff089e8e`](https://github.com/nasa/fprime/tree/6fff089e8e2035307105cad4b28631eea1552ff6)) in one Relate graph (13153 relationships).
- Test code as a call graph: 2230 helper functions and 5318 `CALLS` edges. Evidence is a graph path from a test case to the `REQUIREMENT()` it relies on, up to 4 calls deep in F´.
- F´'s own unit tests, **run in our CI** ([run 45468869538](https://github.com/cokacihan/tracegraph/actions/runs/45468869538)): 2665 test cases, 2649 passed, 16 skipped, 0 failed. All 393 of the 393 test cases that cite a requirement ran.
- Ref deployment poll: **57 GO**, **0 NO-GO**, **170 INCOMPLETE** of 227 requirements in 34 components. Fully GO: `Svc/BufferManager`, `Svc/CmdSequencer`, `Svc/DpManager`.
- 10 test citations of undefined IDs, 6 duplicated requirement IDs and 12 cross-component citations, found from repository text (see [Findings](#findings)).
<!-- /gonogo:headline -->

> GoNoGo is decision support built from repository text and CI results. It is not a flight-readiness authority and is
> not affiliated with NASA, JPL or the F´ project.

## Why

F´ is NASA JPL's open-source, flight-proven framework for small spacecraft software. Each component documents its
requirements in a Software Design Document table (`Requirement | Description | Verification Method`), and unit tests can
cite a requirement with `REQUIREMENT("ID")`. The pieces needed to answer "is HTH-003 verified?" are spread over Markdown
tables, C++ test helpers several calls deep, FPP models and CI logs. The pieces needed for "I changed the `Svc.Sched`
port type, what must I re-test?" are spread even further.

These are connected-context questions. In GoNoGo they become short Cypher queries over one embedded graph that lives
in the repository, runs in CI without a server, and can be queried by people, scripts and AI agents.

## Quickstart

Python 3.12 or newer; no server, no API key.

```bash
git clone https://github.com/cokacihan/tracegraph gonogo && cd gonogo
python -m pip install -r requirements.txt
python -m gonogo poll                                   # GO / NO-GO / INCOMPLETE per component (Ref deployment)
python -m gonogo why HTH-001                            # one verdict, with reasons and evidence
python -m gonogo impact --files Svc/Sched/Sched.fpp --filters   # unit tests to re-run, as gtest filters
python -m gonogo findings                               # IDs that do not line up
python -m gonogo ask "What should I re-run if I change Svc/Health?"
python -m gonogo diff data/releases/fprime-v4.0.0.json data/releases/fprime-v4.4.2.json
python -m pytest
```

### In the browser

[`web/`](web) is a static explorer. It loads the same graph into Relate compiled to WebAssembly
(`@relate/relate-wasm`) and runs the very same verdict and impact Cypher in the tab. It offers:
- a readiness poll board;
- requirement-level verdicts with their tests and receipts;
- a "what must I re-run?" box;
- the release timeline;
- a free Cypher query box.

```bash
python -m gonogo.web_export          # refresh web/data from the dataset and CI results
python -m http.server 8000 -d web    # then open http://localhost:8000
```

`why` on a requirement whose tests cite it under a different ID (real output):

<!-- gonogo:why_hth -->
```text
HTH-001  INCOMPLETE   (Svc/Health, Svc/Health/docs/sdd.md#L13)
  The `Svc::Health` component shall ping each output port specified in the provided table.
  declared methods: Unit Test   CI run 45468869538
  - CITED_UNDER_DIFFERENT_ID: a test of this component cites an undefined ID ending in this one
  tests citing a near-miss ID:
    Svc/Health::Test.NominalTlm
      path: NominalTlm -> nominalTlm -> REQUIREMENT("ISF-HTH-001")
```
<!-- /gonogo:why_hth -->

`why` on a requirement backed by passing tests (real output):

<!-- gonogo:why_tlc -->
```text
TLC-001  GO   (Svc/TlmChan, Svc/TlmChan/docs/sdd.md#L16)
  The `Svc::TlmChan` component shall provide an interface to submit telemetry
  declared methods: Unit Test   CI run 45468869538
  own tests:
    Svc/TlmChan::TlmChanTest.ProcGuardTest PASSED
      path: ProcGuardTest -> runProcGuard -> sendBuff -> REQUIREMENT("TLC-001")
    Svc/TlmChan::TlmChanTest.UpdatedFlagClearGuarded PASSED
      path: UpdatedFlagClearGuarded -> runUpdatedFlagClearGuarded -> sendBuff -> REQUIREMENT("TLC-001")
    Svc/TlmChan::TlmChanTest.MultiChannelTest PASSED
      path: MultiChannelTest -> runMultiChannel -> sendBuff -> REQUIREMENT("TLC-001")
    Svc/TlmChan::TlmChanTest.NominalChannelTest PASSED
      path: NominalChannelTest -> runNominalChannel -> sendBuff -> REQUIREMENT("TLC-001")
```
<!-- /gonogo:why_tlc -->

## Verdicts

One meaning everywhere, decided by a single Cypher query
([`gonogo/verdict.py`](gonogo/verdict.py)) and re-implemented independently in plain Python for the tests.

| Verdict | Meaning |
|---|---|
| **GO** | Every test case of the requirement's own component that cites it with `REQUIREMENT("ID")`, directly or through any chain of helper calls, **passed** in the loaded CI run, and nothing else blocks it. |
| **NO-GO** | A citing test case of the own component **failed**. Only real failures are NO-GO. |
| **INCOMPLETE** | The graph cannot conclude. Each case carries reason codes. |

| Reason code | When |
|---|---|
| `NO_CITING_TEST` | no test case cites the requirement |
| `CITED_UNDER_DIFFERENT_ID` | a test of the component cites an undefined ID that ends in this one (e.g. `ISF-HTH-001` for `HTH-001`) |
| `CITED_BY_OTHER_COMPONENT` | only tests of another component cite it |
| `AMBIGUOUS_ID` | the same requirement ID is defined more than once |
| `NO_METHOD` | the SDD row names no recognisable verification method |
| `NOT_EXECUTED` | a citing test case did not run in the loaded run |
| `EVIDENCE_OUTSIDE_REPO` | a declared method such as Inspection leaves no record in the repository (`--accept-declared` waives it when an own test passed) |

Current poll (generated):

<!-- gonogo:poll -->
| Scope | Requirements | GO | NO-GO | INCOMPLETE |
|---|---|---|---|---|
| Ref deployment (34 components) | 227 | 57 | 0 | 170 |
| All components with requirements | 561 | 140 | 0 | 421 |

INCOMPLETE reasons over all requirements (a requirement can have several):

| Reason | Requirements |
|---|---|
| `NO_CITING_TEST` | 400 |
| `EVIDENCE_OUTSIDE_REPO` | 155 |
| `NO_METHOD` | 23 |
| `AMBIGUOUS_ID` | 12 |
| `CITED_UNDER_DIFFERENT_ID` | 7 |
| `CITED_BY_OTHER_COMPONENT` | 4 |
<!-- /gonogo:poll -->

Most INCOMPLETE verdicts are **missing links, not missing tests**: F´ has thousands of passing unit tests, but only some
of them name the requirement they verify. That is the gap GoNoGo makes visible.

## Evidence: F´'s unit tests, run in our CI

[`.github/workflows/fprime-unit-tests.yml`](.github/workflows/fprime-unit-tests.yml) checks out `nasa/fprime` at the
dataset's commit, builds and runs its unit tests with F´'s own GitHub Actions, writes one gtest XML report per
executable, and converts them with [`gonogo/testresults.py`](gonogo/testresults.py) into
[`data/fprime_test_results.json`](data/fprime_test_results.json).

<!-- gonogo:ci -->
Run [45468869538](https://github.com/cokacihan/tracegraph/actions/runs/45468869538) built F´ `6fff089e8e` with the official `nasa/fprime-actions` steps and ran 136 unit-test executables: 2665 test cases, 2649 passed, 16 skipped, 0 failed. 2066 results map to test cases in the graph; 599 belong to library directories without a component (e.g. `CFDP/Checksum`) or to parameterised cases GoNoGo does not model. All 393 of 393 requirement-citing test cases ran.
<!-- /gonogo:ci -->

## Change impact

F´ unit tests run per component against an autocoded harness, so GoNoGo applies these rules
([`gonogo/impact.py`](gonogo/impact.py)):

- **A changed component directory**: re-run that component's unit tests.
- **A changed port-type definition**: re-run the unit tests of every component that uses the port type.
- **A changed deployment topology**: listed for integration review only, because it does not change which unit tests break.

Requirements that are GO today and depend on a re-run test are reported as *GO pending re-run*.

<!-- gonogo:impact_sched -->
```text
Unit tests to re-run: 1156 test cases in 27 components
  Drv/PosixUartDriver                         0 cases   uses port:Svc.Sched as run
  Svc/ActivePhaser                            8 cases   uses port:Svc.Sched as PhaserMemberOut
  Svc/ActiveRateGroup                         4 cases   uses port:Svc.Sched as RateGroupMemberOut
  Svc/BufferManager                           5 cases   uses port:Svc.Sched as schedIn
  Svc/Ccsds/CfdpManager                     217 cases   uses port:Svc.Sched as run1Hz
  Svc/CmdDispatcher                          15 cases   uses port:Svc.Sched as run
  Svc/CmdSequencer                           97 cases   uses port:Svc.Sched as schedIn
  Svc/ComAggregator                          30 cases   uses port:Svc.Sched as timeout
  Svc/ComLoggerDp                            29 cases   uses port:Svc.Sched as schedIn
  Svc/ComQueue                               31 cases   uses port:Svc.Sched as run
  Svc/DpManager                              10 cases   uses port:Svc.Sched as schedIn
  Svc/DpWriter                               18 cases   uses port:Svc.Sched as schedIn
  Svc/EventManager                            7 cases   uses port:Svc.Sched as run
  Svc/FileDownlink                            9 cases   uses port:Svc.Sched as Run
  Svc/FileManager                            32 cases   uses port:Svc.Sched as schedIn
  Svc/FpySequencer                          347 cases   uses port:Svc.Sched as tlmWrite, port:Svc.Sched as checkTimers
  Svc/Health                                 12 cases   uses port:Svc.Sched as Run
  Svc/PassiveRateGroup                        5 cases   uses port:Svc.Sched as RateGroupMemberOut
  Svc/SystemResources                         2 cases   uses port:Svc.Sched as run
  Svc/TlmChan                                13 cases   uses port:Svc.Sched as Run
  Svc/TlmPacketizer                          36 cases   uses port:Svc.Sched as Run
  Svc/Version                                 7 cases   uses port:Svc.Sched as schedIn
  Svc/WasmSequencer                         218 cases   uses port:Svc.Sched as writeTelemetry, port:Svc.Sched as checkTimers
  TestDeploymentsProject/Ref/BlockDriver      3 cases   uses port:Svc.Sched as Sched
  TestDeploymentsProject/Ref/DpDemo           0 cases   uses port:Svc.Sched as run
  TestDeploymentsProject/Ref/SendBuffApp      0 cases   uses port:Svc.Sched as SchedIn
  TestDeploymentsProject/Ref/SignalGen        1 cases   uses port:Svc.Sched as schedIn

  requirements depending on these tests: 185; GO today, pending re-run: 61
```
<!-- /gonogo:impact_sched -->

## Findings

These come from repository text at the pinned commit. Each one links to the line, and the main ones were checked by
hand. They are **traceability-hygiene findings** (IDs that do not line up), not statements about flight-code
correctness, and some may turn out to be deliberate conventions. We have not filed them upstream yet.

<!-- gonogo:findings -->
| ID | Where (F´ at the pinned commit) | What |
|---|---|---|
| `SVC-DPMANAGER-005`, `SVC-DPMANAGER-006`, `SVC-DPMANAGER-007` | [Svc/DpWriter/test/ut/DpWriterTestMain.cpp](https://github.com/nasa/fprime/blob/6fff089e8e2035307105cad4b28631eea1552ff6/Svc/DpWriter/test/ut/DpWriterTestMain.cpp) | cited by tests of `Svc/DpWriter`, defined in no SDD table |
| `ISF-HTH-001`, `ISF-HTH-002`, `ISF-HTH-003`, `ISF-HTH-004`, `ISF-HTH-005`, `ISF-HTH-006`, `ISF-HTH-007` | [Svc/Health/test/ut/HealthTester.cpp](https://github.com/nasa/fprime/blob/6fff089e8e2035307105cad4b28631eea1552ff6/Svc/Health/test/ut/HealthTester.cpp) | cited by tests of `Svc/Health`, defined in no SDD table |
| `FH-002` | [Svc/FatalHandler/docs/sdd.md#L14](https://github.com/nasa/fprime/blob/6fff089e8e2035307105cad4b28631eea1552ff6/Svc/FatalHandler/docs/sdd.md#L14)<br>[Svc/FatalHandler/docs/sdd.md#L15](https://github.com/nasa/fprime/blob/6fff089e8e2035307105cad4b28631eea1552ff6/Svc/FatalHandler/docs/sdd.md#L15) | same ID defined 2 times |
| `SVC-CCSDS-SPD-009` | [Svc/Ccsds/SpacePacketDeframer/docs/sdd.md#L47](https://github.com/nasa/fprime/blob/6fff089e8e2035307105cad4b28631eea1552ff6/Svc/Ccsds/SpacePacketDeframer/docs/sdd.md#L47)<br>[Svc/Ccsds/SpacePacketDeframer/docs/sdd.md#L53](https://github.com/nasa/fprime/blob/6fff089e8e2035307105cad4b28631eea1552ff6/Svc/Ccsds/SpacePacketDeframer/docs/sdd.md#L53) | same ID defined 2 times |
| `SVC-COMLOGGER-002` | [Svc/ComLogger/docs/sdd.md#L12](https://github.com/nasa/fprime/blob/6fff089e8e2035307105cad4b28631eea1552ff6/Svc/ComLogger/docs/sdd.md#L12)<br>[Svc/ComLoggerDp/docs/sdd.md#L12](https://github.com/nasa/fprime/blob/6fff089e8e2035307105cad4b28631eea1552ff6/Svc/ComLoggerDp/docs/sdd.md#L12) | same ID defined 2 times |
| `SVC-COMLOGGER-003` | [Svc/ComLogger/docs/sdd.md#L13](https://github.com/nasa/fprime/blob/6fff089e8e2035307105cad4b28631eea1552ff6/Svc/ComLogger/docs/sdd.md#L13)<br>[Svc/ComLoggerDp/docs/sdd.md#L13](https://github.com/nasa/fprime/blob/6fff089e8e2035307105cad4b28631eea1552ff6/Svc/ComLoggerDp/docs/sdd.md#L13) | same ID defined 2 times |
| `SVC-COMLOGGER-004` | [Svc/ComLogger/docs/sdd.md#L14](https://github.com/nasa/fprime/blob/6fff089e8e2035307105cad4b28631eea1552ff6/Svc/ComLogger/docs/sdd.md#L14)<br>[Svc/ComLoggerDp/docs/sdd.md#L14](https://github.com/nasa/fprime/blob/6fff089e8e2035307105cad4b28631eea1552ff6/Svc/ComLoggerDp/docs/sdd.md#L14) | same ID defined 2 times |
| `SVC-COMLOGGER-005` | [Svc/ComLogger/docs/sdd.md#L15](https://github.com/nasa/fprime/blob/6fff089e8e2035307105cad4b28631eea1552ff6/Svc/ComLogger/docs/sdd.md#L15)<br>[Svc/ComLoggerDp/docs/sdd.md#L15](https://github.com/nasa/fprime/blob/6fff089e8e2035307105cad4b28631eea1552ff6/Svc/ComLoggerDp/docs/sdd.md#L15) | same ID defined 2 times |
| `PDB-001`, `PDB-002`, `PDB-003`, `PDB-004` | owned by `Svc/PolyDb` | also cited by unit tests of `Svc/PrmDb` |
| `SVC-COMLOGGER-002`, `SVC-COMLOGGER-003`, `SVC-COMLOGGER-004`, `SVC-COMLOGGER-005` | owned by `Svc/ComLogger` | also cited by unit tests of `Svc/ComLoggerDp` |
| `SVC-DPMANAGER-001`, `SVC-DPMANAGER-002`, `SVC-DPMANAGER-003`, `SVC-DPMANAGER-004` | owned by `Svc/DpManager` | also cited by unit tests of `Svc/DpWriter` |
<!-- /gonogo:findings -->

## Evaluation: planted defects in real F´ sources

[`eval/mutation_eval.py`](eval/mutation_eval.py) copies the F´ checkout and plants one defect at a time in the real
files. After each mutation it re-runs the whole pipeline (text → dataset → Relate → Cypher), checks whether the defect
is reported, and checks whether anything else changed. The expected effect is known by construction, so no question was
written by the author of the rules. The baseline is what a careful engineer gets from `grep` over the same tree; the
exact commands are in the module docstring.

<!-- gonogo:mutation -->
| Planted defect (real F´ sources) | Mutations | GoNoGo detected | grep baseline detected | With collateral |
|---|---|---|---|---|
| `citation_deleted` | 20 | 20 | 20 | 0 |
| `citation_id_drift` | 20 | 20 | 20 | 0 |
| `cross_component_cite` | 20 | 20 | 0 | 0 |
| `duplicate_id` | 20 | 20 | 20 | 1 |
| `method_blanked` | 20 | 20 | 12 | 0 |
| `port_type_use_added` | 20 | 20 | 20 | 0 |

Totals: GoNoGo 120/120, grep 92/120; seed 2026, 958.7 s, every mutation re-runs text → dataset → Relate → Cypher.
Collateral case: `duplicate_id` on `SVC-COMLOGGER-003` also changed `SVC-COMLOGGER-002@Svc/ComLoggerDp`.
<!-- /gonogo:mutation -->

How to read it:
- **grep does well on single-hop hygiene.** Undefined, deleted or duplicated IDs are text problems, and grep finds them.
- **The graph is needed when the answer depends on who owns what.** `cross_component_cite` is an example: grep sees "cited" and cannot tell that the citing test belongs to another component.
- **Joins across requirements, tests, runs and ports also need the graph.** "Which GO requirements lose their evidence if port type `Svc.Sched` changes?" is port type → components → test cases → citations → verdicts: one query here, a custom program otherwise.

## Traceability across F´ releases

Relate is embedded, so a graph per release costs one directory, and `gonogo diff` opens two of them side by side in one
process. The table below covers four real F´ snapshots (generated from [`data/releases/`](data/releases)).

<!-- gonogo:releases -->
| | v3.4.0 (2023-11-29) | v4.0.0 (2025-08-07) | v4.4.2 (2026-11-04) | devel (2026-11-24) |
|---|---|---|---|---|
| Requirements | 139 | 199 | 538 | 561 |
| Cited by own test cases | 49 | 48 | 149 | 150 |
| Cited % | 35.3 | 24.1 | 27.7 | 26.7 |
| Unit-test cases | 297 | 506 | 2127 | 2309 |
| Components | 70 | 88 | 124 | 124 |
| Undefined cited IDs | 10 | 12 | 10 | 10 |
| Duplicate IDs | 1 | 2 | 6 | 6 |
| Cross-component citations | 4 | 8 | 12 | 12 |
<!-- /gonogo:releases -->

## How this project uses Relate

- **Mode: directory.** The store [`data/fprime/`](data/fprime) is a committed Relate directory store. It is opened
  read-only by every command; `python -m gonogo build` recreates it from the dataset and the CI results.
- **Why this mode.** Relate's directory mode checkpoints to compressed Parquet, so this graph stays small enough to
  commit and to rebuild in CI on every change. `gonogo export --single-file out.rl` still produces a portable `.rl`
  snapshot when one file is handier.

<!-- gonogo:modes -->
Committed directory store `data/fprime/`: 1.13 MB. Same graph measured in both modes: directory 1.13 MB, single `.rl` file 20.64 MB.
<!-- /gonogo:modes -->

- **What the graph models.**

  ```mermaid
  graph LR
    Part -- SATISFIES --> Requirement
    Requirement -- VERIFIED_BY --> Verification
    Requirement -- DOCUMENTED_IN --> Document
    TestCase -- CALLS --> TestFunction
    TestFunction -- CALLS --> TestFunction
    TestFunction -- CITES --> Requirement
    TestCase -- CITES --> Requirement
    TestCase -- TESTS --> Part
    TestCase -- "RAN_IN (status)" --> TestRun
    Part -- USES_PORT --> PortType
    Instance -- INSTANCE_OF --> Part
    Instance -- CONNECTS_TO --> Instance
  ```

  - **Part:** a component directory (e.g. `Svc/Health`).
  - **Test code:** stored as a call graph. A gtest `TestCase` calls helper `TestFunction`s, which call further
    helpers. A `CITES` edge sits exactly where `REQUIREMENT("ID")` is written.
  - **Evidence:** whether a test case is evidence for a requirement is not precomputed. Cypher decides it with a
    variable-length path, `EXISTS { MATCH (tc)-[:CALLS*0..8]->(cite_site) }`, and `why` prints the shortest chain.
    Example: `ProcGuardTest -> runProcGuard -> sendBuff -> REQUIREMENT("TLC-001")`.
  - **Instance:** an `Instance` and its `CONNECTS_TO` edges are the Ref deployment, with subtopology imports and port
    aliases resolved.
- **Relate features used.**
  - The embedded Python engine (`relate-db`), with Cypher including `CALL {}` subqueries and `EXISTS {}`.
  - Variable-length paths (`CALLS*0..8`) and `shortestPath()` over the test call graph. We test for existence instead
    of enumerating every path. Enumerating paths through the diamond-shaped helper graphs ran into Relate's 256 MB
    value guard, which is a useful lesson the engine taught us.
  - List comprehensions and map projections.
  - Per-label range indexes. They were added after Relate's own `MissingIndex` notification suggested them.
  - A vector index and the `SEARCH` clause over a lexical embedding (hashed character trigrams, not a neural model),
    used for fuzzy name resolution ("the command dispatcher" → `Svc/CmdDispatcher`).
  - A full-text index.
  - Read-only opens.
  - Two stores in one process for `diff` and `check`.
  - Relate's MCP server.
- **Where the data lives.**
  - [`data/fprime_dataset.json`](data/fprime_dataset.json): repository text turned into nodes and relationships by
    [`gonogo/fprime.py`](gonogo/fprime.py).
  - [`data/fprime_test_results.json`](data/fprime_test_results.json): the CI run.
  - [`data/releases/`](data/releases): older F´ releases.
  - [`data/fprime/`](data/fprime): the Relate store. Its small `.cypherlite.*` marker files are committed on purpose:
    without them Relate does not recognise the directory as an existing store and refuses a read-only open.
    `.gitattributes` marks the store as binary so git never rewrites line endings inside it.

## For agents: MCP

Two read-only MCP servers work on the same store ([`.mcp.json.example`](.mcp.json.example)):

- **`gonogo`** ([`gonogo/mcp_server.py`](gonogo/mcp_server.py), no dependencies) has graph-specific tools: `poll`, `why`,
  `tests_to_rerun` (give it the files you changed), `findings`, `ask`.
- **`relate`** is `relate serve --mcp stdio --database data/fprime --read-only`. It offers raw `get_schema` and
  `read_cypher`.

A coding agent can ask "what do I need to re-run before committing this change to `Svc/Health`?" and get gtest filters
and the requirements whose GO depends on them.

## CI gate

`python -m gonogo check --base <base> --head <head> --files <changed files>` prints a Markdown report and exits 1 on
**FAIL**. The gate says PASS/FAIL, not GO/NO-GO, because those words are reserved for test-backed verdicts. It fails
when a change:
- introduces an undefined cited ID, a duplicate ID or a cross-component citation;
- makes a requirement lose every citing test case of its component;
- adds a requirement without a citing test case to a component whose other requirements are cited. In a component that
  does not use `REQUIREMENT()` at all, the same change is only a warning, so the gate stays quiet on F´'s current
  practice.

It also lists the unit tests to re-run.

### Replayed on real F´ pull requests

The gate was replayed on merged nasa/fprime pull requests, with the base and merge commits imported into two Relate
graphs. [`gate-replay.yml`](.github/workflows/gate-replay.yml) re-checks these outcomes on every push (generated):

<!-- gonogo:replays -->
| nasa/fprime PR | Gate | Why | Status of those requirements at the pinned devel commit |
|---|---|---|---|
| [#5170](https://github.com/nasa/fprime/pull/5170) TlmChan hash improvements | **FAIL** | new requirement without an own citing test case, in a component that cites requirements: TLC-005; new requirement without an own citing test case, in a component that cites requirements: TLC-006 | `TLC-005`: NO_CITING_TEST, `TLC-006`: NO_CITING_TEST |
| [#5291](https://github.com/nasa/fprime/pull/5291) Reject invalid PVN in SpacePacketDeframer | **PASS** | new requirement without an own citing test case (component does not use REQUIREMENT()): SVC-CCSDS-SPD-010 | `SVC-CCSDS-SPD-010`: NO_CITING_TEST |
<!-- /gonogo:replays -->

### Use it on your F´ repository

[`action.yml`](action.yml) is a composite GitHub Action. The replay workflow also runs it end to end against the real
`nasa/fprime` history.

```yaml
on: pull_request
jobs:
  gonogo:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: cokacihan/tracegraph@main
        with:
          base: ${{ github.event.pull_request.base.sha }}
```

## Reproduce from scratch

```bash
git clone https://github.com/nasa/fprime data/raw/fprime
git -C data/raw/fprime checkout 6fff089e8e2035307105cad4b28631eea1552ff6
python -m gonogo import-fprime --repo data/raw/fprime   # -> data/fprime_dataset.json
# run the "F´ unit-test evidence" workflow (workflow_dispatch), download its artifact to data/fprime_test_results.json
python -m gonogo build                                  # -> data/fprime/
python -m eval.mutation_eval --per-class 20             # -> eval/MUTATION_RESULTS.md
python -m gonogo.stats --readme README.md --measure-modes   # refreshes every number in this README
```

## Limitations

- **What GO does not prove.** GO means "cited by tests that passed", not "adequately tested". F´ adopts
  `REQUIREMENT()` only partly, so many INCOMPLETE verdicts are missing links rather than missing tests.
- **Heuristic parsing.** GoNoGo reads Markdown tables, FPP and C++ with heuristics. Same-named test helpers in one
  component are merged, which can over-approximate citations. Parameterised and typed gtests are not modelled.
- **Unit tests only.** Integration and system tests are not run, and only the Ref deployment's topology is modelled.
- **Lexical embedding.** The vector index uses a lexical embedding, chosen so that GoNoGo needs no model download or API key.

## AI usage and licences

- **AI assistance.** GoNoGo was built with the help of Claude Code (Anthropic) for design discussion, code, tests and
  documentation drafts. An NVIDIA-hosted model generated paraphrases for an earlier benchmark that has since been
  removed.
- **Checks.** Every number in this README is generated by [`gonogo/stats.py`](gonogo/stats.py) from data in the
  repository, and the F´ findings were checked by hand against the source files.
- **Runtime.** No LLM runs inside GoNoGo.
- **Licences.** GoNoGo is released under the [MIT licence](LICENSE). Data extracted from F´ is Apache-2.0, ©
  California Institute of Technology, and Relate is Apache-2.0 (see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)).
