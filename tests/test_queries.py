"""Every Cypher template must agree with the independently computed ground truth."""
import pytest

from gonogo.queries import answer_ids, run

SIMPLE = [
    "unverified_requirements", "failed_requirements", "open_requirements", "analysis_only_requirements",
    "orphan_parts", "unsatisfied_requirements", "high_risks", "unmitigated_high_risks",
    "unmitigated_risks", "suspect_verifications", "dependency_cycles",
]


@pytest.mark.parametrize("name", SIMPLE)
def test_simple_sets(db, truth, name):
    rows, _, _ = run(db, name)
    assert answer_ids(name, rows) == set(truth[name])


def test_planted_defects_present(truth):
    # the generator plants these; if they vanish the evaluation would be meaningless
    assert truth["unverified_requirements"] == ["REQ-ADCS-05", "REQ-PAY-04", "REQ-THERM-03"]
    assert truth["orphan_parts"] == ["PART-COMMS-SBAND-01", "PART-PAY-FW-02"]
    assert truth["unmitigated_high_risks"] == ["RISK-04", "RISK-11"]
    assert truth["unsatisfied_requirements"] == ["REQ-OBC-05"]
    assert len(truth["open_decision_conflicts"]) == 2


def test_conflicts(db, truth):
    rows, _, _ = run(db, "open_decision_conflicts")
    assert sorted([r["a"], r["b"]] for r in rows) == truth["open_decision_conflicts"]


def test_closure(db, truth):
    rows, _, _ = run(db, "closure_by_subsystem")
    got = {r["subsystem"]: {"total": r["total"], "closed": r["closed"], "percent": r["percent"]} for r in rows}
    assert got == truth["closure_by_subsystem"]


def test_most_connected(db, truth):
    rows, _, _ = run(db, "most_connected_parts")
    assert answer_ids("most_connected_parts", rows) == set(truth["most_connected_parts"]["parts"])


def test_part_impact_all_parts(db, truth):
    for pid, exp in truth["part_impact"].items():
        rows, _, _ = run(db, "part_impact", id=pid)
        (row,) = rows
        assert sorted(row["parts"]) == exp["parts"], pid
        assert sorted(row["requirements"]) == exp["requirements"], pid
        assert sorted(row["verifications"]) == exp["verifications"], pid
        assert row["blast_radius"] == exp["blast_radius"], pid


def test_requirement_impact_all(db, truth):
    for rid, exp in truth["requirement_impact"].items():
        (row,) = run(db, "requirement_impact", id=rid)[0]
        for k in ("requirements", "parts", "verifications"):
            assert sorted(row[k]) == exp[k], (rid, k)


def test_trace_up_all(db, truth):
    for rid, exp in truth["trace_up"].items():
        rows, _, _ = run(db, "trace_up", id=rid)
        assert answer_ids("trace_up", rows) == set(exp), rid


def test_risks_threatening(db, truth):
    for x, exp in truth["risks_threatening"].items():
        rows, _, _ = run(db, "risks_threatening", id=x)
        assert answer_ids("risks_threatening", rows) == set(exp), x


def test_decisions_and_verifications(db, truth):
    for d, exp in truth["decision_affects"].items():
        assert answer_ids("decision_affects", run(db, "decision_affects", id=d)[0]) == set(exp)
    for r, exp in truth["verifications_of"].items():
        assert answer_ids("verifications_of", run(db, "verifications_of", id=r)[0]) == set(exp)


def test_supplier(db, truth):
    for s, exp in truth["parts_by_supplier"].items():
        assert answer_ids("parts_by_supplier", run(db, "parts_by_supplier", supplier=s.upper())[0]) == set(exp)


def test_vector_and_fulltext_search(db):
    rows, _, _ = run(db, "vector_search", q="reaction wheel triad", k=3)
    assert "PART-ADCS-RW-01" in {r["id"] for r in rows}
    rows, _, _ = run(db, "fulltext_search", text="link budget", k=5)
    assert "DOC-03" in {r["id"] for r in rows}
    rows, _, _ = run(db, "graph_rag", q="X-band link budget margin", k=2)
    assert rows and rows[0]["context"]


def test_read_only_store_refuses_writes(db):
    with pytest.raises(Exception):
        db.execute("CREATE (:Entity {id: 'x'})")
