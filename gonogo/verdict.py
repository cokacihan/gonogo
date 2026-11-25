"""GO / NO-GO / INCOMPLETE for every requirement, decided by one Cypher query over the graph.

A test case "cites" a requirement when a path exists from the test case through its test code to a
REQUIREMENT("ID") macro:  (tc:TestCase)-[:CALLS*0..8]->(x)-[:CITES]->(r:Requirement)
x is the test case itself (0 hops, macro in the TEST body) or a helper function several calls deep.

Definitions (one meaning everywhere):
  GO          every test case of the requirement's own component that cites it PASSED in the selected
              unit-test run, and nothing else blocks it.
  NO-GO       a citing test case of the own component FAILED in that run. Only real failures are NO-GO.
  INCOMPLETE  the graph cannot conclude; the reason codes say why:
    NO_METHOD                  the SDD row names no recognisable verification method
    AMBIGUOUS_ID               the same requirement ID is defined more than once
    CITED_UNDER_DIFFERENT_ID   test code of the component cites an undefined ID ending in this one (ISF-HTH-001)
    CITED_BY_OTHER_COMPONENT   only tests of another component cite it
    NO_CITING_TEST             no test case cites it
    NOT_EXECUTED               a citing test case did not run (or no run is loaded)
    EVIDENCE_OUTSIDE_REPO      a declared method (Inspection, System/Integration Test, ...) leaves no record
                               in the repository; `accept_declared=True` waives this when an own test passed

`reference_verdicts` re-implements the same rules in plain Python over the dataset JSON (breadth-first search
over CALLS), without Relate, so the tests can check the Cypher against an independent computation.
"""
from __future__ import annotations

from collections import Counter, defaultdict

VERDICT_ORDER = {"GO": 0, "INCOMPLETE": 1, "NO-GO": 2}
TEST_METHODS = {"Unit Test", "Test"}
CITE_HOPS = 8  # bound on CALLS hops; the importer records the longest chain the data needs (max_citation_hops)

# Start from the few places where REQUIREMENT("ID") is written (cite_site), then keep the test cases of that
# site's component for which a CALLS path exists. EXISTS stops at the first path; enumerating every path would
# explode on the diamond-shaped call graphs of the test helpers (Relate's 256 MB value guard caught that).
CITING = (f"(r)<-[:CITES]-(cite_site)-[:TESTS]->(site_part:Part)\n"
          f"OPTIONAL MATCH (tc:TestCase)-[:TESTS]->(site_part)\n"
          f"  WHERE EXISTS {{ MATCH (tc)-[:CALLS*0..{CITE_HOPS}]->(cite_site) }}")

VERDICTS_CYPHER = f"""
CALL {{
  MATCH (x:Requirement)
  WITH split(x.id, '@')[0] AS ref, count(*) AS n WHERE n > 1
  RETURN collect(ref) AS dup_refs
}}
MATCH (p:Part)-[:SATISFIES]->(r:Requirement)
WITH dup_refs, p, r, split(r.id, '@')[0] AS ref
OPTIONAL MATCH (r)-[:VERIFIED_BY]->(v:Verification)
WITH dup_refs, p, r, ref, collect(DISTINCT v.method) AS methods
OPTIONAL MATCH {CITING}
WITH dup_refs, p, r, ref, methods, collect(DISTINCT tc) AS citing
UNWIND CASE WHEN size(citing) = 0 THEN [null] ELSE citing END AS tc
OPTIONAL MATCH (tc)-[:TESTS]->(tp:Part)
OPTIONAL MATCH (tc)-[ran:RAN_IN]->(:TestRun {{id: $run}})
WITH dup_refs, p, r, ref, methods,
     collect(DISTINCT CASE WHEN tc IS NULL THEN null
             ELSE {{id: tc.id, own: tp = p, status: coalesce(ran.status, 'NOT_RUN')}} END) AS cites
OPTIONAL MATCH (site)-[:TESTS]->(p)
  WHERE (site:TestCase OR site:TestFunction)
    AND any(u IN site.unknown_citations WHERE u <> ref AND (u ENDS WITH '-' + ref OR u ENDS WITH '_' + ref))
OPTIONAL MATCH (dt:TestCase)-[:TESTS]->(p)
  WHERE site IS NOT NULL AND EXISTS {{ MATCH (dt)-[:CALLS*0..{CITE_HOPS}]->(site) }}
WITH dup_refs, p, r, ref, methods, cites, collect(DISTINCT dt.id) AS drift
WITH p, r, ref, methods, drift, ref IN dup_refs AS ambiguous,
     [c IN cites WHERE c.own] AS own, [c IN cites WHERE NOT c.own] AS other
WITH p, r, methods, drift, ambiguous, own, other,
     [c IN own WHERE c.status = 'FAILED'] AS failed,
     [c IN own WHERE c.status IN ['NOT_RUN', 'SKIPPED']] AS not_run,
     [m IN methods WHERE NOT m IN $test_methods] AS outside
WITH p, r, methods, drift, own, other, failed, [x IN [
       CASE WHEN size(methods) = 0 THEN 'NO_METHOD' END,
       CASE WHEN ambiguous THEN 'AMBIGUOUS_ID' END,
       CASE WHEN size(own) = 0 AND size(drift) > 0 THEN 'CITED_UNDER_DIFFERENT_ID' END,
       CASE WHEN size(own) = 0 AND size(drift) = 0 AND size(other) > 0 THEN 'CITED_BY_OTHER_COMPONENT' END,
       CASE WHEN size(own) = 0 AND size(drift) = 0 AND size(other) = 0 THEN 'NO_CITING_TEST' END,
       CASE WHEN size(failed) = 0 AND size(not_run) > 0 THEN 'NOT_EXECUTED' END,
       CASE WHEN size(outside) > 0 AND NOT ($accept_declared AND size(own) > 0) THEN 'EVIDENCE_OUTSIDE_REPO' END
     ] WHERE x IS NOT NULL] AS reasons
RETURN r.id AS id, p.id AS component, r.text AS text, r.source AS source, methods,
       CASE WHEN size(failed) > 0 THEN 'NO-GO' WHEN size(reasons) = 0 THEN 'GO' ELSE 'INCOMPLETE' END AS verdict,
       CASE WHEN size(failed) > 0 THEN ['FAILED_TEST'] + reasons ELSE reasons END AS reasons,
       [c IN own | c.id + ' ' + c.status] AS own_tests,
       [c IN other | c.id + ' ' + c.status] AS other_tests,
       drift AS drift_tests
ORDER BY component, id
"""

# the evidence path of one requirement: test case -> helper calls -> REQUIREMENT() site
EVIDENCE_PATHS_CYPHER = f"""
MATCH (r:Requirement {{id: $id}})<-[:CITES]-(cite_site)-[:TESTS]->(site_part:Part)
MATCH (tc:TestCase)-[:TESTS]->(site_part)
MATCH path = shortestPath((tc)-[:CALLS*0..{CITE_HOPS}]->(cite_site))
OPTIONAL MATCH (tc)-[ran:RAN_IN]->(:TestRun {{id: $run}})
WITH tc, ran, path ORDER BY length(path)
WITH tc, ran, collect(path)[0] AS shortest
RETURN tc.id AS test_case, coalesce(ran.status, 'NOT_RUN') AS status,
       [n IN nodes(shortest) | coalesce(n.name, n.gtest_filter, n.id)] AS chain, length(shortest) AS calls
ORDER BY test_case
"""


# where a test case cites a near-miss ID (e.g. ISF-HTH-001 for HTH-001): the shortest call chain to that macro
DRIFT_PATH_CYPHER = f"""
MATCH (tc:TestCase {{id: $test_case}})-[:CALLS*0..{CITE_HOPS}]->(site)
WHERE any(u IN site.unknown_citations WHERE u <> $ref AND (u ENDS WITH '-' + $ref OR u ENDS WITH '_' + $ref))
WITH tc, site LIMIT 1
MATCH path = shortestPath((tc)-[:CALLS*0..{CITE_HOPS}]->(site))
RETURN [n IN nodes(path) | coalesce(n.name, n.id)] AS chain,
       [u IN site.unknown_citations WHERE u ENDS WITH '-' + $ref OR u ENDS WITH '_' + $ref][0] AS cited
"""


def drift_path(db, test_case: str, rid: str) -> dict | None:
    row = db.execute(DRIFT_PATH_CYPHER, {"test_case": test_case, "ref": rid.split("@")[0]}).fetchone()
    return row.data() if row else None


def verdicts(db, run: str | None, accept_declared: bool = False) -> list[dict]:
    params = {"run": run, "accept_declared": accept_declared, "test_methods": sorted(TEST_METHODS)}
    return [r.data() for r in db.execute(VERDICTS_CYPHER, params)]


def evidence_paths(db, rid: str, run: str | None) -> list[dict]:
    return [r.data() for r in db.execute(EVIDENCE_PATHS_CYPHER, {"id": rid, "run": run})]


def current_run(db) -> str | None:
    row = db.execute("MATCH (t:TestRun) RETURN t.id AS id ORDER BY id DESC LIMIT 1").fetchone()
    return row["id"] if row else None


def worst(vs: list[str]) -> str:
    return max(vs, key=VERDICT_ORDER.__getitem__) if vs else "INCOMPLETE"


def poll(rows: list[dict], deployment_parts: set[str] | None = None) -> list[dict]:
    """Per-component roll-up: counts and the component verdict (GO only if every requirement is GO)."""
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[r["component"]].append(r)
    out = []
    for comp, rs in sorted(by.items()):
        c = Counter(r["verdict"] for r in rs)
        reasons = Counter(x for r in rs for x in r["reasons"])
        out.append({"component": comp, "requirements": len(rs), "GO": c["GO"], "INCOMPLETE": c["INCOMPLETE"],
                    "NO-GO": c["NO-GO"], "verdict": worst([r["verdict"] for r in rs]),
                    "top_reasons": [k for k, _ in reasons.most_common(3)],
                    "in_deployment": deployment_parts is not None and comp in deployment_parts})
    return out


def deployment_parts(db) -> set[str]:
    return {r["id"] for r in (x.data() for x in db.execute(
        "MATCH (:Instance)-[:INSTANCE_OF]->(p:Part) RETURN DISTINCT p.id AS id"))}


# ---------------------------------------------------------------- independent reference (no Relate)
def reach(dataset: dict, hops: int = CITE_HOPS) -> dict[str, set[str]]:
    """For every test case: the nodes reachable over CALLS within `hops` hops, the test case included."""
    calls: dict[str, set[str]] = defaultdict(set)
    for e in dataset["edges"]:
        if e["type"] == "CALLS":
            calls[e["src"]].add(e["dst"])
    out = {}
    for c in dataset["nodes"].get("TestCase", []):
        seen, frontier = {c["id"]}, {c["id"]}
        for _ in range(hops):
            frontier = {n for f in frontier for n in calls[f]} - seen
            if not frontier:
                break
            seen |= frontier
        out[c["id"]] = seen
    return out


def case_citations(dataset: dict, hops: int = CITE_HOPS) -> dict[str, set[str]]:
    """Requirement IDs (defined ones) each test case cites through its test code."""
    cites: dict[str, set[str]] = defaultdict(set)
    for e in dataset["edges"]:
        if e["type"] == "CITES":
            cites[e["src"]].add(e["dst"])
    return {cid: {r for n in nodes for r in cites[n]} for cid, nodes in reach(dataset, hops).items()}


def reference_verdicts(dataset: dict, results: dict | None, accept_declared: bool = False) -> dict[str, dict]:
    nodes = dataset["nodes"]
    reqs = {r["id"]: r for r in nodes["Requirement"]}
    vmethod = {v["id"]: v["method"] for v in nodes.get("Verification", [])}
    case_ids = {c["id"] for c in nodes.get("TestCase", [])}
    unknown = {x["id"]: x.get("unknown_citations", []) for k in ("TestCase", "TestFunction") for x in nodes.get(k, [])}
    comp_of, methods, owner_of_test = {}, defaultdict(set), {}
    for e in dataset["edges"]:
        if e["type"] == "SATISFIES":
            comp_of[e["dst"]] = e["src"]
        elif e["type"] == "VERIFIED_BY":
            methods[e["src"]].add(vmethod[e["dst"]])
        elif e["type"] == "TESTS" and e["src"] in unknown:
            owner_of_test[e["src"]] = e["dst"]
    reached = reach(dataset)
    cites_by_case = case_citations(dataset)
    citing: dict[str, set[str]] = defaultdict(set)
    for cid, rs in cites_by_case.items():
        for r in rs:
            citing[r].add(cid)
    status = {c["id"]: c["status"] for c in (results or {}).get("cases", [])}
    refs = Counter(rid.split("@")[0] for rid in reqs)
    out = {}
    for rid in reqs:
        comp, ref = comp_of[rid], rid.split("@")[0]
        own = [c for c in citing[rid] if owner_of_test.get(c) == comp]
        other = [c for c in citing[rid] if owner_of_test.get(c) != comp]
        st = {c: status.get(c, "NOT_RUN") for c in own}
        failed = [c for c in own if st[c] == "FAILED"]
        not_run = [c for c in own if st[c] in ("NOT_RUN", "SKIPPED")]
        sites = {n for n, us in unknown.items() if owner_of_test.get(n) == comp
                 and any(u != ref and (u.endswith("-" + ref) or u.endswith("_" + ref)) for u in us)}
        drift = [c for c in case_ids if reached[c] & sites]
        ms = methods[rid]
        outside = [m for m in ms if m not in TEST_METHODS]
        reasons = []
        if not ms:
            reasons.append("NO_METHOD")
        if refs[ref] > 1:
            reasons.append("AMBIGUOUS_ID")
        if not own and drift:
            reasons.append("CITED_UNDER_DIFFERENT_ID")
        if not own and not drift and other:
            reasons.append("CITED_BY_OTHER_COMPONENT")
        if not own and not drift and not other:
            reasons.append("NO_CITING_TEST")
        if not failed and not_run:
            reasons.append("NOT_EXECUTED")
        if outside and not (accept_declared and own):
            reasons.append("EVIDENCE_OUTSIDE_REPO")
        verdict = "NO-GO" if failed else ("GO" if not reasons else "INCOMPLETE")
        out[rid] = {"verdict": verdict, "reasons": (["FAILED_TEST"] if failed else []) + reasons, "component": comp}
    return out
