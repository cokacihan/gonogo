"""`gonogo suggest`: words, ranking on the real F´ graph, and the patch it writes."""
from gonogo.suggest import patch, query_terms, rank_cases, suggest
from gonogo.vocab import code_words, norm, text_words


def test_words_meet_in_the_middle():
    # identifiers split at underscores and camelCase, F´ abbreviations spelled out, light suffix stripping
    assert {"send", "command", "health", "enabl"} <= code_words("this->sendCmd_HLTH_ENABLE(0, 10);")
    assert {"command", "health", "enabl"} <= set(text_words("Health checks shall be enabled by command"))
    assert norm("pings") == norm("ping") and norm("writes") == norm("writing") == norm("write")
    assert "the" not in text_words("The component shall ping") and "shall" not in text_words("It shall")


def test_component_name_is_not_a_search_term():
    terms = query_terms("The Svc::TlmChan component shall store telemetry channels", "Svc/TlmChan")
    assert "telemetry" not in terms and "channel" not in terms and "stor" in terms


def test_near_miss_id_is_a_rename_not_a_new_test(db):
    res = suggest(db, "HTH-003")
    assert res["drift"] and res["drift"][0]["cited"] == "ISF-HTH-003"
    assert res["edit"].startswith("rename ISF-HTH-003 to HTH-003")


def test_pr5170_blockers_point_at_the_tests_written_for_them(db):
    # TLC-005/006 block nasa/fprime#5170; ProcGuardTest and checkGuardEvent are the tests of that feature
    assert suggest(db, "TLC-005")["cases"][0]["gtest_filter"] == "TlmChanTest.ProcGuardTest"
    assert suggest(db, "TLC-006")["sites"][0]["name"] == "checkGuardEvent"


def test_call_graph_lifts_a_helper_to_the_cases_that_run_it(db):
    terms = query_terms("emit a WARNING_HI event when the per-run processing cap is reached", "Svc/TlmChan")
    lifted = rank_cases(db, "TLC-006", terms, k=50)
    alone = rank_cases(db, "TLC-006", terms, k=50, lift=False)
    proc = next(c for c in lifted if c["gtest_filter"] == "TlmChanTest.ProcGuardTest")
    assert "checkGuardEvent" in proc["via"]  # reached over CALLS, not written in the TEST body
    assert proc["best"] > next(c["best"] for c in alone if c["gtest_filter"] == "TlmChanTest.ProcGuardTest")


def test_unknown_requirement(db):
    assert suggest(db, "NOPE-999") is None


def test_patch_writes_the_macro_and_the_include(tmp_path):
    src = tmp_path / "Svc/X/test/ut/XTester.cpp"
    src.parent.mkdir(parents=True)
    src.write_text('#include "XTester.hpp"\n\nvoid XTester::checkCap(U32 n);\n\n'
                   "void XTester::checkCap(U32 n) {\n    ASSERT_EVENTS_SIZE(1);\n}\n", encoding="utf-8")
    site = {"at": ["Svc/X/test/ut/XTester.hpp:3", "Svc/X/test/ut/XTester.cpp:5"]}
    diff = patch(tmp_path, "X-001@Svc/X/docs/sdd.md", site)
    added = [x[1:] for x in diff.splitlines() if x.startswith("+") and not x.startswith("+++")]
    assert added == ["#include <Fw/Test/UnitTest.hpp>", '    REQUIREMENT("X-001");']
    assert "@@ -1,7 +1,9 @@" in diff or diff.count("@@") >= 2


def test_agent_and_mcp_answer_citation_help(db, store_run):
    from gonogo.agent import Agent
    from gonogo.mcp_server import Server
    a = Agent(db).ask("Which test should cite TLC-006?")
    assert a.template == "suggest" and "checkGuardEvent" in a.text and a.params["index"] == "test_vocab"
    assert Agent(db).ask("How do I make HTH-001 GO?").text.startswith("HTH-001 is already tested under a near-miss ID")
    assert Agent(db).ask("Why is HTH-001 not GO?").template != "suggest"  # a verdict question stays a verdict
    assert Server(store_run).call("suggest", {"id": "TLC-006"})["sites"][0]["name"] == "checkGuardEvent"
