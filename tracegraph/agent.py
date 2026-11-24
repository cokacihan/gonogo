"""Rule-based, evidence-first question answering over the Relate graph.

No LLM and no API key. A question is mapped to one parameterised Cypher template by
keyword rules; entity ids are taken from the question or resolved through Relate's
vector index. Every answer carries the executed Cypher, its parameters and the result
rows. If no rule matches or an entity cannot be found, the agent says it does not know
and only offers nearest graph nodes as suggestions (never as an answer).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from tracegraph.queries import T, answer_ids, run

ID_RE = re.compile(r"\b(?:REQ|PART|RISK|DEC|VER|DOC|ANOM)-[A-Z0-9]+(?:-[A-Z0-9]+)*\b", re.IGNORECASE)
PREFIX_LABEL = {"REQ": "Requirement", "PART": "Part", "RISK": "Risk", "DEC": "Decision",
                "VER": "Verification", "DOC": "Document", "ANOM": "Anomaly"}
RESOLVE_MIN_SCORE = 0.55  # cosine score (rebounded to [0,1]) needed to accept a name -> id resolution


@dataclass
class Answer:
    question: str
    status: str                      # "answered" | "unknown"
    text: str
    intent: str | None = None
    template: str | None = None
    cypher: str | None = None
    params: dict = field(default_factory=dict)
    rows: list = field(default_factory=list)
    answer_ids: list = field(default_factory=list)
    resolved: list = field(default_factory=list)  # [{mention, id, score, via}]
    suggestions: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _has(q: str, *patterns: str) -> bool:
    return any(re.search(p, q) for p in patterns)


# Global (no entity) intents: (intent/template, patterns). First match wins.
GLOBAL_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("dependency_cycles", (r"\bcycl", r"circular", r"\bloops?\b")),
    ("open_decision_conflicts", (r"conflict", r"contradict", r"inconsistent decision")),
    ("suspect_verifications", (r"anomal", r"suspect", r"passed\b.*\bbut\b")),
    ("unmitigated_high_risks", (r"(unmitigated|no mitigation|without (a |any )?mitigation|not mitigated|lack(s|ing)? mitigation).*(high|critical|red|severe)",
                                r"(high|critical|red|severe).*(unmitigated|no mitigation|without (a |any )?mitigation|not mitigated|lack(s|ing)? mitigation)")),
    ("unmitigated_risks", (r"unmitigated", r"no mitigation", r"without (a |any )?mitigation", r"not mitigated", r"lack(s|ing)? mitigation")),
    ("high_risks", (r"(high|top|critical|severe|red)[- ](zone )?risks?", r"risks?.*(high|critical|severe)")),
    ("analysis_only_requirements", (r"only (by |through |via )?analysis", r"analysis[- ]only", r"just analysis",
                                    r"(sole|solely|exclusively|purely)\b.*\banalysis", r"analysis\b.*\b(sole|solely|exclusive)")),
    ("unverified_requirements", (r"(not|never|no|without|lack\w*|missing)\s+(any\s+)?(verif|test)", r"unverified", r"untested",
                                 r"verification gaps?", r"coverage gaps?",
                                 r"not covered by", r"zero verif", r"no verif")),
    ("failed_requirements", (r"fail",)),
    ("closure_by_subsystem", (r"closure", r"percent", r"progress", r"\bclosed\b.*subsystem", r"subsystem.*\bclosed\b", r"per subsystem")),
    ("open_requirements", (r"\bopen\b", r"not (yet )?closed", r"still (un|not )")),
    ("orphan_parts", (r"orphan", r"unused part", r"parts?.*(satisf\w* no|no requirement|not (linked|traced))", r"dead part")),
    ("unsatisfied_requirements", (r"no part", r"not satisfied", r"unsatisfied", r"unallocated", r"nothing satisf",
                                  r"no satisfying part", r"requirements?\b.*\b(lack\w*|without|no)\b.*\bpart")),
    ("most_connected_parts", (r"most (connected|critical|important|requirements)", r"satisf\w* the most", r"single point",
                              r"part\b.*\b(highest|largest|most|maximum)\b.*requirement")),
]

IMPACT = (r"impact", r"affect", r"blast", r"ripple", r"break", r"\bchang", r"re-?run", r"re-?verif", r"replac")
TRACE = (r"trace", r"parent", r"derive", r"\bwhy\b", r"upstream", r"mission", r"ancestor", r"comes? from")


class Agent:
    def __init__(self, db):
        self.db = db
        self._suppliers = sorted(
            {r["s"] for r in (x.data() for x in db.execute("MATCH (p:Part) RETURN DISTINCT p.supplier AS s"))},
            key=len, reverse=True)

    # ---------- entity handling ----------
    def _exists(self, eid: str) -> str | None:
        row = self.db.execute(
            "MATCH (n:Entity {id: $id}) RETURN [l IN labels(n) WHERE l <> 'Entity'][0] AS label", {"id": eid}
        ).fetchone()
        return row["label"] if row else None

    def _resolve_by_name(self, question: str, label: str) -> dict | None:
        rows, _, _ = run(self.db, "vector_search_label", q=question, label=label, k=1)
        for r in rows:
            if r["score"] >= RESOLVE_MIN_SCORE:
                return {"mention": question, "id": r["id"], "score": round(r["score"], 3), "via": "Relate vector similarity"}
        return None

    def _suggest(self, question: str, k: int = 5) -> list[dict]:
        rows, _, _ = run(self.db, "vector_search", q=question, k=k)
        return [{"id": r["id"], "label": r["label"], "score": round(r["score"], 3)} for r in rows]

    # ---------- main entry ----------
    def ask(self, question: str) -> Answer:
        q = question.lower()
        ids = [m.upper() for m in ID_RE.findall(question)]

        for eid in ids:
            if self._exists(eid) is None:
                return Answer(question, "unknown",
                              f"I don't know: {eid} is not in the graph. Nearest entities are listed as suggestions only.",
                              suggestions=self._suggest(question))

        # ---- entity-specific intents ----
        if ids:
            eid = ids[0]
            label = PREFIX_LABEL[eid.split("-")[0]]
            return self._entity_intent(question, q, eid, label, resolved=[])

        # ---- supplier ----
        for s in self._suppliers:
            if s.lower() in q:
                return self._answer(question, "parts_by_supplier", supplier=s)

        # ---- global intents ----
        for intent, pats in GLOBAL_RULES:
            if _has(q, *pats):
                return self._answer(question, intent)

        # ---- entity named in words (no id): resolve through the vector index ----
        if _has(q, *IMPACT) or _has(q, r"risk", r"verif", r"tested"):
            want = "Requirement" if _has(q, r"requirement", r"\bshall\b") else "Part"
            hit = self._resolve_by_name(question, want)
            if hit:
                return self._entity_intent(question, q, hit["id"], want, resolved=[hit])

        return Answer(question, "unknown",
                      "I don't know: this question is not covered by the traceability graph. "
                      "Nearest entities are listed as suggestions only.",
                      suggestions=self._suggest(question))

    def _entity_intent(self, question: str, q: str, eid: str, label: str, resolved: list) -> Answer:
        # "which/what risks ..." asks for risks even if it also says "affect"
        if label in ("Part", "Requirement") and (re.search(r"\b(which|what|list|any)\s+(\w+\s+)?risks?\b", q)
                                                 or re.search(r"\brisks\b", q)):
            return self._answer(question, "risks_threatening", resolved=resolved, id=eid)
        if label == "Part":
            if _has(q, r"risk") and not _has(q, *IMPACT):
                return self._answer(question, "risks_threatening", resolved=resolved, id=eid)
            if _has(q, *IMPACT) or _has(q, r"depend"):
                return self._answer(question, "part_impact", resolved=resolved, id=eid)
        if label == "Requirement":
            if _has(q, r"risk"):
                return self._answer(question, "risks_threatening", resolved=resolved, id=eid)
            if _has(q, *IMPACT):
                return self._answer(question, "requirement_impact", resolved=resolved, id=eid)
            if _has(q, *TRACE):
                return self._answer(question, "trace_up", resolved=resolved, id=eid)
            if _has(q, r"verif", r"test", r"evidence", r"closed"):
                return self._answer(question, "verifications_of", resolved=resolved, id=eid)
        if label == "Decision" and _has(q, *IMPACT, r"what does", r"touch"):
            return self._answer(question, "decision_affects", resolved=resolved, id=eid)
        return self._answer(question, "describe", resolved=resolved, id=eid)

    def _answer(self, question: str, template: str, resolved: list | None = None, **params) -> Answer:
        rows, cypher, bound = run(self.db, template, **params)
        ids = sorted(answer_ids(template, rows))
        return Answer(question, "answered", _render(template, rows, ids, bound), intent=template, template=template,
                      cypher=cypher, params=bound, rows=rows, answer_ids=ids, resolved=resolved or [])


def _render(template: str, rows: list[dict], ids: list[str], params: dict) -> str:
    title = T[template].title
    if template == "closure_by_subsystem":
        parts = [f"{r['subsystem']} {r['closed']}/{r['total']} ({r['percent']}%)" for r in rows]
        return f"{title}: " + ", ".join(parts) + "."
    if template in ("part_impact", "requirement_impact"):
        if not rows:
            return f"{title}: no rows."
        r = rows[0]
        return (f"{title} for {params['id']}: blast radius {r['blast_radius']} "
                f"({len(r['parts'])} parts, {len(r['requirements'])} requirements, "
                f"{len(r['verifications'])} verifications to re-run).")
    if template == "open_decision_conflicts":
        pairs = [f"{r['a']} vs {r['b']}" for r in rows]
        return f"{title}: {len(rows)} open conflict(s): " + "; ".join(pairs) + "." if rows else f"{title}: none."
    if template == "describe":
        nb = sorted({r["id"] for r in rows if r["id"]})
        return f"{params['id']}: {rows[0]['text'] if rows else ''} Linked to {len(nb)} entities."
    suffix = f" ({params['id']})" if "id" in params else (f" ({params['supplier']})" if "supplier" in params else "")
    if not ids:
        return f"{title}{suffix}: none found in the graph."
    return f"{title}{suffix}: {len(ids)} found: " + ", ".join(ids) + "."
