"""F´ importer: parser unit tests on small fixtures + graph queries vs. independent Python checks."""
import json
from collections import defaultdict

import pytest

from gonogo import fprime
from gonogo.db import build, connect
from gonogo.queries import answer_ids, run


# ------------------------------------------------------------------ parser fixtures
SDD_PLAIN = """# Svc::X
## 2. Requirements

Requirement | Description | Verification Method
----------- | ----------- | -------------------
ARG-001 | The component shall be active | Inspection, Unit test
ARG-002 | The component shall invoke ports | Unit Test

## 3. Design
| NOT-001 | not a requirement table | Unit Test |
"""
SDD_RATIONALE = """## Requirements
| Requirement      | Description | Rationale | Verification Method |
|------------------|-------------|-----------|---------------------|
| SVC-COMQUEUE-001 | queue things | needed, really | Unit Test |
| SVC_CHRONO_TIME_002 | time | because | Unit-Test |
"""
SDD_FREE_TEXT = """## Requirements
| Name | Description | Validation |
|---|---|---|
|SVC-VERSION-001| emit version | This is to provide transparency, on demand|
"""


def _write(tmp_path, name, text=""):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_sdd_plain_table(tmp_path):
    rows = fprime.parse_sdd(_write(tmp_path, "a.md", SDD_PLAIN))
    assert [r["id"] for r in rows] == ["ARG-001", "ARG-002"]  # table outside the section is ignored
    assert rows[0]["methods"] == ["Inspection", "Unit Test"]


def test_sdd_rationale_and_underscore_ids(tmp_path):
    rows = fprime.parse_sdd(_write(tmp_path, "b.md", SDD_RATIONALE))
    assert [r["id"] for r in rows] == ["SVC-COMQUEUE-001", "SVC_CHRONO_TIME_002"]
    assert rows[0]["rationale"] == "needed, really" and rows[0]["methods"] == ["Unit Test"]
    assert rows[1]["methods"] == ["Unit Test"]


def test_sdd_free_text_method_is_not_a_method(tmp_path):
    (row,) = fprime.parse_sdd(_write(tmp_path, "c.md", SDD_FREE_TEXT))
    assert row["methods"] == [] and "transparency" in row["methods_raw"]


def test_topology_import_and_alias_resolution(tmp_path):
    _write(tmp_path, "TestDeploymentsProject/Ref/Top/instances.fpp",
           "module Ref {\n  instance drv: Svc.Driver base id 0x1\n  instance rg: Svc.Group base id 0x2\n}\n")
    _write(tmp_path, "TestDeploymentsProject/Ref/Top/topology.fpp",
           "module Ref {\n deployment topology Ref {\n  instance Core.Subtopology\n  instance drv\n  instance rg\n"
           "  connections A {\n   drv.CycleOut[0] -> rg.CycleIn\n   rg.Out[1] -> Core.Subtopology.runIn\n  }\n }\n}\n")
    _write(tmp_path, "Svc/Subtopologies/Core/Core.fpp",
           "module Core {\n instance disp: Svc.Disp base id 0x3\n instance q: Svc.Queue base id 0x4\n"
           " topology Inner {\n  instance q\n  port qIn = q.run\n }\n"
           " topology Subtopology {\n  import Inner\n  instance disp\n"
           "  connections B {\n   disp.out -> Inner.qIn\n  }\n  port runIn = disp.run\n }\n}\n")
    topo = fprime.ref_topology(tmp_path)
    got = {(c["src"], c["src_port"], c["dst"], c["dst_port"]) for c in topo["connections"]}
    assert got == {("drv", "CycleOut", "rg", "CycleIn"), ("rg", "Out", "Core.disp", "run"),
                   ("Core.disp", "out", "Core.q", "run")}
    assert topo["unresolved"] == []
    assert set(topo["instances"]) == {"drv", "rg", "Core.disp", "Core.q"}

SDD_HELPER_CITATION = '''
void Tester ::runNominal() {
    helper();
}
void Tester ::helper() {
    REQUIREMENT("ABC-001");  // brace in a string "{" must not confuse depth
}
TEST(Nominal, Run) {
    Tester tester;
    tester.runNominal();
}
TEST(Nominal, Nothing) {
    Tester tester;
}
'''


def test_test_case_citations_follow_helper_calls(tmp_path):
    comp = tmp_path / "Svc" / "X"
    _write(tmp_path, "Svc/X/test/ut/Tester.cpp", SDD_HELPER_CITATION)
    cases = {c["name"]: c for c in fprime.test_cases(comp, tmp_path)}
    assert cases["Run"]["cited"] == ["ABC-001"]
    assert cases["Nothing"]["cited"] == []


# ------------------------------------------------------------------ real dataset vs graph
def _edges(data, t):
    return [e for e in data["edges"] if e["type"] == t]


def test_counts_round_trip(fp_data, db_norun):
    n = db_norun.execute("MATCH (n:Entity) RETURN count(n) AS n").single()["n"]
    assert n == sum(len(v) for v in fp_data["nodes"].values())
    assert db_norun.execute("MATCH ()-[r]->() RETURN count(r) AS n").single()["n"] == len(fp_data["edges"])


def test_case_level_citations_cover_file_level(fp_data):
    from gonogo.verdict import case_citations
    reqs = {r["id"] for r in fp_data["nodes"]["Requirement"]}
    by_case = {r for rs in case_citations(fp_data).values() for r in rs}
    by_file = {r for f in fp_data["nodes"]["TestFile"] for r in f["cited_requirements"] if r in reqs}
    assert by_file <= by_case


def test_call_chains_fit_the_query_bound(fp_data):
    from gonogo.verdict import CITE_HOPS
    assert 0 < fp_data["diagnostics"]["max_citation_hops"] <= CITE_HOPS
    assert fp_data["nodes"]["TestFunction"] and any(e["type"] == "CALLS" for e in fp_data["edges"])


def test_evidence_path_is_a_graph_path(db):
    from gonogo.verdict import current_run, drift_path, evidence_paths
    paths = {p["test_case"]: p for p in evidence_paths(db, "TLC-001", current_run(db))}
    p = paths["Svc/TlmChan::TlmChanTest.NominalChannelTest"]
    assert p["chain"][0] == "NominalChannelTest" and p["chain"][-1] == "sendBuff" and p["calls"] == len(p["chain"]) - 1
    d = drift_path(db, "Svc/Health::Test.NominalTlm", "HTH-001")
    assert d["cited"] == "ISF-HTH-001" and d["chain"][0] == "NominalTlm"


def test_unknown_citations_and_duplicates(fp_data, db_norun):
    exp_unknown = {e["dst"] for e in fp_data["diagnostics"]["dangling_citations"]}
    assert answer_ids("fp_unknown_citations", run(db_norun, "fp_unknown_citations")[0]) == exp_unknown
    exp_dup = {d["id"] for d in fp_data["diagnostics"]["duplicate_requirement_ids"]}
    assert answer_ids("fp_duplicate_requirement_ids", run(db_norun, "fp_duplicate_requirement_ids")[0]) == exp_dup


def test_cross_component_citations(fp_data, db_norun):
    from gonogo.verdict import case_citations
    owner = {e["dst"]: e["src"] for e in _edges(fp_data, "SATISFIES")}
    case_comp = {e["src"]: e["dst"] for e in _edges(fp_data, "TESTS") if "::" in e["src"]}
    exp = {r for c, rs in case_citations(fp_data).items() for r in rs if case_comp[c] != owner[r]}
    assert answer_ids("fp_cross_component_citations", run(db_norun, "fp_cross_component_citations")[0]) == exp


def test_known_real_findings_present(fp_data):
    """Hand-checked against the F´ sources at the pinned commit (see README, 'Findings')."""
    unknown = {e["dst"] for e in fp_data["diagnostics"]["dangling_citations"]}
    assert {f"ISF-HTH-00{i}" for i in range(1, 8)} <= unknown
    dups = {d["id"] for d in fp_data["diagnostics"]["duplicate_requirement_ids"]}
    assert {"FH-002", "SVC-CCSDS-SPD-009"} <= dups


def test_include_resolution_follows_fprime_include_paths(tmp_path):
    _write(tmp_path, "Svc/A/A.hpp", '#include <config/ACfg.hpp>\n#include <BCfg/B.hpp>\n#include "Svc/B/BComponentAc.hpp"\n'
                                    '#include <Utils/Types/Queue.hpp>\n#include <vector>\n#include "Local.hpp"\n')
    _write(tmp_path, "Svc/A/Local.hpp")
    _write(tmp_path, "Svc/B/B.hpp")  # its *Ac.hpp is generated at build time and absent from the checkout
    _write(tmp_path, "Svc/B/config/BCfg/B.hpp")
    _write(tmp_path, "default/config/ACfg.hpp")
    _write(tmp_path, "Utils/Types/Queue.hpp", "#include <Utils/Types/CircularBuffer.hpp>\n")
    _write(tmp_path, "Utils/Types/CircularBuffer.hpp")
    libs, edges, stats = fprime.include_graph(tmp_path, {"Svc/A", "Svc/B"})
    got = {(e["src"], e["dst"]) for e in edges}
    assert got == {("Svc/A", "default/config/ACfg.hpp"),  # a configuration header is its own unit
                   ("Svc/A", "Svc/B"),                     # component-local <BCfg/...> and an autocoded header
                   ("Svc/A", "Utils/Types")}               # library directory; Queue -> CircularBuffer stays inside it
    assert libs == {"default/config/ACfg.hpp", "Utils/Types"}
    assert stats["external_or_same_dir"] == 2  # <vector> and the same-directory "Local.hpp"
