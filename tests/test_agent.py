"""Agent behaviour: correct template, evidence attached, honest abstention."""
from gonogo.agent import Agent


def test_answers_carry_cypher_evidence(db, truth):
    a = Agent(db).ask("Which requirements have no verification?")
    assert a.status == "answered" and a.template == "unverified_requirements"
    assert a.cypher and a.rows
    assert a.answer_ids == truth["unverified_requirements"]
    # every id in the answer appears in the evidence rows
    assert set(a.answer_ids) <= {r["id"] for r in a.rows}


def test_unknown_entity_is_not_guessed(db):
    a = Agent(db).ask("What is the impact of PART-ADCS-XX-99?")
    assert a.status == "unknown" and a.answer_ids == []
    assert a.suggestions  # nearest nodes offered, but not as an answer


def test_out_of_scope_abstains(db):
    for q in ["What is the launch date?", "Who is the project manager?"]:
        a = Agent(db).ask(q)
        assert a.status == "unknown" and a.answer_ids == [], q


def test_name_resolution_through_vector_index(db, truth):
    a = Agent(db).ask("What breaks if the reaction wheel triad changes?")
    assert a.template == "part_impact"
    assert a.resolved and a.resolved[0]["id"] == "PART-ADCS-RW-01"
    exp = truth["part_impact"]["PART-ADCS-RW-01"]
    assert set(a.answer_ids) == set(exp["parts"] + exp["requirements"] + exp["verifications"])


def test_risk_question_with_affect_word(db, truth):
    a = Agent(db).ask("What risks affect PART-ADCS-RW-01?")
    assert a.template == "risks_threatening"
    assert a.answer_ids == truth["risks_threatening"]["PART-ADCS-RW-01"]


def test_supplier_and_closure(db, truth):
    ag = Agent(db)
    assert ag.ask("Which parts come from Gyrodyne Labs?").answer_ids == truth["parts_by_supplier"]["Gyrodyne Labs"]
    c = ag.ask("Show verification closure per subsystem")
    assert c.template == "closure_by_subsystem" and "EPS" in c.text
