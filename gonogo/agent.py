"""Rule-based, evidence-first question answering over the F´ graph.

No LLM and no API key: a question is routed by keyword rules to the verdict query, the impact query
or a parameterised Cypher template. Identifiers are read from the question (requirement IDs, component
paths, FPP type names, port types, instance names) or resolved by lexical similarity through Relate's
vector index. Every answer carries the executed Cypher and its rows. Unknown identifiers and questions
outside the graph get "I don't know" plus nearest nodes as suggestions, never a guess.
For open-ended questions, connect an MCP client to the same store instead.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from gonogo.impact import TESTS_TO_RERUN_CYPHER, impact
from gonogo.queries import T, answer_ids, run
from gonogo.verdict import VERDICTS_CYPHER, current_run, deployment_parts, poll, verdicts

REQ_RE = re.compile(r"\b[A-Z][A-Z0-9]*(?:[-_][A-Z0-9]+)*[-_]\d+[A-Za-z]?\b")
PATH_RE = re.compile(r"\b(?:Svc|Drv|Fw|Os|CFDP|Utils|TestDeploymentsProject)/[\w/]*\w")
TYPE_RE = re.compile(r"\b(?:Svc|Drv|Fw|Os|Ref|CFDP|Utils)(?:\.\w+)+\b")
INSTANCE_RE = re.compile(r"(?:Ref:)?(\$?\b[a-z]\w*[A-Z]\w*|\b[A-Z]\w*\.\$?[a-z]\w*)\b")
RESOLVE_MIN_SCORE = 0.55

IMPACT = (r"impact", r"affect", r"break", r"\bchang", r"modif", r"\bedit", r"re-?run", r"retest", r"touch")
VERDICT_WORDS = (r"\bwhy\b", r"\bgo\b", r"no-go", r"verdict", r"status", r"ready", r"verif", r"tested", r"evidence", r"pass")


@dataclass
class Answer:
    question: str
    status: str                      # "answered" | "unknown"
    text: str
    template: str | None = None
    cypher: str | None = None
    params: dict = field(default_factory=dict)
    rows: list = field(default_factory=list)
    answer_ids: list = field(default_factory=list)
    resolved: list = field(default_factory=list)
    suggestions: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _has(q: str, *patterns: str) -> bool:
    return any(re.search(p, q) for p in patterns)


GLOBAL_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("fp_unknown_citations", (r"(cit|referenc)\w*.*(unknown|undefined|not (exist|defined)|nonexist|no sdd)",
                              r"(unknown|undefined|dangling|broken) (requirement )?(ids?|citations?|references?)", r"id drift")),
    ("fp_duplicate_requirement_ids", (r"duplicat", r"(defined|used|appear\w*) (more than once|twice)")),
    ("fp_cross_component_citations", (r"(another|other|wrong|different) component",)),
    ("fp_components_without_requirements", (r"components?\b.*\b(no|without|lack\w*|missing)\b.*requirement",)),
    ("unverified_requirements", (r"(no|without|missing|lack\w*) (verification )?method",)),
    ("verdict:NO-GO", (r"no-go", r"\bfail")),
    ("verdict:NO_CITING_TEST", (r"(not|never|no) (\w+ )?(cited|referenced)", r"uncited", r"no (citing )?tests?\b")),
    ("verdict:GO", (r"\bare go\b", r"\bgo requirements\b", r"which .* \bgo\b")),
    ("poll", (r"\bpoll\b", r"readiness", r"overall", r"summary", r"per component", r"coverage")),
]


class Agent:
    def __init__(self, db):
        self.db = db
        self.run = current_run(db)
        self._rows: list[dict] | None = None

    @property
    def rows(self) -> list[dict]:
        if self._rows is None:
            self._rows = verdicts(self.db, self.run)
        return self._rows

    # ---------- identifiers ----------
    def _label(self, eid: str) -> str | None:
        row = self.db.execute("MATCH (n:Entity {id: $id}) RETURN [l IN labels(n) WHERE l <> 'Entity'][0] AS label",
                              {"id": eid}).fetchone()
        return row["label"] if row else None

    def _entities(self, question: str) -> tuple[list[tuple[str, str]], list[str]]:
        found: list[tuple[str, str]] = []
        missing: list[str] = []

        def add(eid: str) -> bool:
            label = self._label(eid)
            if label and all(eid != f for f, _ in found):
                found.append((eid, label))
            return bool(label)

        for m in REQ_RE.findall(question):
            if not add(m):
                missing.append(m)
        for m in PATH_RE.findall(question):
            if not add(m.rstrip("/")):
                missing.append(m)
        for m in TYPE_RE.findall(question):
            row = self.db.execute("MATCH (p:Part) WHERE $t IN p.types RETURN p.id AS id", {"t": m}).fetchone()
            if not ((row and add(row["id"])) or add(f"port:{m}") or add(f"Ref:{m}")):
                missing.append(m)
        for m in INSTANCE_RE.findall(question):
            add(f"Ref:{m}")
        return found, missing

    def _resolve(self, question: str, label: str) -> dict | None:
        rows, _, _ = run(self.db, "vector_search_label", q=question, label=label, k=1)
        if rows and rows[0]["score"] >= RESOLVE_MIN_SCORE:
            return {"mention": question, "id": rows[0]["id"], "score": round(rows[0]["score"], 3),
                    "via": "Relate vector similarity (lexical)"}
        return None

    def _suggest(self, question: str, k: int = 5) -> list[dict]:
        rows, _, _ = run(self.db, "vector_search", q=question, k=k)
        return [{"id": r["id"], "label": r["label"], "score": round(r["score"], 3)} for r in rows]

    # ---------- answers ----------
    def _template(self, question: str, name: str, resolved=None, **params) -> Answer:
        rows, cypher, bound = run(self.db, name, **params)
        ids = sorted(answer_ids(name, rows))
        title = T[name].title + (f" ({params['id']})" if "id" in params else "")
        text = f"{title}: {len(rows)} row(s)" + (f"; ids: {', '.join(ids[:12])}{' …' if len(ids) > 12 else ''}" if ids else "") + "."
        return Answer(question, "answered", text, name, cypher, bound, rows, ids, resolved or [])

    def _verdict_of(self, question: str, rid: str) -> Answer:
        row = next(r for r in self.rows if r["id"] == rid)
        reasons = ", ".join(row["reasons"]) or "all citing test cases passed"
        tests = row["own_tests"] or ["no own test case cites it"]
        text = f"{rid} is {row['verdict']} ({row['component']}): {reasons}. Evidence: {'; '.join(tests[:5])}."
        return Answer(question, "answered", text, "verdict", VERDICTS_CYPHER.strip(),
                      {"run": self.run, "accept_declared": False}, [row], [rid])

    def _verdict_filter(self, question: str, key: str) -> Answer:
        if key in ("GO", "NO-GO", "INCOMPLETE"):
            sel = [r for r in self.rows if r["verdict"] == key]
        else:
            sel = [r for r in self.rows if key in r["reasons"]]
        ids = [r["id"] for r in sel]
        text = (f"{len(sel)} requirement(s) with {key} in run {self.run or 'none loaded'}"
                + (f": {', '.join(ids[:15])}{' …' if len(ids) > 15 else ''}." if ids else "."))
        return Answer(question, "answered", text, f"verdict:{key}", VERDICTS_CYPHER.strip(),
                      {"run": self.run, "accept_declared": False}, sel, ids)

    def _poll(self, question: str) -> Answer:
        table = [p for p in poll(self.rows, deployment_parts(self.db)) if p["in_deployment"]]
        go = sum(p["GO"] for p in table)
        total = sum(p["requirements"] for p in table)
        text = (f"Ref deployment poll: {go}/{total} requirements GO across {len(table)} components "
                f"(run {self.run or 'none loaded'}); fully GO components: "
                f"{', '.join(p['component'] for p in table if p['verdict'] == 'GO') or 'none'}.")
        return Answer(question, "answered", text, "poll", VERDICTS_CYPHER.strip(), {"run": self.run}, table)

    def _impact(self, question: str, components=(), port_types=(), resolved=None) -> Answer:
        res = impact(self.db, components=list(components), port_types=list(port_types), verdict_rows=self.rows)
        text = (f"Re-run {res['test_cases']} unit-test case(s) in {len(res['rerun'])} component(s): "
                f"{', '.join(r['component'] for r in res['rerun'][:8])}{' …' if len(res['rerun']) > 8 else ''}. "
                f"{len(res['requirements'])} requirement(s) depend on them; {len(res['go_pending_rerun'])} are GO "
                f"today and pending re-run. Deployment connections for integration review: {len(res['integration_review'])}.")
        return Answer(question, "answered", text, "impact", TESTS_TO_RERUN_CYPHER.strip(),
                      {"components": res["components"], "port_types": res["port_types"]}, res["rerun"],
                      sorted({r["component"] for r in res["rerun"]}), resolved or [])

    def ask(self, question: str) -> Answer:
        q = question.lower()
        found, missing = self._entities(question)
        if missing:
            return Answer(question, "unknown", f"I don't know: {missing[0]} is not in the graph. "
                          "Nearest entities are listed as suggestions only.", suggestions=self._suggest(question))
        if found:
            eid, label = found[0]
            return self._about(question, q, eid, label, [])
        for intent, pats in GLOBAL_RULES:
            if _has(q, *pats):
                if intent == "poll":
                    return self._poll(question)
                if intent.startswith("verdict:"):
                    return self._verdict_filter(question, intent.split(":", 1)[1])
                return self._template(question, intent)
        if _has(q, *IMPACT, r"requirement", r"test", r"component"):
            for label in (("PortType",) if "port" in q else ()) + ("Part",):
                hit = self._resolve(question, label)
                if hit:
                    return self._about(question, q, hit["id"], label, [hit])
        return Answer(question, "unknown", "I don't know: this question is not covered by the traceability graph. "
                      "Nearest entities are listed as suggestions only.", suggestions=self._suggest(question))

    def _about(self, question: str, q: str, eid: str, label: str, resolved: list) -> Answer:
        if label == "Requirement":
            return self._verdict_of(question, eid) if eid in {r["id"] for r in self.rows} else \
                self._template(question, "describe", resolved, id=eid)
        if label == "Part":
            if _has(q, *IMPACT):
                return self._impact(question, components=[eid], resolved=resolved)
            return self._template(question, "fp_part_requirements", resolved, id=eid)
        if label == "PortType":
            if _has(q, *IMPACT):
                return self._impact(question, port_types=[eid], resolved=resolved)
            return self._template(question, "fp_port_type_users", resolved, id=eid)
        if label == "Instance":
            return self._template(question, "fp_instance_neighbours", resolved, id=eid)
        return self._template(question, "describe", resolved, id=eid)
