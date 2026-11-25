"""Change impact for F´: which unit tests to re-run and which verdicts a change puts back in question.

Rules (F´ unit tests run per component against an autocoded harness):
  * a changed component directory          -> re-run that component's unit tests;
  * a changed header, or any change in library code (a directory that is not a component, e.g. Fw/Types)
                                            -> also re-run the tests of every component that includes it,
                                               directly or through other units: (s)-[:INCLUDES*1..12]->(changed);
  * a changed port type definition         -> re-run the unit tests of every component that uses the port type;
  * deployment connections of affected components are listed for integration review only: a topology change
    does not change which unit tests break.
A component's own .cpp file stays local: its interface did not change. A component named without files is
treated as if its headers changed (the safe assumption), and the answer says so.
Requirements whose verdict is GO today and depend on a re-run test become "GO pending re-run".
"""
from __future__ import annotations

INCLUDE_HOPS = 12
HEADER_SUFFIXES = (".hpp", ".h", ".hh")

# shortestPath (breadth-first, one path) rather than EXISTS over a variable-length pattern: the include graph is
# dense (Fw is included by most units), and proving that NO path exists by enumerating paths is exponential.
# The shortest chain is also the explanation shown to the user.
TESTS_TO_RERUN_CYPHER = f"""
MATCH (s:Part)
CALL {{
  WITH s
  MATCH (h) WHERE h.id IN $ripple AND h <> s
  MATCH chain = shortestPath((s)-[:INCLUDES*1..{INCLUDE_HOPS}]->(h))
  RETURN collect(chain) AS chains
}}
WITH s, chains, EXISTS {{ MATCH (s)-[:USES_PORT]->(pt:PortType) WHERE pt.id IN $port_types }} AS uses_changed_port
WHERE s.id IN $components OR uses_changed_port OR size(chains) > 0
OPTIONAL MATCH (s)-[u:USES_PORT]->(pt2:PortType) WHERE pt2.id IN $port_types
WITH s, chains, collect(DISTINCT pt2.id + ' as ' + u.port) AS via_ports
OPTIONAL MATCH (tc:TestCase)-[:TESTS]->(s)
WITH s, chains, via_ports, collect(tc.gtest_filter) AS filters
OPTIONAL MATCH (s)-[:SATISFIES]->(r:Requirement)
RETURN s.id AS component, s.id IN $components AS changed_directly, via_ports, size(chains) > 0 AS via_includes,
       CASE WHEN size(chains) > 0 THEN [n IN nodes(chains[0]) | n.id] ELSE [] END AS include_chain,
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
    """Map changed repository paths to changed components, units whose change ripples to includers,
    and port types they define."""
    part_rows = [x.data() for x in db.execute("MATCH (p:Part) RETURN p.id AS id, size(p.types) > 0 AS component")]
    parts = {r["id"] for r in part_rows}
    # directories without an FPP component (Os, Fw/Types, ...) are libraries even when they have an SDD:
    # any change in them can reach every user
    libs = {r["id"] for r in (x.data() for x in db.execute("MATCH (l:Library) RETURN l.id AS id"))}
    libs |= {r["id"] for r in part_rows if not r["component"]}
    port_defs = [r.data() for r in db.execute(
        "MATCH (t:PortType) WHERE t.defined_in IS NOT NULL RETURN t.id AS id, t.defined_in AS file")]
    by_file: dict[str, list[str]] = {}
    for d in port_defs:
        by_file.setdefault(d["file"], []).append(d["id"])
    comps, ripple, ports, topology, unmatched = set(), set(), set(), [], []
    for f in files:
        f = f.strip().replace("\\", "/").removeprefix("./")
        if not f:
            continue
        hit = False
        if f in by_file:  # a port-type definition file
            ports.update(by_file[f])
            hit = True
        owner = max((u for u in parts | libs if f == u or f.startswith(u + "/")), key=len, default=None)
        if owner and f not in by_file:
            if owner in parts:
                comps.add(owner)
            if owner in libs or f.endswith(HEADER_SUFFIXES):
                ripple.add(owner)
            hit = True
        if f.endswith(("topology.fpp", "instances.fpp")) or "/Top/" in f or "Subtopologies/" in f:
            topology.append(f)
            hit = True
        if not hit:
            unmatched.append(f)
    return {"components": sorted(comps), "ripple": sorted(ripple), "port_types": sorted(ports),
            "topology_files": topology, "unmatched": unmatched}


def reason(row: dict) -> str:
    """Why a component is in the re-run set, in the words the CLI, the gate report and the explorer share."""
    if row["changed_directly"]:
        return "changed"
    if row["via_ports"]:
        return "uses " + ", ".join(row["via_ports"])
    return "includes " + " -> ".join(row["include_chain"][1:])


def tests_to_rerun(db, components: list[str], port_types: list[str], ripple: list[str]) -> list[dict]:
    return [r.data() for r in db.execute(TESTS_TO_RERUN_CYPHER,
                                         {"components": components, "port_types": port_types, "ripple": ripple})]


def integration_review(db, components: list[str]) -> list[dict]:
    return [r.data() for r in db.execute(INTEGRATION_CYPHER, {"components": components})]


def impact(db, files: list[str] | None = None, components: list[str] | None = None,
           port_types: list[str] | None = None, verdict_rows: list[dict] | None = None) -> dict:
    cls = classify_files(db, files or [])
    named = sorted(set(components or []))
    comps = sorted(set(cls["components"]) | set(named))
    ripple = sorted(set(cls["ripple"]) | set(named))  # named without files: assume the interface may change
    ports = sorted(set(cls["port_types"]) | {p if p.startswith("port:") else f"port:{p}" for p in (port_types or [])})
    rerun = tests_to_rerun(db, comps, ports, ripple)
    affected_reqs = {r for row in rerun for r in row["requirements"]}
    pending = sorted(v["id"] for v in (verdict_rows or []) if v["id"] in affected_reqs and v["verdict"] == "GO")
    return {"input": cls, "components": comps, "ripple": ripple, "port_types": ports, "rerun": rerun,
            "assumed_interface_change": named,
            "test_cases": sum(r["test_cases"] for r in rerun), "requirements": sorted(affected_reqs),
            "go_pending_rerun": pending, "integration_review": integration_review(db, comps)}
