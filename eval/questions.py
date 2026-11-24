"""Benchmark questions for the synthetic KESTREL-3U dataset.

Expected answers are taken from ground_truth.json (computed in plain Python, not by Relate).
`labels` tells the retrieval baselines which node types count as an answer, which gives them
a fair chance: they do not have to guess the answer type.
"""
from __future__ import annotations

LBL_REQ, LBL_PART, LBL_RISK, LBL_DEC, LBL_VER = "Requirement", "Part", "Risk", "Decision", "Verification"


def build_questions(gt: dict) -> list[dict]:
    qs: list[dict] = []

    def add(cat: str, text: str, expected, labels: list[str]) -> None:
        qs.append({"qid": f"Q{len(qs) + 1:02d}", "category": cat, "text": text,
                   "expected": sorted(set(expected)), "labels": labels, "answerable": True})

    def unknown(text: str) -> None:
        qs.append({"qid": f"Q{len(qs) + 1:02d}", "category": "out_of_scope", "text": text,
                   "expected": [], "labels": [], "answerable": False})

    add("coverage", "Which requirements have no verification?", gt["unverified_requirements"], [LBL_REQ])
    add("coverage", "List the requirements that are not verified by any test, analysis or inspection.", gt["unverified_requirements"], [LBL_REQ])
    add("coverage", "Which requirements are linked to a failed verification?", gt["failed_requirements"], [LBL_REQ])
    add("coverage", "Which requirements are still open?", gt["open_requirements"], [LBL_REQ])
    add("coverage", "Which requirements are verified only by analysis?", gt["analysis_only_requirements"], [LBL_REQ])
    add("coverage", "Which requirements are not satisfied by any part?", gt["unsatisfied_requirements"], [LBL_REQ])
    add("coverage", "Which passed verifications have anomalies recorded against them?", gt["suspect_verifications"], [LBL_VER])
    add("structure", "Are there orphan parts?", gt["orphan_parts"], [LBL_PART])
    add("structure", "Which parts are not linked to any requirement?", gt["orphan_parts"], [LBL_PART])
    add("structure", "Which part satisfies the most requirements?", gt["most_connected_parts"]["parts"], [LBL_PART])
    add("structure", "Are there circular dependencies between parts?", gt["dependency_cycles"], [LBL_PART])
    add("risk", "Which high risks have no mitigation?", gt["unmitigated_high_risks"], [LBL_RISK])
    add("risk", "List critical risks without a mitigation.", gt["unmitigated_high_risks"], [LBL_RISK])
    add("risk", "What are the high risks?", gt["high_risks"], [LBL_RISK])
    add("decision", "Are there conflicting decisions?", [x for pair in gt["open_decision_conflicts"] for x in pair], [LBL_DEC])
    add("decision", "Which accepted decisions contradict each other?", [x for pair in gt["open_decision_conflicts"] for x in pair], [LBL_DEC])

    impact_labels = [LBL_PART, LBL_REQ, LBL_VER]

    def pi(pid: str) -> list[str]:
        x = gt["part_impact"][pid]
        return x["parts"] + x["requirements"] + x["verifications"]

    def ri(rid: str) -> list[str]:
        x = gt["requirement_impact"][rid]
        return x["requirements"] + x["parts"] + x["verifications"]

    add("impact", "What is affected if PART-ADCS-RW-01 changes?", pi("PART-ADCS-RW-01"), impact_labels)
    add("impact", "What is the blast radius of a change to PART-OBC-CPU-01?", pi("PART-OBC-CPU-01"), impact_labels)
    add("impact", "If PART-THERM-RAD-01 is redesigned, which requirements and verifications are impacted?", pi("PART-THERM-RAD-01"), impact_labels)
    add("impact", "What breaks if PART-PAY-OPT-01 is replaced?", pi("PART-PAY-OPT-01"), impact_labels)
    add("impact", "What is affected if the reaction wheel triad changes?", pi("PART-ADCS-RW-01"), impact_labels)
    add("impact", "If we replace the X-band transmitter, what needs to be re-verified?", pi("PART-COMMS-XTX-01"), impact_labels)
    add("impact", "What is impacted if REQ-SYS-01 changes?", ri("REQ-SYS-01"), impact_labels)
    add("impact", "What is the impact of changing REQ-SYS-03?", ri("REQ-SYS-03"), impact_labels)
    add("impact", "If REQ-MIS-01 changes, what is affected downstream?", ri("REQ-MIS-01"), impact_labels)

    add("trace", "Why does REQ-EPS-01 exist? Trace it up to the mission.", gt["trace_up"]["REQ-EPS-01"], [LBL_REQ])
    add("trace", "What are the parent requirements of REQ-ADCS-02?", gt["trace_up"]["REQ-ADCS-02"], [LBL_REQ])
    add("trace", "Trace REQ-PAY-03 to its mission requirement.", gt["trace_up"]["REQ-PAY-03"], [LBL_REQ])
    add("risk", "Which risks threaten REQ-ADCS-02?", gt["risks_threatening"]["REQ-ADCS-02"], [LBL_RISK])
    add("risk", "What risks affect PART-ADCS-RW-01?", gt["risks_threatening"]["PART-ADCS-RW-01"], [LBL_RISK])
    add("risk", "Which risks threaten REQ-EPS-02?", gt["risks_threatening"]["REQ-EPS-02"], [LBL_RISK])
    add("coverage", "How is REQ-COMMS-03 verified?", gt["verifications_of"]["REQ-COMMS-03"], [LBL_VER])
    add("coverage", "Which verifications cover REQ-EPS-02?", gt["verifications_of"]["REQ-EPS-02"], [LBL_VER])
    add("decision", "What does DEC-03 affect?", gt["decision_affects"]["DEC-03"], [LBL_PART, LBL_REQ])
    add("decision", "Which items are affected by DEC-09?", gt["decision_affects"]["DEC-09"], [LBL_PART, LBL_REQ])
    add("supplier", "Which parts come from Gyrodyne Labs?", gt["parts_by_supplier"]["Gyrodyne Labs"], [LBL_PART])
    add("supplier", "List all parts supplied by Skylink RF.", gt["parts_by_supplier"]["Skylink RF"], [LBL_PART])

    unknown("What is the launch date?")
    unknown("Who is the project manager?")
    unknown("How much does the star tracker cost?")
    unknown("What is the impact of PART-ADCS-XX-99?")
    unknown("Which requirements did the customer reject in 2019?")
    return qs
