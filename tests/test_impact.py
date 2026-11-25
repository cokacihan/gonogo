"""Tests-to-re-run must equal a plain-Python computation over the dataset (independent of Relate)."""
from collections import defaultdict

from gonogo.impact import INCLUDE_HOPS, classify_files, impact


def _includers(fp_data, ripple):
    """Every unit that reaches a ripple unit over INCLUDES edges (breadth-first, reversed)."""
    rev = defaultdict(set)
    for e in fp_data["edges"]:
        if e["type"] == "INCLUDES":
            rev[e["dst"]].add(e["src"])
    seen, frontier = set(), set(ripple)
    for _ in range(INCLUDE_HOPS):
        frontier = {s for f in frontier for s in rev[f]} - seen
        if not frontier:
            break
        seen |= frontier
    return seen


def _python_rerun(fp_data, components, port_types, ripple):
    users = defaultdict(set)
    for e in fp_data["edges"]:
        if e["type"] == "USES_PORT":
            users[e["dst"]].add(e["src"])
    parts = {p["id"] for p in fp_data["nodes"]["Part"]}
    affected = (set(components) | {u for p in port_types for u in users[p]}
                | (_includers(fp_data, ripple) & parts))
    case_ids = {c["id"] for c in fp_data["nodes"]["TestCase"]}
    cases = defaultdict(int)
    for e in fp_data["edges"]:
        if e["type"] == "TESTS" and e["src"] in case_ids and e["dst"] in affected:
            cases[e["dst"]] += 1
    reqs = {e["dst"] for e in fp_data["edges"] if e["type"] == "SATISFIES" and e["src"] in affected}
    return affected, sum(cases.values()), reqs


def test_classify_files(db_norun):
    cls = classify_files(db_norun, ["Svc/Health/Health.cpp", "Svc/Sched/Sched.fpp", "README.md",
                                    "TestDeploymentsProject/Ref/Top/topology.fpp", "Fw/Types/BasicTypes.hpp",
                                    "Os/File.cpp"])
    assert "Svc/Health" in cls["components"] and "Svc/Health" not in cls["ripple"]  # own .cpp stays local
    assert {"Fw/Types", "Os"} <= set(cls["ripple"])  # a header, and library code without an FPP component
    assert "port:Svc.Sched" in cls["port_types"]
    assert cls["unmatched"] == ["README.md"]
    assert cls["topology_files"] == ["TestDeploymentsProject/Ref/Top/topology.fpp"]


def test_impact_matches_python(fp_data, db):
    for files, comps, ports in [([], ["Svc/Health"], []), ([], [], ["port:Svc.Sched"]),
                                ([], ["Svc/CmdDispatcher"], ["port:Svc.Ping"]), (["Svc/TlmChan/TlmChan.cpp"], [], []),
                                (["Fw/Types/BasicTypes.hpp"], [], []), (["Utils/Hash/Hash.hpp"], [], [])]:
        res = impact(db, files=files, components=comps, port_types=ports)
        affected, n_cases, reqs = _python_rerun(fp_data, res["components"], res["port_types"], res["ripple"])
        assert {r["component"] for r in res["rerun"]} == affected, (files, comps, ports)
        assert res["test_cases"] == n_cases
        assert set(res["requirements"]) == reqs


def test_library_header_ripples_through_include_chains(db):
    res = impact(db, files=["Utils/Hash/Hash.hpp"])
    by = {r["component"]: r for r in res["rerun"]}
    assert len(by) > 50, "Utils/Hash reaches most components through Os"
    chain = by["Drv/Ip"]["include_chain"]
    assert chain[0] == "Drv/Ip" and chain[-1] == "Utils/Hash" and len(chain) >= 3
    local = impact(db, files=["Svc/TlmChan/TlmChan.cpp"])
    assert {r["component"] for r in local["rerun"]} == {"Svc/TlmChan"}


def test_topology_is_integration_review_not_unit_tests(db):
    res = impact(db, files=["Svc/CmdDispatcher/CommandDispatcherImpl.cpp"])
    assert {r["component"] for r in res["rerun"]} == {"Svc/CmdDispatcher"}
    assert res["integration_review"], "the dispatcher is connected in the Ref deployment"


def test_config_header_ripples_only_to_its_includers(db):
    res = impact(db, files=["default/config/TlmChanImplCfg.hpp"])
    assert res["input"]["unmatched"] == []
    assert {r["component"] for r in res["rerun"]} == {"Svc/TlmChan"}
    assert res["rerun"][0]["include_chain"] == ["Svc/TlmChan", "default/config/TlmChanImplCfg.hpp"]
