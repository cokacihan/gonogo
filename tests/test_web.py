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


def test_browser_code_on_relate_wasm_matches_the_cli():
    """The explorer's own JavaScript, on the vendored Relate WebAssembly build (Node), gives the CLI's answers."""
    import shutil
    import subprocess

    import pytest

    from gonogo.db import connect
    from gonogo.impact import impact
    from gonogo.verdict import current_run, deployment_parts, poll, verdicts
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    out = subprocess.run(["node", str(Path(__file__).with_name("web_parity.mjs"))], capture_output=True, text=True,
                         encoding="utf-8", check=True).stdout
    web = json.loads(out)
    db = connect()  # the committed store; web/data is exported from it
    rows = verdicts(db, current_run(db))
    assert web["verdicts"] == {r["id"]: [r["verdict"], sorted(r["reasons"])] for r in rows}
    table = poll(rows, deployment_parts(db))
    assert web["poll"] == {p["component"]: [p["verdict"], p["GO"], p["NO-GO"], p["INCOMPLETE"], p["in_deployment"]]
                           for p in table}
    for w in web["impacts"]:
        res = impact(db, files=w["files"], verdict_rows=rows)
        assert w["components"] == sorted(r["component"] for r in res["rerun"]), w["files"]
        assert (w["test_cases"], w["requirements"], w["go_pending"]) == \
            (res["test_cases"], len(res["requirements"]), len(res["go_pending_rerun"])), w["files"]
    db.close()
