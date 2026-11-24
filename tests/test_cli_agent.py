"""CLI smoke tests, gtest report parsing, and agent routing on the real F´ graph."""
import json

from gonogo.agent import Agent
from gonogo.cli import main
from gonogo.testresults import component_of, parse_dir

XML = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites><testsuite name="Test">
  <testcase name="NominalTlm" status="run" result="completed" time="0.01" classname="Test" />
  <testcase name="WarningTlm" status="run" result="completed" time="0.02" classname="Test"><failure message="x"/></testcase>
  <testcase name="FaultTlm" status="notrun" result="suppressed" time="0" classname="Test" />
</testsuite></testsuites>
"""


def test_gtest_xml_parsing(tmp_path):
    (tmp_path / "Svc_Health_ut_exe.xml").write_text(XML, encoding="utf-8")
    res = parse_dir(tmp_path)
    assert [(c["id"], c["status"]) for c in res["cases"]] == [
        ("Svc/Health::Test.NominalTlm", "PASSED"), ("Svc/Health::Test.WarningTlm", "FAILED"),
        ("Svc/Health::Test.FaultTlm", "SKIPPED")]
    assert component_of("Svc_Ccsds_TcDeframer_ut_exe", {"Svc/Ccsds/TcDeframer"}) == "Svc/Ccsds/TcDeframer"


def test_cli_commands(store_run, capsys):
    s = ["--store", str(store_run)]
    assert main(s + ["poll", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["run"] == "run:synthetic-7" and out["components"]
    assert main(s + ["why", "HTH-001"]) == 0
    assert "CITED_UNDER_DIFFERENT_ID" in capsys.readouterr().out
    assert main(s + ["why", "NOPE-999"]) == 2
    capsys.readouterr()
    assert main(s + ["impact", "--files", "Svc/Sched/Sched.fpp", "--json"]) == 0
    assert "port:Svc.Sched" in json.loads(capsys.readouterr().out)["port_types"]
    assert main(s + ["findings", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["fp_unknown_citations"]


def test_agent_routes_and_abstains(db):
    ag = Agent(db)
    cases = {
        "What should I re-run if I change Svc/Health?": "impact",
        "What breaks if I modify the Svc.Sched port?": "impact",
        "What is the impact of changing the command dispatcher?": "impact",
        "Is CD-002 GO?": "verdict",
        "Which test files cite undefined requirement IDs?": "fp_unknown_citations",
        "Are there duplicate requirement IDs?": "fp_duplicate_requirement_ids",
        "Which requirements are NO-GO?": "verdict:NO-GO",
        "Show the readiness poll": "poll",
        "What is rateGroupDriverComp connected to?": "fp_instance_neighbours",
    }
    for q, tmpl in cases.items():
        a = ag.ask(q)
        assert a.status == "answered" and a.template == tmpl, (q, a.template, a.text)
        assert a.cypher
    for q in ["How is ABC-999 verified?", "What is the launch date?", "What breaks if Svc.NoSuchThing changes?"]:
        a = ag.ask(q)
        assert a.status == "unknown" and a.answer_ids == [], q
