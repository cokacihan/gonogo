"""Tests-to-re-run must equal a plain-Python computation over the dataset."""
from collections import defaultdict

from gonogo.impact import classify_files, impact


def _python_rerun(fp_data, components, port_types):
    users = defaultdict(set)
    for e in fp_data["edges"]:
        if e["type"] == "USES_PORT":
            users[e["dst"]].add(e["src"])
    affected = set(components) | {u for p in port_types for u in users[p]}
    case_ids = {c["id"] for c in fp_data["nodes"]["TestCase"]}
    cases = defaultdict(int)
    for e in fp_data["edges"]:
        if e["type"] == "TESTS" and e["src"] in case_ids and e["dst"] in affected:
            cases[e["dst"]] += 1
    reqs = {e["dst"] for e in fp_data["edges"] if e["type"] == "SATISFIES" and e["src"] in affected}
    return affected, sum(cases.values()), reqs


def test_classify_files(db_norun):
    cls = classify_files(db_norun, ["Svc/Health/Health.cpp", "Svc/Sched/Sched.fpp", "README.md",
                                    "TestDeploymentsProject/Ref/Top/topology.fpp"])
    assert "Svc/Health" in cls["components"]
    assert "port:Svc.Sched" in cls["port_types"]
    assert cls["unmatched"] == ["README.md"]
    assert cls["topology_files"] == ["TestDeploymentsProject/Ref/Top/topology.fpp"]


def test_component_and_port_type_impact_match_python(fp_data, db):
    for comps, ports in [(["Svc/Health"], []), ([], ["port:Svc.Sched"]), (["Svc/CmdDispatcher"], ["port:Svc.Ping"])]:
        res = impact(db, components=comps, port_types=ports)
        affected, n_cases, reqs = _python_rerun(fp_data, comps, ports)
        assert {r["component"] for r in res["rerun"]} == affected
        assert res["test_cases"] == n_cases
        assert set(res["requirements"]) == reqs


def test_topology_is_integration_review_not_unit_tests(db):
    res = impact(db, components=["Svc/CmdDispatcher"])
    assert {r["component"] for r in res["rerun"]} == {"Svc/CmdDispatcher"}
    assert res["integration_review"], "the dispatcher is connected in the Ref deployment"
