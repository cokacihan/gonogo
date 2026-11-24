"""Parameterised, read-only Cypher templates over the F´ graph.

Each template declares which result columns form its answer set, so the agent, the CLI and the
tests can show exactly which rows support which claim.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from gonogo.embed import DIM, embed


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


# ------------------------------------------------------------------ hygiene findings
_t("fp_unknown_citations", "Test files citing requirement IDs that no SDD defines",
   "MATCH (t:TestFile) WHERE size(t.unknown_citations) > 0 "
   "OPTIONAL MATCH (t)-[:TESTS]->(p:Part) "
   "RETURN t.id AS file, p.id AS component, t.unknown_citations AS unknown_ids ORDER BY file",
   id_columns=("unknown_ids",))

_t("fp_duplicate_requirement_ids", "Requirement IDs defined more than once",
   "MATCH (r:Requirement) WITH split(r.id, '@')[0] AS ref, collect(r) AS rs WHERE size(rs) > 1 "
   "UNWIND rs AS r RETURN ref AS id, r.component AS component, r.source AS source ORDER BY id, source")

_t("fp_cross_component_citations", "Requirements cited by unit tests of another component",
   "MATCH (tc:TestCase)-[:CITES]->(r:Requirement)<-[:SATISFIES]-(p:Part), (tc)-[:TESTS]->(q:Part) WHERE p <> q "
   "RETURN r.id AS id, p.id AS owner, q.id AS cited_by, count(tc) AS test_cases ORDER BY id, cited_by")

_t("unverified_requirements", "Requirements whose SDD row names no verification method",
   "MATCH (p:Part)-[:SATISFIES]->(r:Requirement) WHERE NOT (r)-[:VERIFIED_BY]->() "
   "RETURN r.id AS id, p.id AS component, r.methods_raw AS declared ORDER BY component, id")

_t("fp_components_without_requirements", "Components in the Ref deployment whose directory has no SDD requirements",
   "MATCH (i:Instance)-[:INSTANCE_OF]->(p:Part) WHERE NOT (p)-[:SATISFIES]->(:Requirement) "
   "RETURN p.id AS id, collect(i.name) AS instances, p.has_sdd AS has_sdd ORDER BY id")

# ------------------------------------------------------------------ entity lookups
_t("fp_requirement_evidence", "Test cases citing one requirement",
   "MATCH (p:Part)-[:SATISFIES]->(r:Requirement {id: $id}) "
   "OPTIONAL MATCH (tc:TestCase)-[:CITES]->(r) OPTIONAL MATCH (tc)-[:TESTS]->(q:Part) "
   "RETURN r.id AS requirement, p.id AS component, r.text AS text, r.methods_raw AS declared, r.source AS source, "
   "collect(tc.id) AS test_cases, collect(DISTINCT q.id) AS tested_by_components",
   params=("id",), id_columns=("test_cases",))

_t("fp_part_requirements", "Requirements of one component and the test cases citing them",
   "MATCH (p:Part {id: $id})-[:SATISFIES]->(r:Requirement) "
   "OPTIONAL MATCH (tc:TestCase)-[:TESTS]->(p) WHERE (tc)-[:CITES]->(r) "
   "RETURN r.id AS id, r.methods AS methods, collect(tc.gtest_filter) AS cited_by ORDER BY id", params=("id",))

_t("fp_port_type_users", "Components using one port type",
   "MATCH (p:Part)-[u:USES_PORT]->(t:PortType {id: $id}) "
   "RETURN p.id AS id, u.port AS port, u.direction AS direction, u.kind AS kind ORDER BY id, port", params=("id",))

_t("fp_instance_neighbours", "Port connections of one Ref deployment instance (integration view)",
   "MATCH (i:Instance {id: $id})-[c:CONNECTS_TO]-(o:Instance) "
   "RETURN o.id AS id, CASE WHEN startNode(c) = i THEN 'out' ELSE 'in' END AS direction, "
   "c.src_port AS src_port, c.dst_port AS dst_port, c.graph AS graph ORDER BY direction, id", params=("id",))

_t("fp_part_by_type", "Component directory for an FPP type name",
   "MATCH (p:Part) WHERE $type IN p.types RETURN p.id AS id", params=("type",))

_t("describe", "Neighbourhood of one entity",
   "MATCH (n:Entity {id: $id}) OPTIONAL MATCH (n)-[rel]-(m:Entity) "
   "RETURN n.id AS node, n.text AS text, type(rel) AS rel, "
   "CASE WHEN startNode(rel) = n THEN 'out' ELSE 'in' END AS direction, m.id AS id ORDER BY rel, id LIMIT 200",
   params=("id",))

# ------------------------------------------------------------------ search (Relate vector / full-text indexes)
# The embedding is a lexical hashed-trigram vector (gonogo.embed): used for fuzzy name resolution,
# not as a semantic model.
_t("vector_search", "Nearest entities by lexical similarity (Relate vector index)",
   "MATCH (n:Entity) SEARCH n IN (VECTOR INDEX entity_embedding FOR vector($q, $dim, FLOAT32) LIMIT $k) "
   "SCORE AS score RETURN n.id AS id, [l IN labels(n) WHERE l <> 'Entity'][0] AS label, score, n.text AS text "
   "ORDER BY score DESC, id", params=("q", "dim", "k"))

_t("vector_search_label", "Nearest entities of one label (exact cosine scan in Relate)",
   "MATCH (n:Entity) WHERE $label IN labels(n) "
   "WITH n, vector.similarity.cosine(n.embedding, vector($q, $dim, FLOAT32)) AS score "
   "RETURN n.id AS id, $label AS label, score, n.text AS text ORDER BY score DESC, id LIMIT $k",
   params=("q", "dim", "label", "k"))

_t("fulltext_search", "Full-text matches (Relate full-text index)",
   "CALL db.index.fulltext.queryNodes('entity_text', $text) YIELD node, score "
   "RETURN node.id AS id, [l IN labels(node) WHERE l <> 'Entity'][0] AS label, score, node.text AS text "
   "ORDER BY score DESC, id LIMIT $k", params=("text", "k"))


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
    out: set[str] = set()
    for row in rows:
        for col in T[name].id_columns:
            v = row.get(col)
            if isinstance(v, list):
                out.update(x for x in v if x is not None)
            elif v is not None:
                out.add(v)
    return out
