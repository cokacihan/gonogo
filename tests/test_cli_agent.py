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
    for q in ["How is ABC-999 verified?", "What is the launch date?", "What breaks if Svc.NoSuchThing changes?",
              "What breaks if I change the weather?", "What breaks if I change the pizza oven?", "Is the moon GO?",
              "What should I re-run if I change it?"]:
        a = ag.ask(q)
        assert a.status == "unknown" and a.answer_ids == [], (q, a.text)


def test_agent_resolves_names_by_whole_words_and_asks_when_ambiguous(db):
    ag = Agent(db)
    a = ag.ask("What is the impact of changing the command dispatcher?")
    assert a.template == "impact" and a.answer_ids == ["Svc/CmdDispatcher"]
    a = ag.ask("What breaks if I change the component that sends telemetry?")
    assert a.status == "unknown" and "Svc/TlmChan" in a.text and "Svc/TlmPacketizer" in a.text
    a = ag.ask("Is the health component ready to fly?")
    assert a.template == "component_verdict" and a.answer_ids == ["Svc/Health"] and "not flight readiness" in a.text
    a = ag.ask("Which components are fully GO?")
    assert a.template == "components_go" and all("::" not in x and "/" in x for x in a.answer_ids)


def test_results_map_subdirectory_executables_to_component():
    from gonogo.db import results_edges
    res = {"fprime_commit": "x", "run": {"id": "1"},
           "cases": [{"id": "Drv/Ip/Tcp/test::E.T", "status": "PASSED", "time_s": 0.0},
                     {"id": "CFDP/Checksum::C.One", "status": "PASSED", "time_s": 0.0}]}
    run, edges, unmapped = results_edges(res, {"Drv/Ip::E.T"})
    assert run["id"] == "run:1"
    assert [(e["src"], e["status"]) for e in edges] == [("Drv/Ip::E.T", "PASSED")]
    assert unmapped == ["CFDP/Checksum::C.One"]


def test_diff_of_identical_graphs_is_empty(store_norun):
    from gonogo.db import connect
    from gonogo.diff import check, diff, snapshot
    a = connect(store_norun)
    s = snapshot(a)
    d = diff(s, s)
    assert not any(d["requirements"][k] for k in ("added", "removed", "text_changed", "methods_changed"))
    assert all(not v["new"] for v in d["findings"].values())
    _, md, code = check(a, a, ["Svc/Health/Health.cpp"])
    assert code == 0 and "PASS" in md and "Svc/Health" in md


def test_mcp_server_tools(store_run):
    from gonogo.mcp_server import Server
    srv = Server(store_run)
    init = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["capabilities"] == {"tools": {}}
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = {t["name"] for t in srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]}
    assert names == {"poll", "why", "tests_to_rerun", "findings", "ask"}
    r = srv.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "tests_to_rerun", "arguments": {"files": ["Svc/Health/Health.cpp"]}}})
    out = json.loads(r["result"]["content"][0]["text"])
    assert out["components"] == ["Svc/Health"] and out["test_cases"] > 0
    bad = srv.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nope", "arguments": {}}})
    assert bad["result"]["isError"] is True
