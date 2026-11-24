"""F´ importer: parser unit tests on small fixtures + graph queries vs. independent Python checks."""
import json
from collections import defaultdict

import pytest

from tracegraph import fprime
from tracegraph.db import FPRIME_DATASET, build, connect
from tracegraph.queries import answer_ids, run


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


def _write(tmp_path, name, text):
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


# ------------------------------------------------------------------ real dataset vs graph
@pytest.fixture(scope="module")
def fp_data():
    if not FPRIME_DATASET.exists():
        pytest.skip("data/fprime_dataset.json missing (run: python -m tracegraph.fprime)")
    return json.loads(FPRIME_DATASET.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def fp_db(fp_data, tmp_path_factory):
    path = tmp_path_factory.mktemp("fp") / "fprime.rl"
    build(fp_data, path)
    conn = connect(path)
    yield conn
    conn.close()


def _edges(data, t):
    return [e for e in data["edges"] if e["type"] == t]


def test_counts_round_trip(fp_data, fp_db):
    n = fp_db.execute("MATCH (n:Entity) RETURN count(n) AS n").single()["n"]
    assert n == sum(len(v) for v in fp_data["nodes"].values())
    assert fp_db.execute("MATCH ()-[r]->() RETURN count(r) AS n").single()["n"] == len(fp_data["edges"])


def test_unit_test_without_citation(fp_data, fp_db):
    evidenced = {e["src"] for e in _edges(fp_data, "EVIDENCED_BY")}
    ut = {v["id"] for v in fp_data["nodes"]["Verification"] if v["method"] == "Unit Test"}
    req_of = {e["dst"]: e["src"] for e in _edges(fp_data, "VERIFIED_BY")}
    expected = {req_of[v] for v in ut - evidenced}
    rows, _, _ = run(fp_db, "fp_unit_test_without_citation")
    assert answer_ids("fp_unit_test_without_citation", rows) == expected


def test_unknown_citations_and_duplicates(fp_data, fp_db):
    exp_unknown = {e["dst"] for e in fp_data["diagnostics"]["dangling_citations"]}
    assert answer_ids("fp_unknown_citations", run(fp_db, "fp_unknown_citations")[0]) == exp_unknown
    exp_dup = {d["id"] for d in fp_data["diagnostics"]["duplicate_requirement_ids"]}
    assert answer_ids("fp_duplicate_requirement_ids", run(fp_db, "fp_duplicate_requirement_ids")[0]) == exp_dup


def test_component_impact_matches_python_bfs(fp_data, fp_db):
    inst_of = {e["src"]: e["dst"] for e in _edges(fp_data, "INSTANCE_OF")}
    out = defaultdict(set)
    for e in _edges(fp_data, "CONNECTS_TO"):
        out[e["src"]].add(e["dst"])
    sat = defaultdict(set)
    for e in _edges(fp_data, "SATISFIES"):
        sat[e["src"]].add(e["dst"])
    ver = defaultdict(set)
    for e in _edges(fp_data, "VERIFIED_BY"):
        ver[e["src"]].add(e["dst"])
    for part in ["Svc/RateGroupDriver", "Svc/ActiveRateGroup", "Svc/CmdDispatcher", "Svc/Health"]:
        srcs = {i for i, p in inst_of.items() if p == part}
        frontier, seen = set(srcs), set()
        for _ in range(2):  # FP_IMPACT_HOPS
            frontier = {d for s in frontier for d in out[s]}
            seen |= frontier
        downstream = seen - srcs
        parts = {inst_of[d] for d in downstream if d in inst_of} - {part}
        reqs = {r for p in parts | {part} for r in sat[p]}
        vers = {v for r in reqs for v in ver[r]}
        (row,) = run(fp_db, "fp_component_impact", id=part)[0]
        assert set(row["downstream_instances"]) == downstream, part
        assert set(row["parts"]) == parts, part
        assert set(row["requirements"]) == reqs, part
        assert set(row["verifications"]) == vers, part


# ------------------------------------------------------------------ agent on the F´ store
def test_agent_routes_fprime_questions(fp_db):
    from tracegraph.agent import Agent
    ag = Agent(fp_db)
    assert ag.kind == "fprime"
    cases = {
        "What is affected if Svc/RateGroupDriver changes?": "fp_component_impact",
        "What breaks if Svc.ActiveRateGroup is modified?": "fp_component_impact",
        "Which unit tests cite requirement IDs that no SDD defines?": "fp_unknown_citations",
        "Are there duplicate requirement IDs?": "fp_duplicate_requirement_ids",
        "Which unit-test requirements are not cited by any test?": "fp_unit_test_without_citation",
        "How is HTH-003 verified?": "fp_requirement_evidence",
        "What is rateGroupDriverComp connected to?": "fp_instance_neighbours",
    }
    for q, tmpl in cases.items():
        a = ag.ask(q)
        assert a.status == "answered" and a.template == tmpl, (q, a.template)
        assert a.cypher and a.rows is not None


def test_agent_fprime_abstains_on_unknown_ids(fp_db):
    from tracegraph.agent import Agent
    ag = Agent(fp_db)
    for q in ["How is ABC-999 verified?", "What is affected if Svc.NoSuchThing changes?", "What is the launch date?"]:
        a = ag.ask(q)
        assert a.status == "unknown" and a.answer_ids == [], q
