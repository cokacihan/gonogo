"""gonogo command line.

  gonogo import-fprime --repo PATH      repository text -> data/fprime_dataset.json
  gonogo build                          dataset (+ CI test results) -> Relate store data/fprime/
  gonogo poll [--all] [--json]          GO / NO-GO / INCOMPLETE per component (Ref deployment by default)
  gonogo why ID                         verdict of one requirement, with the evidence path
  gonogo suggest ID                     test code that most likely verifies an uncited requirement, and the edit
  gonogo impact --files F... | --component C... | --port-type T...
                                        unit tests to re-run, verdicts put back in question
  gonogo findings                       traceability hygiene findings (IDs that do not line up)
  gonogo ask "question"                 rule-based question answering with Cypher receipts
  gonogo diff BASE HEAD                 compare two graphs (stores or dataset JSONs), e.g. two F´ releases
  gonogo check --base B --head H [--files F...]   CI gate: PASS/FAIL + unit tests to re-run
  gonogo export --single-file OUT.rl | --json OUT.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from gonogo import db as store
from gonogo.impact import impact as compute_impact, reason
from gonogo.suggest import patch, suggest
from gonogo.verdict import (VERDICTS_CYPHER, current_run, deployment_parts, drift_path, evidence_paths, poll,
                            verdicts)

REASON_TEXT = {
    "FAILED_TEST": "a citing test case of this component FAILED in the run",
    "NO_METHOD": "the SDD row names no recognisable verification method",
    "AMBIGUOUS_ID": "the same requirement ID is defined more than once",
    "CITED_UNDER_DIFFERENT_ID": "a test of this component cites an undefined ID ending in this one",
    "CITED_BY_OTHER_COMPONENT": "only tests of another component cite it",
    "NO_CITING_TEST": "no test case cites it with REQUIREMENT(\"ID\")",
    "NOT_EXECUTED": "a citing test case did not run in the loaded run",
    "EVIDENCE_OUTSIDE_REPO": "a declared method (e.g. Inspection) leaves no record in the repository",
}

_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
_C = {"GO": "\033[32m", "NO-GO": "\033[31m", "INCOMPLETE": "\033[33m", "dim": "\033[2m", "bold": "\033[1m", "end": "\033[0m"}


def _run_label(run: str | None) -> str:
    return f"CI run {run.removeprefix('run:')}" if run else "no CI run loaded"


def c(text: str, key: str) -> str:
    return f"{_C[key]}{text}{_C['end']}" if _COLOR else text


def _open(args):
    return store.connect(Path(args.store))


def cmd_import(args) -> int:
    from gonogo.fprime import build_dataset
    ds = build_dataset(Path(args.repo))
    Path(args.out).write_text(json.dumps(ds, indent=1), encoding="utf-8")
    print({k: len(v) for k, v in ds["nodes"].items()}, "relationships:", len(ds["edges"]))
    return 0


def cmd_build(args) -> int:
    ds, res = store.load_inputs(Path(args.dataset), Path(args.results))
    print(json.dumps(store.build(ds, Path(args.store), res), indent=1))
    return 0


def _verdict_rows(conn, args):
    run = current_run(conn)
    return run, verdicts(conn, run, accept_declared=args.accept_declared)


def cmd_poll(args) -> int:
    conn = _open(args)
    run, rows = _verdict_rows(conn, args)
    dep = deployment_parts(conn)
    table = [p for p in poll(rows, dep) if args.all or p["in_deployment"]]
    if args.json:
        print(json.dumps({"run": run, "components": table}, indent=1))
        return 0
    m = store.meta(conn)
    scope = "all components" if args.all else "components of the Ref deployment"
    print(c(f"GoNoGo poll — F´ {str(m.get('source_commit'))[:10]}, {_run_label(run)}, {scope}", "bold"))
    width = max(len(p["component"]) for p in table) if table else 10
    for p in table:
        bar = c("█" * p["GO"], "GO") + c("█" * p["NO-GO"], "NO-GO") + c("░" * p["INCOMPLETE"], "INCOMPLETE")
        print(f"  {p['component']:<{width}}  {c(format(p['verdict'], '<10'), p['verdict'])} "
              f"{p['GO']:>3} GO {p['NO-GO']:>2} NO-GO {p['INCOMPLETE']:>3} INC  {bar}  "
              f"{c(', '.join(p['top_reasons']), 'dim')}")
    tot = {k: sum(p[k] for p in table) for k in ("requirements", "GO", "NO-GO", "INCOMPLETE")}
    print(f"\n  {tot['requirements']} requirements: {c(str(tot['GO']) + ' GO', 'GO')}, "
          f"{c(str(tot['NO-GO']) + ' NO-GO', 'NO-GO')}, {c(str(tot['INCOMPLETE']) + ' INCOMPLETE', 'INCOMPLETE')}"
          + ("" if run else c("  (no unit-test run loaded: nothing can be GO)", "dim")))
    return 0


def cmd_why(args) -> int:
    conn = _open(args)
    run, rows = _verdict_rows(conn, args)
    row = next((r for r in rows if r["id"] == args.id), None)
    if row is None:
        print(f"I don't know: {args.id} is not a requirement in the graph.")
        return 2
    print(f"{c(row['id'], 'bold')}  {c(row['verdict'], row['verdict'])}   ({row['component']}, {row['source']})")
    print(f"  {row['text']}")
    print(f"  declared methods: {', '.join(row['methods']) or 'none'}   {_run_label(run)}")
    for code in row["reasons"]:
        print(f"  - {code}: {REASON_TEXT[code]}")
    chains = {p["test_case"]: p for p in evidence_paths(conn, row["id"], run)}
    for label, key in (("own tests", "own_tests"), ("tests of other components", "other_tests"),
                       ("tests citing a near-miss ID", "drift_tests")):
        if row[key]:
            print(f"  {label}:")
            for t in row[key]:
                print(f"    {t}")
                ev = chains.get(t.rsplit(" ", 1)[0])
                if ev:  # the graph path that makes this test case evidence: TEST -> helpers -> REQUIREMENT()
                    print(c(f"      path: {' -> '.join(ev['chain'])} -> REQUIREMENT(\"{row['id'].split('@')[0]}\")", "dim"))
                elif key == "drift_tests" and (dp := drift_path(conn, t, row["id"])):
                    print(c(f"      path: {' -> '.join(dp['chain'])} -> REQUIREMENT(\"{dp['cited']}\")", "dim"))
    if args.cypher:
        print(c("\n  receipt (Cypher):", "dim"))
        print(c(VERDICTS_CYPHER.strip(), "dim"))
        print(c(f"  params: run={run!r}, accept_declared={args.accept_declared}", "dim"))
    return 0


def cmd_suggest(args) -> int:
    conn = _open(args)
    res = suggest(conn, args.id, k=args.k)
    if res is None:
        print(f"I don't know: {args.id} is not a requirement in the graph.")
        return 2
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0
    if args.patch:  # only the diff, so that `gonogo suggest ID --patch --repo R > x.patch` can be applied
        diff = res["sites"] and not res["drift"] and patch(Path(args.repo), res["id"], res["sites"][0])
        if not diff:
            print(f"No patch: {res['edit']}", file=sys.stderr)
            return 1
        sys.stdout.write(diff)
        return 0
    print(f"{c(res['id'], 'bold')}  ({res['component']})")
    print(f"  {res['text']}")
    if res["cited_at"]:
        print(f"  already cited at: {', '.join(res['cited_at'])} (see gonogo why {res['id']})")
    print(c(f"  words matched against the component's test code: {' '.join(res['terms'])}", "dim"))
    if res["drift"]:
        print("  near-miss ID in this component's tests:")
        for d in res["drift"]:
            print(f"    {d['site']:<36} cites {d['cited']}   {', '.join(d['at'])}")
    print("  best-matching test code (Relate full-text index, BM25):")
    for s in res["sites"]:
        print(f"    {s['score']:5.1f}  {s['kind']:<8} {s['name']:<36} {c(', '.join(s['at']), 'dim')}")
    print("  test cases that execute it (call graph):")
    for t in res["cases"]:
        print(f"    {t['best']:5.1f}  {t['gtest_filter']:<44} {t['status']:<8} {c('via ' + ' -> '.join(t['via']), 'dim')}")
    print()
    print(f"  suggested edit: {res['edit']}")
    print(c("  A suggestion is not evidence: the verdict turns GO only when the line is written and the test passes in CI.",
            "dim"))
    return 0


def cmd_impact(args) -> int:
    conn = _open(args)
    _, rows = _verdict_rows(conn, args)
    res = compute_impact(conn, files=args.files, components=args.component, port_types=args.port_type, verdict_rows=rows)
    if args.json:
        print(json.dumps(res, indent=1))
        return 0
    inp = res["input"]
    if inp["unmatched"]:
        print(c(f"  not mapped to any component or port type: {', '.join(inp['unmatched'])}", "dim"))
    if res["assumed_interface_change"]:
        print(c(f"  named without files, so their headers are assumed to change: "
                f"{', '.join(res['assumed_interface_change'])} (pass --files for an exact set)", "dim"))
    if res["ripple"]:
        print(c(f"  changes that reach every includer: {', '.join(res['ripple'])}", "dim"))
    print(c(f"Unit tests to re-run: {res['test_cases']} test cases in {len(res['rerun'])} components", "bold"))
    shown = res["rerun"] if args.all else res["rerun"][:25]
    for r in shown:
        why = reason(r)
        print(f"  {r['component']:<40} {r['test_cases']:>4} cases   {c(why, 'dim')}")
        if args.filters and r["filters"]:
            print(c(f"    GTEST_FILTER={':'.join(sorted(r['filters']))}", "dim"))
    if len(shown) < len(res["rerun"]):
        print(c(f"  … {len(res['rerun']) - len(shown)} more components (--all to list them)", "dim"))
    print(f"\n  requirements depending on these tests: {len(res['requirements'])}; "
          f"GO today, pending re-run: {len(res['go_pending_rerun'])}")
    if res["integration_review"]:
        print(c(f"\nIntegration review (deployment connections, not unit-test impact): "
                f"{len(res['integration_review'])}", "bold"))
        for i in res["integration_review"][:20]:
            print(f"  {i['instance']} {i['direction']:<3} {i['peer']}  ({i['src_port']} -> {i['dst_port']}, {i['graph']})")
    if inp["topology_files"]:
        print(c(f"  topology files changed: {', '.join(inp['topology_files'])} -> integration review only", "dim"))
    return 0


def cmd_findings(args) -> int:
    from gonogo.queries import run as run_tpl
    conn = _open(args)
    sections = [("Test files citing IDs that no SDD defines", "fp_unknown_citations"),
                ("Requirement IDs defined more than once", "fp_duplicate_requirement_ids"),
                ("Requirements cited by tests of another component", "fp_cross_component_citations")]
    out = {}
    for title, tpl in sections:
        rows, cypher, _ = run_tpl(conn, tpl)
        out[tpl] = rows
        if not args.json:
            print(c(f"{title}: {len(rows)}", "bold"))
            for r in rows:
                print("  " + "  ".join(f"{k}={v}" for k, v in r.items()))
    if args.json:
        print(json.dumps(out, indent=1))
    else:
        print(c("\nThese are traceability-hygiene findings derived from repository text; they may include parser "
                "false positives and are not statements about flight-code correctness.", "dim"))
    return 0


def cmd_ask(args) -> int:
    from gonogo.agent import Agent
    a = Agent(_open(args)).ask(args.question)
    if args.json:
        print(json.dumps(a.to_dict(), indent=1, default=str))
        return 0
    print(a.text)
    if a.cypher:
        print(c("\nreceipt (Cypher):", "dim"))
        print(c(a.cypher, "dim"))
        print(c(f"params: {a.params}   rows: {len(a.rows)}", "dim"))
    for s in a.suggestions:
        print(c(f"  nearest: {s['id']} ({s['label']}, {s['score']})", "dim"))
    return 0 if a.status == "answered" else 2


def cmd_diff(args) -> int:
    from gonogo.diff import diff, open_any, snapshot
    b, h = open_any(Path(args.base)), open_any(Path(args.head))
    d = diff(snapshot(b), snapshot(h))
    if args.json:
        print(json.dumps(d, indent=1, default=list))
        return 0
    print(c(f"F´ {str(d['base']['commit'])[:10]} -> {str(d['head']['commit'])[:10]}", "bold"))
    for k in d["base"]:
        if k != "commit":
            print(f"  {k:<34} {d['base'][k]!s:>8} -> {d['head'][k]!s:<8}")
    r = d["requirements"]
    print(f"\n  requirements added {len(r['added'])}, removed {len(r['removed'])}, text changed {len(r['text_changed'])}, "
          f"methods changed {len(r['methods_changed'])}; citations gained {len(d['citations']['gained'])}, "
          f"lost {len(d['citations']['lost'])}")
    print(f"  components added {len(d['components']['added'])}, removed {len(d['components']['removed'])}; "
          f"port uses added {len(d['ports']['uses_added'])}, removed {len(d['ports']['uses_removed'])}")
    for k, v in d["findings"].items():
        print(f"  findings {k}: new {len(v['new'])}, resolved {len(v['resolved'])}")
    return 0


def cmd_check(args) -> int:
    from gonogo.diff import check, open_any
    files = args.files
    if args.files_from:
        files = [ln.strip() for ln in Path(args.files_from).read_text(encoding="utf-8").splitlines() if ln.strip()]
    res, md, code = check(open_any(Path(args.base)), open_any(Path(args.head)), files)
    if args.markdown:
        Path(args.markdown).write_text(md, encoding="utf-8")
    print(md)
    return code


def cmd_export(args) -> int:
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(store.export_json(Path(args.store))), encoding="utf-8")
        print(f"wrote {args.json}")
    if args.single_file:
        ds, res = store.load_inputs(Path(args.dataset), Path(args.results))
        print(json.dumps(store.build(ds, Path(args.single_file), res), indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gonogo", description="Traceability and test-impact gate for F´ flight software, on Relate.")
    ap.add_argument("--store", default=str(store.STORE), help="Relate store (directory or .rl)")
    ap.add_argument("--accept-declared", action="store_true",
                    help="waive EVIDENCE_OUTSIDE_REPO when an own test case passed")
    # the same flag is accepted after the subcommand too (gonogo poll --accept-declared)
    flag = argparse.ArgumentParser(add_help=False)
    flag.add_argument("--accept-declared", action="store_true", default=argparse.SUPPRESS,
                      help="waive EVIDENCE_OUTSIDE_REPO when an own test case passed")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("import-fprime"); p.set_defaults(fn=cmd_import)
    p.add_argument("--repo", required=True); p.add_argument("--out", default=str(store.DATASET))
    p = sub.add_parser("build"); p.set_defaults(fn=cmd_build)
    p.add_argument("--dataset", default=str(store.DATASET)); p.add_argument("--results", default=str(store.RESULTS))
    p = sub.add_parser("poll", parents=[flag]); p.set_defaults(fn=cmd_poll)
    p.add_argument("--all", action="store_true"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("why", parents=[flag]); p.set_defaults(fn=cmd_why)
    p.add_argument("id"); p.add_argument("--cypher", action="store_true")
    p = sub.add_parser("suggest", help="test code that most likely verifies an uncited requirement")
    p.set_defaults(fn=cmd_suggest); p.add_argument("id"); p.add_argument("--k", type=int, default=5)
    p.add_argument("--json", action="store_true")
    p.add_argument("--patch", action="store_true", help="print a git-apply diff that writes the REQUIREMENT() line")
    p.add_argument("--repo", default="data/raw/fprime", help="F´ checkout the patch is made against")
    p = sub.add_parser("impact", parents=[flag]); p.set_defaults(fn=cmd_impact)
    p.add_argument("--files", nargs="*", default=[]); p.add_argument("--component", nargs="*", default=[])
    p.add_argument("--port-type", nargs="*", default=[]); p.add_argument("--filters", action="store_true")
    p.add_argument("--all", action="store_true", help="list every component, not only the first 25")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("findings"); p.set_defaults(fn=cmd_findings); p.add_argument("--json", action="store_true")
    p = sub.add_parser("ask"); p.set_defaults(fn=cmd_ask)
    p.add_argument("question"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("diff", help="compare two stores or dataset JSONs"); p.set_defaults(fn=cmd_diff)
    p.add_argument("base"); p.add_argument("head"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("check", help="CI gate between a base and a head graph"); p.set_defaults(fn=cmd_check)
    p.add_argument("--base", required=True); p.add_argument("--head", required=True)
    p.add_argument("--files", nargs="*", default=[]); p.add_argument("--files-from")
    p.add_argument("--markdown")
    p = sub.add_parser("export"); p.set_defaults(fn=cmd_export)
    p.add_argument("--json"); p.add_argument("--single-file")
    p.add_argument("--dataset", default=str(store.DATASET)); p.add_argument("--results", default=str(store.RESULTS))

    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
