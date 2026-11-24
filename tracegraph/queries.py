"""Parameterised, read-only Cypher templates. Every agent answer is produced by one of these.

Each template declares which result columns form the answer set, so the evaluation and the
UI can show exactly which rows support which claim.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from tracegraph.embed import DIM, embed

HIGH_RISK_SCORE = 12
MAX_HOPS = 10


@dataclass(frozen=True)
class Template:
    name: str
    title: str
    cypher: str
    params: tuple[str, ...] = ()
    id_columns: tuple[str, ...] = ("id",)  # columns (scalars or lists) whose values are answer ids
    defaults: dict = field(default_factory=dict)


T: dict[str, Template] = {}


def _t(*args, **kw) -> None:
    t = Template(*args, **kw)
    T[t.name] = t


_t("unverified_requirements", "Requirements with no verification activity",
   "MATCH (r:Requirement) WHERE NOT (r)-[:VERIFIED_BY]->() "
   "RETURN r.id AS id, r.subsystem AS subsystem, r.text AS text ORDER BY id")

_t("failed_requirements", "Requirements linked to a FAILED verification",
   "MATCH (r:Requirement)-[:VERIFIED_BY]->(v:Verification {status: 'FAILED'}) "
   "RETURN r.id AS id, v.id AS verification, v.method AS method ORDER BY id, verification")

_t("open_requirements", "Requirements not yet closed (no PASSED verification)",
   "MATCH (r:Requirement) WHERE NOT EXISTS { MATCH (r)-[:VERIFIED_BY]->(:Verification {status: 'PASSED'}) } "
   "OPTIONAL MATCH (r)-[:VERIFIED_BY]->(v) "
   "RETURN r.id AS id, r.subsystem AS subsystem, collect(v.status) AS verification_status ORDER BY id")

_t("analysis_only_requirements", "Requirements verified by analysis only (no test, inspection or review)",
   "MATCH (r:Requirement)-[:VERIFIED_BY]->(v:Verification) "
   "WITH r, collect(DISTINCT v.method) AS methods WHERE methods = ['Analysis'] "
   "RETURN r.id AS id, r.subsystem AS subsystem ORDER BY id")

_t("closure_by_subsystem", "Verification closure per subsystem",
   "MATCH (r:Requirement) "
   "WITH r.subsystem AS subsystem, "
   "     EXISTS { MATCH (r)-[:VERIFIED_BY]->(:Verification {status: 'PASSED'}) } AS closed "
   "WITH subsystem, count(*) AS total, sum(CASE WHEN closed THEN 1 ELSE 0 END) AS closed "
   "RETURN subsystem, total, closed, round(100.0 * closed / total, 1) AS percent ORDER BY subsystem",
   id_columns=())

_t("orphan_parts", "Parts that satisfy no requirement and enable no other part",
   "MATCH (p:Part) WHERE NOT (p)-[:SATISFIES]->() AND NOT ()-[:DEPENDS_ON]->(p) "
   "RETURN p.id AS id, p.name AS name, p.subsystem AS subsystem ORDER BY id")

_t("unsatisfied_requirements", "Leaf requirements that no part satisfies",
   "MATCH (r:Requirement) WHERE NOT ()-[:DERIVES_FROM]->(r) AND NOT ()-[:SATISFIES]->(r) "
   "RETURN r.id AS id, r.text AS text ORDER BY id")

_t("high_risks", "High risks (severity x likelihood >= threshold)",
   "MATCH (k:Risk) WHERE k.score >= $threshold "
   "RETURN k.id AS id, k.title AS title, k.score AS score ORDER BY score DESC, id",
   params=("threshold",), defaults={"threshold": HIGH_RISK_SCORE})

_t("unmitigated_high_risks", "High risks without any mitigation",
   "MATCH (k:Risk) WHERE k.score >= $threshold AND NOT (k)-[:MITIGATED_BY]->() "
   "OPTIONAL MATCH (k)-[:THREATENS]->(t) "
   "RETURN k.id AS id, k.title AS title, k.score AS score, collect(t.id) AS threatens ORDER BY score DESC, id",
   params=("threshold",), defaults={"threshold": HIGH_RISK_SCORE}, id_columns=("id",))

_t("unmitigated_risks", "Risks without any mitigation (all scores)",
   "MATCH (k:Risk) WHERE NOT (k)-[:MITIGATED_BY]->() "
   "RETURN k.id AS id, k.title AS title, k.score AS score ORDER BY score DESC, id")

_t("open_decision_conflicts", "Accepted decisions that conflict with each other",
   "MATCH (a:Decision {status: 'ACCEPTED'})-[:CONFLICTS_WITH]-(b:Decision {status: 'ACCEPTED'}) "
   "WHERE a.id < b.id RETURN a.id AS a, b.id AS b, a.title AS a_title, b.title AS b_title ORDER BY a, b",
   id_columns=("a", "b"))

_t("suspect_verifications", "PASSED verifications with an anomaly recorded against them",
   "MATCH (an:Anomaly)-[:OBSERVED_IN]->(v:Verification {status: 'PASSED'}) "
   "OPTIONAL MATCH (r:Requirement)-[:VERIFIED_BY]->(v) "
   "RETURN v.id AS id, collect(DISTINCT an.id) AS anomalies, collect(DISTINCT r.id) AS requirements ORDER BY id")

_t("most_connected_parts", "Parts that satisfy the most requirements",
   "MATCH (p:Part) OPTIONAL MATCH (p)-[:SATISFIES]->(r:Requirement) "
   "WITH p, count(r) AS n WITH max(n) AS top, collect({id: p.id, n: n}) AS rows "
   "UNWIND rows AS x WITH x, top WHERE x.n = top RETURN x.id AS id, x.n AS satisfies ORDER BY id")

_t("dependency_cycles", "Parts that sit on a circular DEPENDS_ON chain",
   f"MATCH path = (p:Part)-[:DEPENDS_ON*1..{MAX_HOPS}]->(p) "
   "RETURN p.id AS id, min(length(path)) AS cycle_length, collect([n IN nodes(path) | n.id])[0] AS example ORDER BY id")

_t("parts_by_supplier", "Parts from a given supplier",
   "MATCH (p:Part) WHERE toLower(p.supplier) = toLower($supplier) "
   "RETURN p.id AS id, p.name AS name, p.subsystem AS subsystem ORDER BY id", params=("supplier",))

_t("part_impact", "Change impact of a part (dependent parts, requirements up the tree, verifications to re-run)",
   "MATCH (p:Part {id: $id}) "
   f"OPTIONAL MATCH (d:Part)-[:DEPENDS_ON*1..{MAX_HOPS}]->(p) "
   "WITH p, [x IN collect(DISTINCT d) WHERE x <> p] AS deps "  # a dependency cycle can lead back to p
   "UNWIND [p] + deps AS a "
   "OPTIONAL MATCH (a)-[:SATISFIES]->(r0:Requirement) "
   f"OPTIONAL MATCH (r0)-[:DERIVES_FROM*0..{MAX_HOPS}]->(r:Requirement) "
   "WITH deps, collect(DISTINCT r) AS reqs "
   "UNWIND CASE WHEN size(reqs) = 0 THEN [null] ELSE reqs END AS r "
   "OPTIONAL MATCH (r)-[:VERIFIED_BY]->(v:Verification) "
   "WITH [x IN deps | x.id] AS parts, [x IN reqs | x.id] AS requirements, collect(DISTINCT v.id) AS verifications "
   "RETURN parts, requirements, verifications, size(parts) + size(requirements) + size(verifications) AS blast_radius",
   params=("id",), id_columns=("parts", "requirements", "verifications"))

_t("requirement_impact", "Change impact of a requirement (derived requirements, parts, verifications)",
   "MATCH (r:Requirement {id: $id}) "
   f"OPTIONAL MATCH (c:Requirement)-[:DERIVES_FROM*1..{MAX_HOPS}]->(r) "
   "WITH r, collect(DISTINCT c) AS kids "
   "UNWIND [r] + kids AS s "
   "OPTIONAL MATCH (p:Part)-[:SATISFIES]->(s) "
   "OPTIONAL MATCH (s)-[:VERIFIED_BY]->(v:Verification) "
   "WITH [x IN kids | x.id] AS requirements, collect(DISTINCT p.id) AS parts, collect(DISTINCT v.id) AS verifications "
   "RETURN requirements, parts, verifications, size(requirements) + size(parts) + size(verifications) AS blast_radius",
   params=("id",), id_columns=("requirements", "parts", "verifications"))

_t("trace_up", "Parent requirement chain up to mission level",
   f"MATCH path = (r:Requirement {{id: $id}})-[:DERIVES_FROM*1..{MAX_HOPS}]->(a:Requirement) "
   "RETURN a.id AS id, a.level AS level, length(path) AS hops, a.text AS text ORDER BY hops",
   params=("id",))

_t("risks_threatening", "Risks that threaten a requirement or part",
   "MATCH (k:Risk)-[:THREATENS]->(x:Entity {id: $id}) "
   "OPTIONAL MATCH (k)-[:MITIGATED_BY]->(m) "
   "RETURN k.id AS id, k.title AS title, k.score AS score, collect(m.id) AS mitigations ORDER BY score DESC, id",
   params=("id",))

_t("decision_affects", "Items directly affected by a decision",
   "MATCH (d:Decision {id: $id})-[:AFFECTS]->(x:Entity) "
   "RETURN x.id AS id, [l IN labels(x) WHERE l <> 'Entity'][0] AS label ORDER BY id", params=("id",))

_t("verifications_of", "Verification activities of a requirement",
   "MATCH (r:Requirement {id: $id})-[:VERIFIED_BY]->(v:Verification) "
   "RETURN v.id AS id, v.method AS method, v.status AS status ORDER BY id", params=("id",))

_t("describe", "Neighbourhood of one entity",
   "MATCH (n:Entity {id: $id}) OPTIONAL MATCH (n)-[rel]-(m:Entity) "
   "RETURN n.id AS node, n.text AS text, type(rel) AS rel, "
   "CASE WHEN startNode(rel) = n THEN 'out' ELSE 'in' END AS direction, m.id AS id ORDER BY rel, id",
   params=("id",))

_t("vector_search", "Nearest entities by lexical embedding (Relate vector index)",
   "MATCH (n:Entity) SEARCH n IN (VECTOR INDEX entity_embedding FOR vector($q, $dim, FLOAT32) LIMIT $k) "
   "SCORE AS score RETURN n.id AS id, [l IN labels(n) WHERE l <> 'Entity'][0] AS label, score, n.text AS text "
   "ORDER BY score DESC, id",
   params=("q", "dim", "k"))

_t("vector_search_label", "Nearest entities of one label (exact cosine scan in Relate)",
   "MATCH (n:Entity) WHERE $label IN labels(n) "
   "WITH n, vector.similarity.cosine(n.embedding, vector($q, $dim, FLOAT32)) AS score "
   "RETURN n.id AS id, $label AS label, score, n.text AS text ORDER BY score DESC, id LIMIT $k",
   params=("q", "dim", "label", "k"))

_t("fulltext_search", "Full-text matches (Relate full-text index)",
   "CALL db.index.fulltext.queryNodes('entity_text', $text) YIELD node, score "
   "RETURN node.id AS id, [l IN labels(node) WHERE l <> 'Entity'][0] AS label, score, node.text AS text "
   "ORDER BY score DESC, id LIMIT $k",
   params=("text", "k"))

_t("graph_rag", "GraphRAG: vector entry points expanded one hop through the graph",
   "MATCH (n:Entity) SEARCH n IN (VECTOR INDEX entity_embedding FOR vector($q, $dim, FLOAT32) LIMIT $k) "
   "SCORE AS score OPTIONAL MATCH (n)-[rel]-(m:Entity) "
   "RETURN n.id AS id, score, collect(DISTINCT type(rel) + ':' + m.id) AS context ORDER BY score DESC, id",
   params=("q", "dim", "k"))


# ------------------------------------------------------------------ F´ (real data) templates
FP_IMPACT_HOPS = 2

_t("fp_unit_test_without_citation", "Requirements declaring 'Unit Test' that no unit test cites with REQUIREMENT()",
   "MATCH (p:Part)-[:SATISFIES]->(r:Requirement)-[:VERIFIED_BY]->(v:Verification {method: 'Unit Test'}) "
   "WHERE NOT (v)-[:EVIDENCED_BY]->(:TestFile) "
   "RETURN r.id AS id, p.id AS component, p.has_unit_tests AS component_has_tests ORDER BY component, id")

_t("fp_cited_requirements", "Requirements cited by unit tests (with the citing files)",
   "MATCH (t:TestFile)-[c:CITES]->(r:Requirement) "
   "RETURN r.id AS id, collect(t.id) AS files, reduce(f = [], x IN collect(c.functions) | f + x) AS functions "
   "ORDER BY id")

_t("fp_unknown_citations", "Test files citing requirement IDs that no SDD defines",
   "MATCH (t:TestFile) WHERE size(t.unknown_citations) > 0 "
   "OPTIONAL MATCH (t)-[:TESTS]->(p:Part) "
   "RETURN t.id AS file, p.id AS component, t.unknown_citations AS unknown_ids ORDER BY file",
   id_columns=("unknown_ids",))

_t("fp_duplicate_requirement_ids", "Requirement IDs defined more than once",
   "MATCH (r:Requirement) WHERE r.id CONTAINS '@' "
   "RETURN split(r.id, '@')[0] AS id, r.component AS component, r.source AS source ORDER BY id, component")

_t("fp_components_without_requirements", "Components in the Ref deployment whose directory has no SDD requirements",
   "MATCH (i:Instance)-[:INSTANCE_OF]->(p:Part) WHERE NOT (p)-[:SATISFIES]->(:Requirement) "
   "RETURN p.id AS id, collect(i.name) AS instances, p.has_sdd AS has_sdd ORDER BY id")

_t("fp_component_coverage", "Requirement evidence per component (cited by a unit test)",
   "MATCH (p:Part)-[:SATISFIES]->(r:Requirement) "
   "WITH p, r, EXISTS { MATCH (r)<-[:CITES]-(:TestFile) } AS cited "
   "WITH p, count(r) AS requirements, sum(CASE WHEN cited THEN 1 ELSE 0 END) AS cited "
   "RETURN p.id AS component, requirements, cited, round(100.0 * cited / requirements, 1) AS percent "
   "ORDER BY percent, component", id_columns=())

_t("fp_component_impact", "Change impact of a component through the Ref deployment's port connections",
   "MATCH (p:Part {id: $id}) "
   "OPTIONAL MATCH (p)<-[:INSTANCE_OF]-(src:Instance) "
   f"OPTIONAL MATCH (src)-[:CONNECTS_TO*1..{FP_IMPACT_HOPS}]->(dst:Instance) "
   "WITH p, collect(DISTINCT src) AS srcs, collect(DISTINCT dst) AS dsts "
   "WITH p, srcs, [d IN dsts WHERE NOT d IN srcs] AS downstream "
   "UNWIND CASE WHEN size(downstream) = 0 THEN [null] ELSE downstream END AS d "
   "OPTIONAL MATCH (d)-[:INSTANCE_OF]->(q:Part) "
   "WITH p, srcs, downstream, collect(DISTINCT q) AS parts "
   "WITH srcs, downstream, [x IN parts WHERE x <> p] AS parts, [p] + [x IN parts WHERE x <> p] AS scope "
   "UNWIND scope AS s "
   "OPTIONAL MATCH (s)-[:SATISFIES]->(r:Requirement) "
   "OPTIONAL MATCH (r)-[:VERIFIED_BY]->(v:Verification) "
   "WITH srcs, downstream, parts, collect(DISTINCT r.id) AS requirements, collect(DISTINCT v.id) AS verifications "
   "RETURN [x IN srcs | x.id] AS instances, [x IN downstream | x.id] AS downstream_instances, "
   "[x IN parts | x.id] AS parts, requirements, verifications, "
   "size(downstream) + size(requirements) + size(verifications) AS blast_radius",
   params=("id",), id_columns=("downstream_instances", "parts", "requirements", "verifications"))

_t("fp_instance_neighbours", "Port connections of one Ref deployment instance",
   "MATCH (i:Instance {id: $id})-[c:CONNECTS_TO]-(o:Instance) "
   "RETURN o.id AS id, CASE WHEN startNode(c) = i THEN 'out' ELSE 'in' END AS direction, "
   "c.src_port AS src_port, c.dst_port AS dst_port, c.graph AS graph ORDER BY direction, id",
   params=("id",))

_t("fp_requirement_evidence", "Declared verification and citing unit tests of one requirement",
   "MATCH (r:Requirement {id: $id}) OPTIONAL MATCH (r)-[:VERIFIED_BY]->(v:Verification) "
   "OPTIONAL MATCH (t:TestFile)-[:CITES]->(r) "
   "RETURN r.id AS requirement, r.text AS text, r.methods_raw AS declared, collect(DISTINCT v.id) AS verifications, "
   "collect(DISTINCT t.id) AS test_files, r.source AS source",
   params=("id",), id_columns=("verifications", "test_files"))

_t("fp_part_requirements", "Requirements of one component, with citing unit tests",
   "MATCH (p:Part {id: $id})-[:SATISFIES]->(r:Requirement) OPTIONAL MATCH (t:TestFile)-[:CITES]->(r) "
   "RETURN r.id AS id, r.methods AS methods, collect(t.id) AS cited_by ORDER BY id", params=("id",))

_t("fp_part_by_type", "Component directory for an FPP type name",
   "MATCH (p:Part) WHERE $type IN p.types RETURN p.id AS id", params=("type",))

def run(db, name: str, **params) -> tuple[list[dict], str, dict]:
    """Execute a template. Returns (rows as plain dicts, cypher text, bound params)."""
    t = T[name]
    bound = {**t.defaults, **params}
    if "q" in t.params and isinstance(bound.get("q"), str):
        bound["q"] = embed(bound["q"])
        bound.setdefault("dim", DIM)
    missing = [p for p in t.params if p not in bound]
    if missing:
        raise ValueError(f"template {name} needs params {missing}")
    rows = [r.data() for r in db.execute(t.cypher, {k: bound[k] for k in t.params})]
    return rows, t.cypher, {k: v for k, v in bound.items() if k not in ("q", "dim")}


def answer_ids(name: str, rows: list[dict]) -> set[str]:
    """Collect the answer-id set from the template's id columns."""
    out: set[str] = set()
    for row in rows:
        for col in T[name].id_columns:
            v = row.get(col)
            if isinstance(v, list):
                out.update(x for x in v if x is not None)
            elif v is not None:
                out.add(v)
    return out
