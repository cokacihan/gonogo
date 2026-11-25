"""Build and open the persistent Relate store of an F´ repository.

Store mode: **directory** (WAL + Parquet checkpoint). Relate's directory mode checkpoints to compressed Parquet,
which keeps this graph (vector properties included) far smaller than the single-file mode; both sizes are measured
by `python -m gonogo.stats --measure-modes` and recorded in docs/stats.json. A directory store is also what a CI job
builds and diffs, so it is the committed default; `gonogo export --single-file` writes a portable `.rl` on demand.
"""
from __future__ import annotations

import json
import shutil
from collections import defaultdict
from pathlib import Path

import relate

from gonogo.embed import DIM, embed

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
STORE = DATA_DIR / "fprime"                       # committed Relate store (directory mode)
DATASET = DATA_DIR / "fprime_dataset.json"        # output of gonogo.fprime (repository text -> graph)
RESULTS = DATA_DIR / "fprime_test_results.json"   # output of the CI unit-test run (gonogo.testresults)

LABELS = ["Part", "Library", "Requirement", "Verification", "Document", "TestFile", "TestCase", "TestFunction", "PortType",
          "Instance"]
EMBED_LABELS = {"Part", "Requirement", "PortType", "Instance", "Document"}
REL_TYPES = ["SATISFIES", "DOCUMENTED_IN", "VERIFIED_BY", "CITES", "EVIDENCED_BY", "TESTS", "USES_PORT",
             "INSTANCE_OF", "CONNECTS_TO", "RAN_IN", "CALLS", "INCLUDES"]


def _remove_store(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def results_edges(results: dict | None, case_ids: set[str]) -> tuple[dict | None, list[dict], list[str]]:
    """TestRun node, RAN_IN edges for test cases we know, and result ids we could not map."""
    if not results:
        return None, [], []
    run_id = f"run:{results['run'].get('id') or 'local'}"
    run = {"id": run_id, "fprime_commit": results["fprime_commit"], "url": results["run"].get("url", ""),
           "runner": results["run"].get("runner", ""), "text": f"F Prime unit-test run {run_id}"}
    edges, unmapped, seen = [], [], set()
    for c in results["cases"]:
        comp, _, name = c["id"].partition("::")
        parts = comp.split("/")
        # executables built from a sub-directory (e.g. Drv/Ip/Tcp/test) belong to the closest component above it
        cid = next((f"{'/'.join(parts[:k])}::{name}" for k in range(len(parts), 0, -1)
                    if f"{'/'.join(parts[:k])}::{name}" in case_ids), None)
        if cid and cid not in seen:
            seen.add(cid)
            edges.append({"src": cid, "type": "RAN_IN", "dst": run_id, "status": c["status"], "time_s": c["time_s"],
                          "reported_as": c["id"]})
        else:
            unmapped.append(c["id"])
    return run, edges, unmapped


def build(dataset: dict, path: Path = STORE, results: dict | None = None) -> dict:
    """(Re)create the store. Returns counts read back from the graph plus result-mapping diagnostics."""
    _remove_store(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    nodes_by_label = {k: list(v) for k, v in dataset["nodes"].items()}
    edges = list(dataset["edges"])
    case_ids = {c["id"] for c in nodes_by_label.get("TestCase", [])}
    run, run_edges, unmapped = results_edges(results, case_ids)
    if run:
        nodes_by_label["TestRun"] = [run]
        edges += run_edges
    db = relate.connect(str(path))
    try:
        db.execute("CREATE INDEX entity_id IF NOT EXISTS FOR (n:Entity) ON (n.id)")
        for label in LABELS + ["TestRun"]:  # per-label id indexes (Relate's planner hint for MATCH (:Label {id: ...}))
            db.execute(f"CREATE INDEX {label.lower()}_id IF NOT EXISTS FOR (n:{label}) ON (n.id)")
        for label in LABELS + ["TestRun"]:
            rows = nodes_by_label.get(label, [])
            if not rows:
                continue
            # label names come from the fixed whitelist above, never from input
            if label in EMBED_LABELS:  # only named things people ask about get a vector (name resolution)
                db.execute(f"UNWIND $rows AS r CREATE (n:Entity:{label}) "
                           "SET n = r, n.embedding = vector(r.embedding, $dim, FLOAT32)",
                           {"rows": [dict(n, embedding=embed(n["text"])) for n in rows], "dim": DIM})
            else:
                db.execute(f"UNWIND $rows AS r CREATE (n:Entity:{label}) SET n = r", {"rows": rows})
        by_type: dict[str, list[dict]] = defaultdict(list)
        for e in edges:
            props = {k: v for k, v in e.items() if k not in ("src", "type", "dst")}
            by_type[e["type"]].append({"src": e["src"], "dst": e["dst"], "props": props})
        for rel, rows in by_type.items():
            if rel not in REL_TYPES:
                raise ValueError(f"unknown relationship type {rel}")
            cur = db.execute(f"UNWIND $rows AS e MATCH (a:Entity {{id: e.src}}), (b:Entity {{id: e.dst}}) "
                             f"CREATE (a)-[r:{rel}]->(b) SET r = e.props", {"rows": rows})
            n_created = cur.consume().counters["relationships_created"]
            if n_created != len(rows):
                raise RuntimeError(f"{rel}: {n_created} of {len(rows)} relationships created (dangling or duplicate id)")
        db.execute("CREATE VECTOR INDEX entity_embedding IF NOT EXISTS FOR (n:Entity) ON (n.embedding) "
                   "OPTIONS {indexConfig: {`vector.dimensions`: %d, `vector.similarity_function`: 'cosine'}}" % DIM)
        db.execute("CREATE FULLTEXT INDEX entity_text IF NOT EXISTS FOR (n:Entity) ON EACH [n.text, n.id]")
        src = dataset.get("source", {})
        db.execute("MERGE (m:Meta {key: 'dataset'}) SET m.source_repo = $repo, m.source_commit = $commit, "
                   "m.source_license = $lic, m.run = $run, m.unmapped_results = $unmapped",
                   {"repo": src.get("repo"), "commit": src.get("commit"), "lic": src.get("license"),
                    "run": run["id"] if run else None, "unmapped": len(unmapped)})
        n_nodes = db.execute("MATCH (n:Entity) RETURN count(n) AS n").single()["n"]
        n_rels = db.execute("MATCH ()-[r]->() RETURN count(r) AS n").single()["n"]
    finally:
        db.close()
    return {"nodes": n_nodes, "relationships": n_rels, "path": str(path), "run": run["id"] if run else None,
            "results_mapped": len(run_edges), "results_unmapped": len(unmapped)}


def load_inputs(dataset_path: Path = DATASET, results_path: Path = RESULTS) -> tuple[dict, dict | None]:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    results = json.loads(results_path.read_text(encoding="utf-8")) if results_path.exists() else None
    if results and results.get("fprime_commit") != dataset["source"]["commit"]:
        raise ValueError(f"test results are for F´ {results.get('fprime_commit')}, "
                         f"dataset is for {dataset['source']['commit']}")
    return dataset, results


def connect(path: Path = STORE, read_only: bool = True) -> relate.Connection:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run: python -m gonogo build")
    return relate.connect(str(path), read_only=read_only)


def meta(db) -> dict:
    row = db.execute("MATCH (m:Meta {key: 'dataset'}) RETURN properties(m) AS p").fetchone()
    return row["p"] if row else {}


def export_json(path: Path = STORE) -> dict:
    """Nodes and relationships without embeddings, for the browser explorer (Relate WASM)."""
    db = connect(path)
    try:
        nodes = [r.data() for r in db.execute(
            "MATCH (n:Entity) RETURN n.id AS id, [l IN labels(n) WHERE l <> 'Entity'][0] AS label, "
            "properties(n) AS props ORDER BY id")]
        for n in nodes:
            n["props"].pop("embedding", None)
        edges = [r.data() for r in db.execute(
            "MATCH (a:Entity)-[r]->(b:Entity) RETURN a.id AS src, type(r) AS type, b.id AS dst, "
            "properties(r) AS props ORDER BY src, type, dst")]
        info = meta(db)
    finally:
        db.close()
    return {"meta": info, "nodes": nodes, "edges": edges}


if __name__ == "__main__":
    ds, res = load_inputs()
    print(build(ds, STORE, res))
