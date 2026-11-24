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

from gonogo.db import dataset_kind
from gonogo.queries import T, answer_ids, run

# identifiers that may appear in a question
ID_RE = re.compile(r"\b(?:REQ|PART|RISK|DEC|VER|DOC|ANOM)-[A-Z0-9]+(?:-[A-Z0-9]+)*\b", re.IGNORECASE)
GENERIC_REQ_RE = re.compile(r"\b[A-Z][A-Z0-9]*(?:[-_][A-Z0-9]+)*[-_]\d+[A-Za-z]?\b")  # ARG-001, SVC_CHRONO_TIME_002
PATH_RE = re.compile(r"\b(?:Svc|Drv|Fw|Os|CFDP|Utils|TestDeploymentsProject)/[\w/]*\w")  # Svc/ActiveRateGroup
TYPE_RE = re.compile(r"\b(?:Svc|Drv|Fw|Os|Ref|CFDP|Utils)\.\w+\b")  # Svc.ActiveRateGroup
INSTANCE_RE = re.compile(r"(?:Ref:)?(\$?\b[a-z]\w*[A-Z]\w*|\b[A-Z]\w*\.\$?\w+)\b")  # rateGroup1Comp, CdhCore.cmdDisp
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

# Rules that only make sense on the F´ store (checked first there)
FP_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("fp_unknown_citations", (r"(cit|referenc)\w*.*(unknown|undefined|missing|not (exist|defined)|nonexist|no sdd|not in (any|the) sdd)",
                              r"(unknown|undefined|dangling|broken|orphan(ed)?) (requirement )?(ids?|citations?|references?)",
                              r"id drift")),
    ("fp_duplicate_requirement_ids", (r"duplicat", r"(defined|used|appear\w*) (more than once|twice)")),
    ("fp_unit_test_without_citation", (r"unit[- ]test\w*.*\b(no|not|without|never|lack\w*|missing)\b.*(cit|referenc|trac|link|evidence)",
                                       r"\b(not|never|no)\b (\w+ )?(cited|referenced)", r"uncited",
                                       r"without (a |any )?(test )?citation")),
    ("fp_components_without_requirements", (r"components?\b.*\b(no|without|lack\w*|missing)\b.*requirement",
                                            r"(deploy\w*|\bref\b).*\b(no|without)\b.*requirement")),
    ("fp_component_coverage", (r"coverage", r"percent", r"per component", r"evidence (rate|ratio|level)")),
]

IMPACT = (r"impact", r"affect", r"blast", r"ripple", r"break", r"\bchang", r"re-?run", r"re-?verif", r"replac")
TRACE = (r"trace", r"parent", r"derive", r"\bwhy\b", r"upstream", r"mission", r"ancestor", r"comes? from")


class Agent:
    def __init__(self, db):
        self.db = db
        self.kind = dataset_kind(db)
        self._suppliers = sorted(
            {r["s"] for r in (x.data() for x in db.execute("MATCH (p:Part) RETURN DISTINCT p.supplier AS s"))
             if r["s"]}, key=len, reverse=True)

    def _entities(self, question: str) -> tuple[list[tuple[str, str]], list[str]]:
        """Identifiers mentioned in the question: [(id, label)] that exist, and id-like tokens that do not."""
        found: list[tuple[str, str]] = []
        missing: list[str] = []

        def add(eid: str, must_exist: bool = True) -> None:
            if any(eid == f for f, _ in found):
                return
            label = self._exists(eid)
            if label:
                found.append((eid, label))
            elif must_exist and eid not in missing:
                missing.append(eid)

        for m in ID_RE.findall(question):
            add(m.upper())
        for m in GENERIC_REQ_RE.findall(question):
            add(m)
        for m in PATH_RE.findall(question):
            add(m.rstrip("/"))
        for m in TYPE_RE.findall(question):
            row = self.db.execute("MATCH (p:Part) WHERE $t IN p.types RETURN p.id AS id", {"t": m}).fetchone()
            if row:
                add(row["id"])
            elif self._exists(f"Ref:{m}"):
                add(f"Ref:{m}")
            elif self.kind == "fprime" and m not in missing:
                missing.append(m)
        if self.kind == "fprime":
            for m in INSTANCE_RE.findall(question):
                if self._exists(f"Ref:{m}"):
                    add(f"Ref:{m}")
        return found, missing

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
        found, missing = self._entities(question)
        if missing:
            return Answer(question, "unknown",
                          f"I don't know: {missing[0]} is not in the graph. Nearest entities are listed as suggestions only.",
                          suggestions=self._suggest(question))

        # ---- entity-specific intents ----
        if found:
            eid, label = found[0]
            return self._entity_intent(question, q, eid, label, resolved=[])

        # ---- F´-only global intents ----
        if self.kind == "fprime":
            for intent, pats in FP_RULES:
                if _has(q, *pats):
                    return self._answer(question, intent)

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
        if self.kind == "fprime":
            if label == "Part":
                if _has(q, *IMPACT, r"depend", r"downstream"):
                    return self._answer(question, "fp_component_impact", resolved=resolved, id=eid)
                if _has(q, r"requirement", r"test", r"cit", r"evidence", r"verif"):
                    return self._answer(question, "fp_part_requirements", resolved=resolved, id=eid)
            if label == "Instance":
                return self._answer(question, "fp_instance_neighbours", resolved=resolved, id=eid)
            if label == "Requirement" and _has(q, r"verif", r"test", r"cit", r"evidence", r"how", r"covered"):
                return self._answer(question, "fp_requirement_evidence", resolved=resolved, id=eid)
            return self._answer(question, "describe", resolved=resolved, id=eid)
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
    if template == "fp_component_coverage":
        total = sum(r["requirements"] for r in rows)
        cited = sum(r["cited"] for r in rows)
        zero = [r["component"] for r in rows if r["cited"] == 0]
        pct = round(100.0 * cited / total, 1) if total else 0.0
        return (f"{title}: {cited}/{total} requirements ({pct}%) are cited by a unit test via REQUIREMENT(); "
                f"{len(zero)} of {len(rows)} components have no cited requirement. Per-component rows below.")
    if template == "fp_requirement_evidence":
        if not rows:
            return f"{title}: no rows."
        r = rows[0]
        files = ", ".join(r["test_files"]) or "no unit test cites it with REQUIREMENT()"
        return (f"{r['requirement']}: declared verification '{r['declared'] or 'none'}'; "
                f"evidence: {files}. Source: {r['source']}.")
    if template == "fp_component_impact":
        if not rows:
            return f"{title}: no rows."
        r = rows[0]
        return (f"{title} for {params['id']}: {len(r['instances'])} instance(s) in the Ref deployment, "
                f"{len(r['downstream_instances'])} downstream instances within 2 port hops, "
                f"{len(r['parts'])} other components, {len(r['requirements'])} requirements and "
                f"{len(r['verifications'])} declared verifications to revisit (blast radius {r['blast_radius']}).")
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
