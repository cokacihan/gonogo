"""The Cypher verdict must equal the independent Python reference for every requirement."""
from collections import Counter

import pytest

from gonogo.verdict import current_run, deployment_parts, poll, reference_verdicts, verdicts


def _compare(rows, ref):
    assert len(rows) == len(ref)
    bad = [(r["id"], r["verdict"], r["reasons"], ref[r["id"]]) for r in rows
           if (r["verdict"], r["reasons"]) != (ref[r["id"]]["verdict"], ref[r["id"]]["reasons"])]
    assert bad == []


def test_without_a_run_nothing_is_go(fp_data, db_norun):
    rows = verdicts(db_norun, current_run(db_norun))
    _compare(rows, reference_verdicts(fp_data, None))
    assert Counter(r["verdict"] for r in rows) == {"INCOMPLETE": len(rows)}


@pytest.mark.parametrize("accept_declared", [False, True])
def test_with_a_run_cypher_equals_reference(fp_data, fp_results, db, accept_declared):
    rows = verdicts(db, current_run(db), accept_declared=accept_declared)
    _compare(rows, reference_verdicts(fp_data, fp_results, accept_declared=accept_declared))
    kinds = Counter(r["verdict"] for r in rows)
    assert kinds["GO"] > 0 and kinds["NO-GO"] > 0 and kinds["INCOMPLETE"] > 0


def test_go_needs_every_own_citing_case_passed(db):
    for r in verdicts(db, current_run(db)):
        if r["verdict"] == "GO":
            assert r["own_tests"] and all(t.endswith(" PASSED") for t in r["own_tests"])
        if r["verdict"] == "NO-GO":
            assert any(t.endswith(" FAILED") for t in r["own_tests"])


def test_real_hygiene_findings_never_go(db):
    by = {r["id"]: r for r in verdicts(db, current_run(db))}
    assert by["HTH-001"]["verdict"] == "INCOMPLETE" and "CITED_UNDER_DIFFERENT_ID" in by["HTH-001"]["reasons"]
    assert "Svc/Health::Test.NominalTlm" in by["HTH-001"]["drift_tests"]
    assert "AMBIGUOUS_ID" in by["FH-002"]["reasons"]


def test_poll_rolls_up_components(db):
    rows = verdicts(db, current_run(db))
    table = poll(rows, deployment_parts(db))
    assert sum(p["requirements"] for p in table) == len(rows)
    for p in table:
        if p["NO-GO"]:
            assert p["verdict"] == "NO-GO"
        elif p["INCOMPLETE"]:
            assert p["verdict"] == "INCOMPLETE"
        else:
            assert p["verdict"] == "GO"
    assert any(p["in_deployment"] for p in table)
