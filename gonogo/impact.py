"""Change impact for F´: which unit tests to re-run and which verdicts a change puts back in question.

Rules (F´ unit tests run per component against an autocoded harness):
  * a changed component directory  -> re-run that component's unit tests;
  * a changed port type definition -> re-run the unit tests of every component that uses the port type;
  * deployment connections of affected components are listed for integration review only: a topology
    change does not change which unit tests break.
Requirements whose verdict is GO today and depend on a re-run test become "GO pending re-run".
"""
from __future__ import annotations

TESTS_TO_RERUN_CYPHER = """
MATCH (s:Part)
WHERE s.id IN $components OR EXISTS { MATCH (s)-[:USES_PORT]->(pt:PortType) WHERE pt.id IN $port_types }
OPTIONAL MATCH (s)-[u:USES_PORT]->(pt2:PortType) WHERE pt2.id IN $port_types
WITH s, collect(DISTINCT pt2.id + ' as ' + u.port) AS via_ports
OPTIONAL MATCH (tc:TestCase)-[:TESTS]->(s)
WITH s, via_ports, collect(tc.gtest_filter) AS filters
OPTIONAL MATCH (s)-[:SATISFIES]->(r:Requirement)
RETURN s.id AS component, s.id IN $components AS changed_directly, via_ports,
       size(filters) AS test_cases, filters, collect(r.id) AS requirements
ORDER BY changed_directly DESC, component
"""

INTEGRATION_CYPHER = """
MATCH (i:Instance)-[:INSTANCE_OF]->(p:Part) WHERE p.id IN $components
MATCH (i)-[c:CONNECTS_TO]-(o:Instance)
RETURN i.id AS instance, CASE WHEN startNode(c) = i THEN 'out' ELSE 'in' END AS direction,
       o.id AS peer, c.src_port AS src_port, c.dst_port AS dst_port, c.graph AS graph
ORDER BY instance, direction, peer
"""


def classify_files(db, files: list[str]) -> dict:
    """Map changed repository paths to component directories and port types they define."""
    parts = [r["id"] for r in (x.data() for x in db.execute("MATCH (p:Part) RETURN p.id AS id"))]
    port_defs = [r.data() for r in db.execute(
        "MATCH (t:PortType) WHERE t.defined_in IS NOT NULL RETURN t.id AS id, t.defined_in AS file")]
    by_file: dict[str, list[str]] = {}
    for d in port_defs:
        by_file.setdefault(d["file"], []).append(d["id"])
    comps, ports, topology, unmatched = set(), set(), [], []
    for f in files:
        f = f.replace("\\", "/").lstrip("./")
        hit = False
        if f in by_file:  # a port-type definition file
            ports.update(by_file[f])
            hit = True
        owner = max((p for p in parts if f == p or f.startswith(p + "/")), key=len, default=None)
        if owner and f not in by_file:
            comps.add(owner)
            hit = True
        if f.endswith(("topology.fpp", "instances.fpp")) or "/Top/" in f or "Subtopologies/" in f:
            topology.append(f)
            hit = True
        if not hit:
            unmatched.append(f)
    return {"components": sorted(comps), "port_types": sorted(ports), "topology_files": topology,
            "unmatched": unmatched}


def tests_to_rerun(db, components: list[str], port_types: list[str]) -> list[dict]:
    return [r.data() for r in db.execute(TESTS_TO_RERUN_CYPHER, {"components": components, "port_types": port_types})]


def integration_review(db, components: list[str]) -> list[dict]:
    return [r.data() for r in db.execute(INTEGRATION_CYPHER, {"components": components})]


def impact(db, files: list[str] | None = None, components: list[str] | None = None,
           port_types: list[str] | None = None, verdict_rows: list[dict] | None = None) -> dict:
    cls = classify_files(db, files or [])
    comps = sorted(set(cls["components"]) | set(components or []))
    ports = sorted(set(cls["port_types"]) | {p if p.startswith("port:") else f"port:{p}" for p in (port_types or [])})
    rerun = tests_to_rerun(db, comps, ports)
    affected_reqs = {r for row in rerun for r in row["requirements"]}
    pending = sorted(v["id"] for v in (verdict_rows or []) if v["id"] in affected_reqs and v["verdict"] == "GO")
    return {"input": cls, "components": comps, "port_types": ports, "rerun": rerun,
            "test_cases": sum(r["test_cases"] for r in rerun), "requirements": sorted(affected_reqs),
            "go_pending_rerun": pending, "integration_review": integration_review(db, comps)}
