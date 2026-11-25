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
IMPACT = (r"impact", r"affect", r"break", r"\bchang", r"modif", r"\bedit", r"re-?run", r"retest", r"touch")
VERDICT_WORDS = (r"\bwhy\b", r"\bgo\b", r"no-go", r"verdict", r"status", r"ready", r"verif", r"tested", r"evidence",
                 r"\bpass", r"\bfly\b")

# words that never name a component; what remains of a question is the mention we try to resolve
STOP = set("""what which who whom whose when where why how is are was were be been being do does did done should would
could can will shall may might must i me my we our you your it its it's this that these those the a an of to in on at for
from by with about into after before if then than and or not no yes please tell show list give find need want if
change changes changed changing modify modified modifying edit edits edited editing break breaks broken breaking impact
impacts impacted affect affects affected re rerun run runs retest touch touched test tests tested testing case cases
requirement requirements component components part parts port ports type types ready fly flight go no-go status verdict
verified verify evidence pass passes passed one ones thing things something anything any all every some
sends send sent uses use used does handle handles""".split())

# F´ abbreviations, so "command dispatcher" meets Svc/CmdDispatcher and "telemetry" meets Svc/TlmChan
ABBREV = {"cmd": "command", "tlm": "telemetry", "seq": "sequencer", "prm": "parameter", "mgr": "manager",
          "buff": "buffer", "buf": "buffer", "drv": "driver", "chan": "channel", "disp": "dispatcher",
          "com": "communication", "dp": "product", "rg": "rate", "posix": "time"}


def _words(text: str) -> set[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return {w for w in re.findall(r"[a-z][a-z0-9]+", spaced.lower())}


def _close(a: str, b: str) -> bool:
    """Whole-word match, or a shared stem of at least 5 letters (dispatch / dispatcher)."""
    return a == b or (min(len(a), len(b)) >= 5 and (a.startswith(b) or b.startswith(a)))


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
    ("components_go", (r"components?\b.*\b(fully|entirely|completely|all)\b.*\bgo\b", r"fully go",
                       r"which components? (are|is) go\b")),
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

    def _resolve(self, question: str, label: str) -> dict:
        """Resolve a component or port type named in words. Only the mention counts (question and action words are
        dropped), and a candidate must share a whole word with it, so "the weather" resolves to nothing.
        Returns {"hit": ...} or {"ambiguous": [...]} or {}."""
        mention = sorted(w for w in _words(question) - STOP if len(w) >= 3)
        if not mention:
            return {}
        if label == "Part":
            cands = [(r["id"], r["id"].split("/")[-1] + " " + " ".join(r["types"] or [])) for r in (x.data() for x in
                     self.db.execute("MATCH (p:Part) RETURN p.id AS id, p.types AS types"))]
        else:
            cands = [(r["id"], r["id"].split(".")[-1]) for r in (x.data() for x in
                     self.db.execute("MATCH (t:PortType) RETURN t.id AS id"))]
        scored = []
        for cid, text in cands:
            cw = _words(text)
            cw |= {ABBREV[w] for w in cw if w in ABBREV}
            hits = sum(any(_close(m, w) for w in cw) for m in mention)
            if hits:
                scored.append((hits, -len(cw), cid))
        if not scored:
            return {}
        scored.sort(reverse=True)
        best = [c for h, _, c in scored if h == scored[0][0]]
        if len(best) > 1:
            return {"ambiguous": best[:8], "mention": " ".join(mention)}
        return {"hit": {"mention": " ".join(mention), "id": best[0], "matched_words": scored[0][0],
                        "via": "whole-word match on component names (with F´ abbreviations)"}}

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
        assumed = (f" Assuming the headers of {', '.join(res['assumed_interface_change'])} change; name the files "
                   "for an exact set." if res["assumed_interface_change"] else "")
        text = (f"Re-run {res['test_cases']} unit-test case(s) in {len(res['rerun'])} component(s): "
                f"{', '.join(r['component'] for r in res['rerun'][:8])}{' …' if len(res['rerun']) > 8 else ''}. "
                f"{len(res['requirements'])} requirement(s) depend on them; {len(res['go_pending_rerun'])} are GO "
                f"today and pending re-run. Deployment connections for integration review: "
                f"{len(res['integration_review'])}.{assumed}")
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
                if intent == "components_go":
                    return self._fully_go(question)
                if intent.startswith("verdict:"):
                    return self._verdict_filter(question, intent.split(":", 1)[1])
                return self._template(question, intent)
        if _has(q, *IMPACT, *VERDICT_WORDS, r"requirement", r"test", r"component"):
            for label in (("PortType",) if "port" in q else ()) + ("Part",):
                res = self._resolve(question, label)
                if "hit" in res:
                    return self._about(question, q, res["hit"]["id"], label, [res["hit"]])
                if "ambiguous" in res:
                    return Answer(question, "unknown",
                                  f"I don't know which {'port type' if label == 'PortType' else 'component'} "
                                  f"\"{res['mention']}\" means: {', '.join(res['ambiguous'])}. Name one of them.",
                                  suggestions=[{"id": c, "label": label, "score": None} for c in res["ambiguous"]])
        return Answer(question, "unknown", "I don't know: this question is not covered by the traceability graph. "
                      "Nearest entities are listed as suggestions only.", suggestions=self._suggest(question))

    def _component_verdict(self, question: str, eid: str, resolved: list) -> Answer:
        row = next((p for p in poll(self.rows, deployment_parts(self.db)) if p["component"] == eid), None)
        if row is None:
            return Answer(question, "answered", f"{eid} has no SDD requirements, so GoNoGo has nothing to judge.",
                          "component_verdict", VERDICTS_CYPHER.strip(), {"run": self.run}, [], [eid], resolved)
        text = (f"{eid} is {row['verdict']}: {row['GO']} of {row['requirements']} requirements GO, {row['NO-GO']} NO-GO, "
                f"{row['INCOMPLETE']} incomplete" + (f" ({', '.join(row['top_reasons'])})" if row["top_reasons"] else "")
                + ". This is a traceability verdict from repository text and CI results, not flight readiness.")
        return Answer(question, "answered", text, "component_verdict", VERDICTS_CYPHER.strip(),
                      {"run": self.run, "accept_declared": False}, [row], [eid], resolved)

    def _fully_go(self, question: str) -> Answer:
        table = poll(self.rows, deployment_parts(self.db))
        go = [p["component"] for p in table if p["verdict"] == "GO"]
        dep = [c for c in go if any(p["component"] == c and p["in_deployment"] for p in table)]
        text = (f"{len(go)} of {len(table)} components with requirements are fully GO"
                + (f": {', '.join(go)}" if go else "") + f". In the Ref deployment: {', '.join(dep) or 'none'}.")
        return Answer(question, "answered", text, "components_go", VERDICTS_CYPHER.strip(), {"run": self.run},
                      [p for p in table if p["verdict"] == "GO"], go)

    def _about(self, question: str, q: str, eid: str, label: str, resolved: list) -> Answer:
        if label == "Requirement":
            return self._verdict_of(question, eid) if eid in {r["id"] for r in self.rows} else \
                self._template(question, "describe", resolved, id=eid)
        if label == "Part":
            if _has(q, *IMPACT):
                return self._impact(question, components=[eid], resolved=resolved)
            if _has(q, *VERDICT_WORDS):
                return self._component_verdict(question, eid, resolved)
            return self._template(question, "fp_part_requirements", resolved, id=eid)
        if label == "PortType":
            if _has(q, *IMPACT):
                return self._impact(question, port_types=[eid], resolved=resolved)
            return self._template(question, "fp_port_type_users", resolved, id=eid)
        if label == "Instance":
            return self._template(question, "fp_instance_neighbours", resolved, id=eid)
        return self._template(question, "describe", resolved, id=eid)
