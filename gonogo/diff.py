"""Compare two F´ graphs (two commits or two releases) and gate a change.

Both Relate stores are opened side by side in the same process (embedded engine, no server) and the same
Cypher runs against each; Python only computes the set differences.

`check` is the CI gate. It reports PASS or FAIL (deliberately not GO/NO-GO, which are reserved for
test-backed verdicts) and fails when the head commit:
  * introduces a new hygiene finding (undefined cited ID, duplicate ID, cross-component citation),
  * makes a requirement lose every citing test case of its own component,
  * adds a requirement that no own test case cites.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from gonogo import db as store
from gonogo.impact import impact
from gonogo.queries import run
from gonogo.verdict import CITE_HOPS, verdicts

STATE_CYPHER = f"""
MATCH (p:Part)-[:SATISFIES]->(r:Requirement)
OPTIONAL MATCH (r)<-[:CITES]-(cite_site)-[:TESTS]->(p)
OPTIONAL MATCH (tc:TestCase)-[:TESTS]->(p)
  WHERE cite_site IS NOT NULL AND EXISTS {{ MATCH (tc)-[:CALLS*0..{CITE_HOPS}]->(cite_site) }}
RETURN r.id AS id, p.id AS component, r.text AS text, r.methods AS methods, count(DISTINCT tc) AS own_cites
"""


def norm(component_or_id: str) -> str:
    """Release-independent names: the Ref deployment moved under TestDeploymentsProject/ in 4.x."""
    return component_or_id.replace("TestDeploymentsProject/", "")


def open_any(path: Path):
    """A Relate store (directory or .rl) or a dataset JSON (built into a temporary store)."""
    path = Path(path)
    if path.suffix == ".json":
        tmp = Path(tempfile.mkdtemp(prefix="gonogo_")) / "store"
        store.build(json.loads(path.read_text(encoding="utf-8")), tmp)
        path = tmp
    return store.connect(path)


def snapshot(db) -> dict:
    reqs = {norm(r["id"]): dict(r, component=norm(r["component"])) for r in (x.data() for x in db.execute(STATE_CYPHER))}
    reasons = {norm(r["id"]): sorted(set(r["reasons"]) - {"NOT_EXECUTED"}) for r in verdicts(db, None)}
    comps = {norm(r["id"]) for r in (x.data() for x in db.execute("MATCH (p:Part) RETURN p.id AS id"))}
    ports = {(norm(r["c"]), r["t"]) for r in (x.data() for x in db.execute(
        "MATCH (p:Part)-[:USES_PORT]->(t:PortType) RETURN DISTINCT p.id AS c, t.id AS t"))}
    n_cases = db.execute("MATCH (t:TestCase) RETURN count(t) AS n").single()["n"]
    findings = {
        "unknown_citations": {(norm(r["file"]), u) for r in run(db, "fp_unknown_citations")[0] for u in r["unknown_ids"]},
        "duplicate_ids": {r["id"] for r in run(db, "fp_duplicate_requirement_ids")[0]},
        "cross_component": {(r["id"], norm(r["cited_by"])) for r in run(db, "fp_cross_component_citations")[0]},
    }
    return {"meta": store.meta(db), "reqs": reqs, "reasons": reasons, "components": comps, "ports": ports,
            "test_cases": n_cases, "findings": findings}


def metrics(s: dict) -> dict:
    reqs = s["reqs"].values()
    cited = sum(1 for r in reqs if r["own_cites"] > 0)
    return {"commit": s["meta"].get("source_commit"), "components": len(s["components"]),
            "requirements": len(s["reqs"]), "requirements_cited_by_own_tests": cited,
            "cited_percent": round(100.0 * cited / len(s["reqs"]), 1) if s["reqs"] else 0.0,
            "test_cases": s["test_cases"], "port_uses": len(s["ports"]),
            **{f"findings_{k}": len(v) for k, v in s["findings"].items()}}


def diff(base: dict, head: dict) -> dict:
    b, h = base["reqs"], head["reqs"]
    common = sorted(set(b) & set(h))
    out = {
        "base": metrics(base), "head": metrics(head),
        "components": {"added": sorted(head["components"] - base["components"]),
                       "removed": sorted(base["components"] - head["components"])},
        "requirements": {"added": sorted(set(h) - set(b)), "removed": sorted(set(b) - set(h)),
                         "text_changed": [i for i in common if b[i]["text"] != h[i]["text"]],
                         "methods_changed": [i for i in common if sorted(b[i]["methods"]) != sorted(h[i]["methods"])]},
        "citations": {"gained": [i for i in common if b[i]["own_cites"] == 0 < h[i]["own_cites"]],
                      "lost": [i for i in common if h[i]["own_cites"] == 0 < b[i]["own_cites"]]},
        "status_changed": [{"id": i, "base": base["reasons"].get(i, []), "head": head["reasons"].get(i, [])}
                           for i in common if base["reasons"].get(i) != head["reasons"].get(i)],
        "ports": {"uses_added": sorted(head["ports"] - base["ports"]), "uses_removed": sorted(base["ports"] - head["ports"])},
        "findings": {k: {"new": sorted(head["findings"][k] - base["findings"][k]),
                         "resolved": sorted(base["findings"][k] - head["findings"][k])} for k in head["findings"]},
    }
    out["requirements"]["added_uncited"] = [i for i in out["requirements"]["added"] if h[i]["own_cites"] == 0]
    return out


def check(base_db, head_db, changed_files: list[str] | None = None) -> tuple[dict, str, int]:
    base_s, head_s = snapshot(base_db), snapshot(head_db)
    d = diff(base_s, head_s)
    blocking = []
    for k, v in d["findings"].items():
        blocking += [f"new finding ({k}): {x}" for x in v["new"]]
    blocking += [f"lost every own citing test case: {i}" for i in d["citations"]["lost"]]
    # an uncited new requirement blocks only where the component already cites requirements (practice regression);
    # components that never use REQUIREMENT() get a warning instead, so the gate is not noisy on F´'s current practice
    citing_comps = {r["component"] for r in base_s["reqs"].values() if r["own_cites"] > 0}
    head_reqs = head_s["reqs"]
    warnings = []
    for i in d["requirements"]["added_uncited"]:
        if head_reqs[i]["component"] in citing_comps:
            blocking.append(f"new requirement without an own citing test case, in a component that cites requirements: {i}")
        else:
            warnings.append(f"new requirement without an own citing test case (component does not use REQUIREMENT()): {i}")
    imp = impact(head_db, files=changed_files or [])
    md = render_check(d, blocking, imp, changed_files or [], warnings)
    return {"diff": d, "blocking": blocking, "warnings": warnings, "impact": imp}, md, (1 if blocking else 0)


def render_check(d: dict, blocking: list[str], imp: dict, files: list[str], warnings: list[str] | None = None) -> str:
    status = "FAIL" if blocking else "PASS"
    b, h = d["base"], d["head"]
    lines = [f"## GoNoGo traceability gate: **{status}**", "",
             f"Base `{str(b['commit'])[:10]}` → head `{str(h['commit'])[:10]}`. "
             f"Requirements {b['requirements']} → {h['requirements']}; cited by own tests "
             f"{b['requirements_cited_by_own_tests']} → {h['requirements_cited_by_own_tests']}.", ""]
    if blocking:
        lines += ["### Blocking", ""] + [f"- {x}" for x in blocking] + [""]
    if warnings:
        lines += ["### Warnings", ""] + [f"- {x}" for x in warnings] + [""]
    if files:
        lines += [f"### Unit tests to re-run for {len(files)} changed file(s)", "",
                  f"{imp['test_cases']} test cases in {len(imp['rerun'])} components "
                  f"(GO today and pending re-run: {len(imp['go_pending_rerun'])}).", ""]
        lines += [f"- `{r['component']}`: {r['test_cases']} cases"
                  + ("" if r["changed_directly"] else f" (uses {', '.join(r['via_ports'])})") for r in imp["rerun"][:25]]
        if imp["integration_review"]:
            lines += ["", f"Deployment connections to review (integration, not unit tests): {len(imp['integration_review'])}"]
        lines.append("")
    r = d["requirements"]
    lines += ["### Changes (informational)", "",
              f"- requirements added {len(r['added'])}, removed {len(r['removed'])}, text changed {len(r['text_changed'])}, "
              f"methods changed {len(r['methods_changed'])}",
              f"- citations gained {len(d['citations']['gained'])}, lost {len(d['citations']['lost'])}",
              f"- port uses added {len(d['ports']['uses_added'])}, removed {len(d['ports']['uses_removed'])}",
              f"- findings resolved: " + ", ".join(f"{k} {len(v['resolved'])}" for k, v in d["findings"].items()), "",
              "_GoNoGo reports traceability evidence from repository text; it is decision support, not a flight-readiness authority._"]
    return "\n".join(lines) + "\n"
