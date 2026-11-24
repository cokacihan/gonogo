"""The browser explorer must run the same Cypher as the CLI, over the same graph."""
import json
from pathlib import Path

from gonogo.impact import INTEGRATION_CYPHER, TESTS_TO_RERUN_CYPHER
from gonogo.verdict import VERDICTS_CYPHER
from gonogo.web_export import bundle

WEB = Path(__file__).resolve().parents[1] / "web" / "data"


def test_committed_queries_match_python():
    q = json.loads((WEB / "queries.json").read_text(encoding="utf-8"))
    assert q["verdicts"] == VERDICTS_CYPHER.strip()
    assert q["tests_to_rerun"] == TESTS_TO_RERUN_CYPHER.strip()
    assert q["integration"] == INTEGRATION_CYPHER.strip()


def test_committed_graph_matches_dataset_and_run(fp_data):
    g = json.loads((WEB / "graph.json").read_text(encoding="utf-8"))
    assert g["meta"]["source"]["commit"] == fp_data["source"]["commit"]
    for label, rows in fp_data["nodes"].items():
        assert len(g["nodes"][label]) == len(rows), label
    assert all("embedding" not in n for rows in g["nodes"].values() for n in rows)


def test_bundle_without_results_has_no_run(fp_data):
    b = bundle(fp_data, None)
    assert "TestRun" not in b["nodes"] and "RAN_IN" not in b["edges"]
