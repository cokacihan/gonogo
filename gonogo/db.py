"""Build and open the persistent Relate stores."""
from __future__ import annotations

import json
import shutil
from collections import defaultdict
from pathlib import Path

import relate

from gonogo.embed import DIM, embed

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "tracegraph.rl"

FPRIME_DB_PATH = DATA_DIR / "fprime"  # directory mode: WAL + Parquet checkpoint (compact, git-friendly)
FPRIME_DATASET = DATA_DIR / "fprime_dataset.json"

LABELS = ["Subsystem", "Requirement", "Verification", "Part", "Risk", "Decision", "Anomaly", "Document",
          "TestFile", "Instance"]
REL_TYPES = ["DERIVES_FROM", "ALLOCATED_TO", "VERIFIED_BY", "PART_OF", "DEPENDS_ON", "SATISFIES",
             "THREATENS", "MITIGATED_BY", "AFFECTS", "CONFLICTS_WITH", "OBSERVED_IN", "INVOLVES",
             "DOCUMENTED_IN", "CITES", "EVIDENCED_BY", "TESTS", "INSTANCE_OF", "CONNECTS_TO"]


def _remove_store(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def build(dataset: dict, path: Path = DB_PATH) -> dict:
    """(Re)create the store from a dataset dict. Returns counts read back from the graph."""
    _remove_store(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = relate.connect(str(path))
    try:
        db.execute("CREATE INDEX entity_id IF NOT EXISTS FOR (n:Entity) ON (n.id)")
        for label in LABELS:
            rows = [dict(n, embedding=embed(n["text"])) for n in dataset["nodes"].get(label, [])]
            if not rows:
                continue
            # Label names come from the fixed LABELS whitelist, never from user input.
            db.execute(
                f"UNWIND $rows AS r CREATE (n:Entity:{label}) "
                "SET n = r, n.embedding = vector(r.embedding, $dim, FLOAT32)",
                {"rows": rows, "dim": DIM},
            )
        by_type: dict[str, list[dict]] = defaultdict(list)
        for e in dataset["edges"]:
            props = {k: v for k, v in e.items() if k not in ("src", "type", "dst")}
            by_type[e["type"]].append({"src": e["src"], "dst": e["dst"], "props": props})
        for rel, rows in by_type.items():
            if rel not in REL_TYPES:
                raise ValueError(f"unknown relationship type {rel}")
            cur = db.execute(
                f"UNWIND $rows AS e MATCH (a:Entity {{id: e.src}}), (b:Entity {{id: e.dst}}) "
                f"CREATE (a)-[r:{rel}]->(b) SET r = e.props",
                {"rows": rows},
            )
            n_created = cur.consume().counters["relationships_created"]
            if n_created != len(rows):
                raise RuntimeError(f"{rel}: {n_created} of {len(rows)} relationships created (dangling id?)")
        db.execute(
            "CREATE VECTOR INDEX entity_embedding IF NOT EXISTS FOR (n:Entity) ON (n.embedding) "
            "OPTIONS {indexConfig: {`vector.dimensions`: %d, `vector.similarity_function`: 'cosine'}}" % DIM
        )
        db.execute("CREATE FULLTEXT INDEX entity_text IF NOT EXISTS FOR (n:Entity) ON EACH [n.text, n.id]")
        src = dataset.get("source", {})
        db.execute("MERGE (m:Meta {key: 'dataset'}) SET m.kind = $kind, m.seed = $seed, m.mission = $mission, "
                   "m.source_repo = $repo, m.source_commit = $commit, m.source_license = $lic",
                   {"kind": dataset.get("kind", "synthetic"), "seed": dataset.get("seed"),
                    "mission": dataset["mission"], "repo": src.get("repo"), "commit": src.get("commit"),
                    "lic": src.get("license")})
        nodes = db.execute("MATCH (n:Entity) RETURN count(n) AS n").single()["n"]
        rels = db.execute("MATCH ()-[r]->() RETURN count(r) AS n").single()["n"]
    finally:
        db.close()
    return {"nodes": nodes, "relationships": rels, "path": str(path)}


def connect(path: Path = DB_PATH, read_only: bool = True) -> relate.Connection:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run: python -m gonogo build")
    return relate.connect(str(path), read_only=read_only)


def export_json(path: Path = DB_PATH) -> dict:
    """Export nodes/edges (without embeddings) for the browser (Relate WASM) demo."""
    db = connect(path)
    try:
        nodes = [r.data() for r in db.execute(
            "MATCH (n:Entity) RETURN n.id AS id, [l IN labels(n) WHERE l <> 'Entity'][0] AS label, "
            "properties(n) AS props ORDER BY id")]
        for n in nodes:
            n["props"].pop("embedding", None)
        edges = [r.data() for r in db.execute(
            "MATCH (a:Entity)-[r]->(b:Entity) RETURN a.id AS src, type(r) AS type, b.id AS dst "
            "ORDER BY src, type, dst")]
    finally:
        db.close()
    return {"nodes": nodes, "edges": edges}


def dataset_kind(db) -> str:
    row = db.execute("MATCH (m:Meta {key: 'dataset'}) RETURN m.kind AS k").fetchone()
    return (row["k"] if row else None) or "synthetic"


if __name__ == "__main__":
    print(build(json.loads((DATA_DIR / "dataset.json").read_text(encoding="utf-8"))))
    if FPRIME_DATASET.exists():
        print(build(json.loads(FPRIME_DATASET.read_text(encoding="utf-8")), FPRIME_DB_PATH))
