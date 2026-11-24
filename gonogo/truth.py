"""Ground truth computed in plain Python from the dataset dict.

This module deliberately does NOT use Relate or Cypher. The evaluation compares answers
produced through the graph database against these independently computed sets.
"""
from __future__ import annotations

from collections import defaultdict

HIGH_RISK_SCORE = 12


class Index:
    def __init__(self, data: dict):
        self.nodes = {n["id"]: {**n, "label": lbl} for lbl, ns in data["nodes"].items() for n in ns}
        self.out: dict[tuple[str, str], list[str]] = defaultdict(list)
        self.inc: dict[tuple[str, str], list[str]] = defaultdict(list)
        for e in data["edges"]:
            self.out[(e["src"], e["type"])].append(e["dst"])
            self.inc[(e["dst"], e["type"])].append(e["src"])

    def ids(self, label: str) -> list[str]:
        return sorted(i for i, n in self.nodes.items() if n["label"] == label)

    def closure(self, start: str, rel: str, reverse: bool) -> set[str]:
        """All nodes reachable from start over rel (1..n hops), excluding start."""
        adj = self.inc if reverse else self.out
        seen: set[str] = set()
        stack = [start]
        while stack:
            cur = stack.pop()
            for nxt in adj.get((cur, rel), []):
                if nxt not in seen and nxt != start:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen


def _reach_incl_start(ix: Index, start: str, rel: str) -> set[str]:
    """Nodes reachable in >= 1 hop, where start itself counts if a cycle returns to it."""
    seen: set[str] = set()
    stack = [start]
    while stack:
        for nxt in ix.out.get((stack.pop(), rel), []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def part_impact(ix: Index, pid: str) -> dict:
    parts = {pid} | ix.closure(pid, "DEPENDS_ON", reverse=True)
    reqs: set[str] = set()
    for p in parts:
        for r in ix.out.get((p, "SATISFIES"), []):
            reqs |= {r} | ix.closure(r, "DERIVES_FROM", reverse=False)
    vers = {v for r in reqs for v in ix.out.get((r, "VERIFIED_BY"), [])}
    others = parts - {pid}
    return {"parts": sorted(others), "requirements": sorted(reqs), "verifications": sorted(vers),
            "blast_radius": len(others) + len(reqs) + len(vers)}


def requirement_impact(ix: Index, rid: str) -> dict:
    reqs = {rid} | ix.closure(rid, "DERIVES_FROM", reverse=True)
    parts = {p for r in reqs for p in ix.inc.get((r, "SATISFIES"), [])}
    vers = {v for r in reqs for v in ix.out.get((r, "VERIFIED_BY"), [])}
    children = reqs - {rid}
    return {"requirements": sorted(children), "parts": sorted(parts), "verifications": sorted(vers),
            "blast_radius": len(children) + len(parts) + len(vers)}


def ground_truth(data: dict) -> dict:
    ix = Index(data)
    reqs, parts = ix.ids("Requirement"), ix.ids("Part")
    vstatus = {v: ix.nodes[v]["status"] for v in ix.ids("Verification")}
    vmethod = {v: ix.nodes[v]["method"] for v in ix.ids("Verification")}
    vers_of = {r: ix.out.get((r, "VERIFIED_BY"), []) for r in reqs}

    unverified = sorted(r for r in reqs if not vers_of[r])
    failed = sorted(r for r in reqs if any(vstatus[v] == "FAILED" for v in vers_of[r]))
    open_reqs = sorted(r for r in reqs if not any(vstatus[v] == "PASSED" for v in vers_of[r]))
    analysis_only = sorted(r for r in reqs if vers_of[r] and all(vmethod[v] == "Analysis" for v in vers_of[r]))

    closure: dict[str, dict] = {}
    for sub in sorted({ix.nodes[r]["subsystem"] for r in reqs}):
        members = [r for r in reqs if ix.nodes[r]["subsystem"] == sub]
        closed = [r for r in members if r not in open_reqs]
        closure[sub] = {"total": len(members), "closed": len(closed),
                        "percent": round(100.0 * len(closed) / len(members), 1)}

    orphan = sorted(p for p in parts if not ix.out.get((p, "SATISFIES")) and not ix.inc.get((p, "DEPENDS_ON")))
    leaf = [r for r in reqs if not ix.inc.get((r, "DERIVES_FROM"))]
    unsatisfied = sorted(r for r in leaf if not ix.inc.get((r, "SATISFIES")))

    risks = ix.ids("Risk")
    high = sorted(r for r in risks if ix.nodes[r]["score"] >= HIGH_RISK_SCORE)
    unmitigated_high = sorted(r for r in high if not ix.out.get((r, "MITIGATED_BY")))
    unmitigated = sorted(r for r in risks if not ix.out.get((r, "MITIGATED_BY")))

    conflicts = sorted(
        sorted([a, b]) for a in ix.ids("Decision") for b in ix.out.get((a, "CONFLICTS_WITH"), [])
        if ix.nodes[a]["status"] == "ACCEPTED" and ix.nodes[b]["status"] == "ACCEPTED"
    )
    suspect = sorted(v for v in vstatus if vstatus[v] == "PASSED" and ix.inc.get((v, "OBSERVED_IN")))

    sat_count = {p: len(ix.out.get((p, "SATISFIES"), [])) for p in parts}
    top = max(sat_count.values())
    most_connected = sorted(p for p, c in sat_count.items() if c == top)

    cycles = sorted(p for p in parts if p in _reach_incl_start(ix, p, "DEPENDS_ON"))

    suppliers: dict[str, list[str]] = defaultdict(list)
    for p in parts:
        suppliers[ix.nodes[p]["supplier"]].append(p)

    return {
        "seed": data["seed"],
        "unverified_requirements": unverified,
        "failed_requirements": failed,
        "open_requirements": open_reqs,
        "analysis_only_requirements": analysis_only,
        "closure_by_subsystem": closure,
        "orphan_parts": orphan,
        "unsatisfied_requirements": unsatisfied,
        "high_risks": high,
        "unmitigated_high_risks": unmitigated_high,
        "unmitigated_risks": unmitigated,
        "open_decision_conflicts": conflicts,
        "suspect_verifications": suspect,
        "most_connected_parts": {"parts": most_connected, "satisfies": top},
        "dependency_cycles": cycles,
        "parts_by_supplier": {s: sorted(v) for s, v in sorted(suppliers.items())},
        "part_impact": {p: part_impact(ix, p) for p in parts},
        "requirement_impact": {r: requirement_impact(ix, r) for r in reqs},
        "trace_up": {r: sorted(ix.closure(r, "DERIVES_FROM", reverse=False)) for r in reqs},
        "risks_threatening": {x: sorted(ix.inc.get((x, "THREATENS"), [])) for x in reqs + parts},
        "decision_affects": {d: sorted(ix.out.get((d, "AFFECTS"), [])) for d in ix.ids("Decision")},
        "verifications_of": {r: sorted(vers_of[r]) for r in reqs},
    }
