"""Compute every number the README shows, and write it into the README's generated blocks.

    python -m gonogo.stats                 # print docs/stats.json content
    python -m gonogo.stats --readme README.md [--measure-modes]

Blocks look like <!-- gonogo:NAME --> ... <!-- /gonogo:NAME --> and are replaced wholesale, so no number in
them is typed by hand. --measure-modes builds the store once as a directory and once as a single .rl file
in a temporary folder and records both sizes.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from gonogo import db as store
from gonogo.diff import metrics, snapshot
from gonogo.queries import run as run_tpl
from gonogo.verdict import case_citations, current_run, deployment_parts, poll, verdicts

ROOT = Path(__file__).resolve().parents[1]
RELEASES = [("v3.4.0", "2023-11-29"), ("v4.0.0", "2025-08-07"), ("v4.4.2", "2026-11-04")]
# merged nasa/fprime pull requests replayed through the gate (graphs and file lists in data/replay/)
REPLAYS = [("5170", "TlmChan hash improvements", "d833799", "bf71d97"),
           ("5291", "Reject invalid PVN in SpacePacketDeframer", "acaa3fb", "9a4d173")]
FPRIME_BLOB = "https://github.com/nasa/fprime/blob/{sha}/{path}"


def _size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size


def _mb(n: int) -> str:
    return f"{n / 1e6:.2f} MB"


def cli(*args: str) -> str:
    import gonogo.cli as cli_mod
    buf = io.StringIO()
    color, cli_mod._COLOR = cli_mod._COLOR, False  # never let ANSI colour codes into the README
    try:
        with contextlib.redirect_stdout(buf):
            cli_mod.main(["--store", str(store.STORE), *args])
    finally:
        cli_mod._COLOR = color
    return buf.getvalue().rstrip()


def compute(measure_modes: bool = False) -> dict:
    ds, res = store.load_inputs()
    sha = ds["source"]["commit"]
    case_ids = {c["id"] for c in ds["nodes"]["TestCase"]}
    _, run_edges, unmapped = store.results_edges(res, case_ids)
    case_cites = case_citations(ds)
    cited_cases = {c for c, rs in case_cites.items() if rs}
    ran = {e["src"] for e in run_edges}
    conn = store.connect()
    try:
        run = current_run(conn)
        rows = verdicts(conn, run)
        dep = deployment_parts(conn)
        table = poll(rows, dep)
        findings = {t: run_tpl(conn, t)[0] for t in
                    ("fp_unknown_citations", "fp_duplicate_requirement_ids", "fp_cross_component_citations")}
        current_metrics = metrics(snapshot(conn))
    finally:
        conn.close()

    def totals(sel):
        return {k: sum(p[k] for p in sel) for k in ("requirements", "GO", "NO-GO", "INCOMPLETE")}

    status = Counter(c["status"] for c in res["cases"])
    out = {
        "fprime_commit": sha,
        "dataset": {"components": len(ds["nodes"]["Part"]), "requirements": len(ds["nodes"]["Requirement"]),
                    "test_cases": len(ds["nodes"]["TestCase"]), "port_types": len(ds["nodes"]["PortType"]),
                    "instances": len(ds["nodes"]["Instance"]),
                    "connections": sum(e["type"] == "CONNECTS_TO" for e in ds["edges"]),
                    "relationships": len(ds["edges"]),
                    "requirements_cited": len({r for rs in case_cites.values() for r in rs}),
                    "test_functions": len(ds["nodes"].get("TestFunction", [])),
                    "calls": sum(e["type"] == "CALLS" for e in ds["edges"]),
                    "max_citation_hops": ds["diagnostics"].get("max_citation_hops")},
        "ci_run": {"id": res["run"]["id"], "url": res["run"]["url"], "executables": len(res["executables"]),
                   "cases": len(res["cases"]), "passed": status["PASSED"], "skipped": status["SKIPPED"],
                   "failed": status["FAILED"], "mapped": len(run_edges), "unmapped": len(unmapped),
                   "citing_cases": len(cited_cases), "citing_cases_ran": len(cited_cases & ran)},
        "poll_ref": totals([p for p in table if p["in_deployment"]]),
        "poll_all": totals(table),
        "ref_components": sum(p["in_deployment"] for p in table),
        "fully_go_ref": [p["component"] for p in table if p["in_deployment"] and p["verdict"] == "GO"],
        "reasons_all": dict(Counter(x for r in rows for x in r["reasons"]).most_common()),
        "findings": {k: v for k, v in findings.items()},
        "store_dir_bytes": _size(store.STORE),
        "releases": [],
    }
    for tag, date in RELEASES:
        f = ROOT / "data" / "releases" / f"fprime-{tag}.json"
        if f.exists():
            tmp = Path(tempfile.mkdtemp(prefix="gonogo_rel_")) / "store"
            store.build(json.loads(f.read_text(encoding="utf-8")), tmp)
            c = store.connect(tmp)
            out["releases"].append({"tag": tag, "date": date, **metrics(snapshot(c))})
            c.close()
            shutil.rmtree(tmp.parent, ignore_errors=True)
    out["releases"].append({"tag": "devel", "date": "2026-11-24", **current_metrics})
    from gonogo.diff import check, open_any
    rep_dir = ROOT / "data" / "replay"
    by_id = {r["id"]: r for r in rows}
    out["replays"] = []
    for pr, title, base, head in REPLAYS:
        if not (rep_dir / f"pr{pr}-{base}.json").exists():
            continue
        files = [ln.strip() for ln in (rep_dir / f"pr{pr}-files.txt").read_text(encoding="utf-8").splitlines() if ln.strip()]
        bdb, hdb = open_any(rep_dir / f"pr{pr}-{base}.json"), open_any(rep_dir / f"pr{pr}-{head}.json")
        res, _, code = check(bdb, hdb, files)
        bdb.close(); hdb.close()
        added = res["diff"]["requirements"]["added"]
        out["replays"].append({"pr": pr, "title": title, "base": base, "head": head, "result": "FAIL" if code else "PASS",
                               "blocking": res["blocking"], "warnings": res["warnings"], "added": added,
                               "rerun_cases": res["impact"]["test_cases"],
                               "today": {a: by_id[a]["reasons"] for a in added if a in by_id}})
    mut = ROOT / "eval" / "mutation_results.json"
    if mut.exists():
        m = json.loads(mut.read_text(encoding="utf-8"))
        out["mutation"] = {"summary": m["summary"], "seconds": m["seconds"], "seed": m["seed"],
                           "collateral": [x for x in m["mutations"] if x["collateral"]]}
    sug = ROOT / "eval" / "suggest_results.json"
    if sug.exists():
        out["suggest"] = {k: v for k, v in json.loads(sug.read_text(encoding="utf-8")).items() if k != "per_requirement"}
    loop = ROOT / "data" / "replay" / "pr5170-suggested-check.md"
    if loop.exists():
        out["closed_loop"] = loop.read_text(encoding="utf-8")
    for key, name in (("suggest_heldout", "suggest_heldout.json"), ("impact_replay", "impact_replay.json")):
        f = ROOT / "eval" / name
        if f.exists():
            out[key] = {k: v for k, v in json.loads(f.read_text(encoding="utf-8")).items()
                        if k not in ("requirements", "unreachable")}
    if measure_modes:
        tmp = Path(tempfile.mkdtemp(prefix="gonogo_modes_"))
        store.build(ds, tmp / "dir", res)
        store.build(ds, tmp / "single.rl", res)
        out["modes"] = {"directory_bytes": _size(tmp / "dir"), "single_file_bytes": _size(tmp / "single.rl")}
        shutil.rmtree(tmp, ignore_errors=True)
    out["cli"] = {"why_hth": cli("why", "HTH-001"), "why_tlc": cli("why", "TLC-001"),
                  "impact_sched": cli("impact", "--files", "Svc/Sched/Sched.fpp"),
                  "impact_health": cli("impact", "--files", "Svc/Health/Health.cpp"),
                  "impact_lib": cli("impact", "--files", "Utils/Types/CircularBuffer.hpp"),
                  "suggest_tlc": cli("suggest", "TLC-006")}
    return out


# ---------------------------------------------------------------- README blocks
def blocks(s: dict, old_modes: dict | None = None) -> dict[str, str]:
    d, ci, pr, pa = s["dataset"], s["ci_run"], s["poll_ref"], s["poll_all"]
    sha = s["fprime_commit"]
    b = {}
    b["headline"] = (
        f"- **{d['requirements']}** requirements, **{d['test_cases']}** unit-test cases, **{d['components']}** components, "
        f"**{d['port_types']}** port types and the **{d['instances']}**-instance Ref deployment of "
        f"[NASA F´](https://github.com/nasa/fprime) (commit [`{sha[:10]}`](https://github.com/nasa/fprime/tree/{sha})) "
        f"in one Relate graph ({d['relationships']} relationships).\n"
        f"- Test code as a call graph: {d['test_functions']} helper functions and {d['calls']} `CALLS` edges. "
        f"Evidence is a graph path from a test case to the `REQUIREMENT()` it relies on, up to "
        f"{d['max_citation_hops']} calls deep in F´.\n"
        f"- F´'s own unit tests, **run in our CI** ([run {ci['id']}]({ci['url']})): {ci['cases']} test cases, "
        f"{ci['passed']} passed, {ci['skipped']} skipped, {ci['failed']} failed. All {ci['citing_cases_ran']} of the "
        f"{ci['citing_cases']} test cases that cite a requirement ran.\n"
        f"- Ref deployment poll: **{pr['GO']} GO**, **{pr['NO-GO']} NO-GO**, **{pr['INCOMPLETE']} INCOMPLETE** of "
        f"{pr['requirements']} requirements in {s['ref_components']} components. Fully GO: "
        f"{', '.join(f'`{c}`' for c in s['fully_go_ref']) or 'none'}.\n"
        f"- {len({u for r in s['findings']['fp_unknown_citations'] for u in r['unknown_ids']})} test citations of "
        f"undefined IDs, {len({r['id'] for r in s['findings']['fp_duplicate_requirement_ids']})} duplicated "
        f"requirement IDs and {len(s['findings']['fp_cross_component_citations'])} cross-component citations, "
        "found from repository text (see [Findings](#findings)).")
    # first screen: the finding, the fix, the proof (all numbers from the same computation)
    pitch = [f"- **The finding.** F´'s own unit tests pass in CI ({ci['passed']} passed, {ci['failed']} failed), yet "
             f"only **{pa['GO']} of {pa['requirements']}** requirements are GO: named by tests that all passed, with "
             f"nothing else missing; "
             f"{s['reasons_all'].get('NO_CITING_TEST', 0)} are cited by no test at all. Missing links, not missing "
             f"tests, and GoNoGo shows each one with its reason."]
    loop = re.search(r"With these (\d+) lines.*?says \*\*(PASS|FAIL)\*\*", s.get("closed_loop") or "", re.S)
    if s.get("suggest") and s.get("replays"):
        g = s["suggest"]
        r5170 = next((r for r in s["replays"] if r["pr"] == "5170"), None) if loop else None
        pitch.append(f"- **The fix.** `gonogo suggest` points at the test where the `REQUIREMENT()` line belongs: the "
                     f"place F´ developers chose is in its top 3 for {100 * g['sites']['full']['hit@3']:.0f} % of "
                     f"{g['requirements']} cited requirements ({100 * g['sites']['identifiers']['hit@3']:.0f} % from "
                     f"code identifiers alone; random order: {100 * g['sites']['random']['hit@3']:.0f} %)."
                     + (f" On the real nasa/fprime PR #5170 the gate says **{r5170['result']}**; with the "
                        f"{loop.group(1)} suggested lines it says **{loop.group(2)}** "
                        "([closing the loop](#replayed-on-real-f-pull-requests))." if r5170 else ""))
    if s.get("mutation"):
        summ = s["mutation"]["summary"]
        tot = {k: sum(v.get(k, 0) for v in summ.values())
               for k in ("mutations", "gonogo_detected", "grep_detected", "grep_scoped_detected")}
        graph_only = [c for c in ("citation_orphaned", "citing_test_deleted") if c in summ]
        g_only = {k: sum(summ[c].get(k, 0) for c in graph_only) for k in ("mutations", "gonogo_detected",
                                                                           "grep_scoped_detected")}
        best_grep = tot["grep_scoped_detected"] if "grep_scoped_detected" in next(iter(summ.values())) else tot["grep_detected"]
        proof = (f"- **The proof.** {tot['mutations']} defects planted in real F´ sources: GoNoGo caught "
                 f"{tot['gonogo_detected']}, a careful per-component grep {best_grep}.")
        if graph_only:
            proof += (f" The gap is where evidence is a graph path: when a `REQUIREMENT()` line survives but no test "
                      f"reaches it any more, GoNoGo caught {g_only['gonogo_detected']} of {g_only['mutations']} and grep "
                      f"{g_only['grep_scoped_detected']}.")
        pitch.append(proof + " Every number on this page is generated by a script, and CI fails when the page and the "
                     "committed data disagree.")
    b["pitch"] = "\n".join(pitch)
    b["poll"] = "\n".join([
        "| Scope | Requirements | GO | NO-GO | INCOMPLETE |", "|---|---|---|---|---|",
        f"| Ref deployment ({s['ref_components']} components) | {pr['requirements']} | {pr['GO']} | {pr['NO-GO']} | {pr['INCOMPLETE']} |",
        f"| All components with requirements | {pa['requirements']} | {pa['GO']} | {pa['NO-GO']} | {pa['INCOMPLETE']} |",
        "", "INCOMPLETE reasons over all requirements (a requirement can have several):", "",
        "| Reason | Requirements |", "|---|---|"] +
        [f"| `{k}` | {v} |" for k, v in s["reasons_all"].items()])
    b["ci"] = (f"Run [{ci['id']}]({ci['url']}) built F´ `{sha[:10]}` with the official `nasa/fprime-actions` steps and "
               f"ran {ci['executables']} unit-test executables: {ci['cases']} test cases, {ci['passed']} passed, "
               f"{ci['skipped']} skipped, {ci['failed']} failed. {ci['mapped']} results map to test cases in the graph; "
               f"{ci['unmapped']} belong to library directories without a component (e.g. `CFDP/Checksum`) or to "
               f"parameterised cases GoNoGo does not model. All {ci['citing_cases_ran']} of {ci['citing_cases']} "
               "requirement-citing test cases ran.")
    rows = ["| ID | Where (F´ at the pinned commit) | What |", "|---|---|---|"]
    for r in s["findings"]["fp_unknown_citations"]:
        url = FPRIME_BLOB.format(sha=sha, path=r["file"])
        rows.append(f"| {', '.join(f'`{u}`' for u in r['unknown_ids'])} | [{r['file']}]({url}) | cited by tests of "
                    f"`{r['component']}`, defined in no SDD table |")
    dup_links: dict[str, list[str]] = {}
    for r in s["findings"]["fp_duplicate_requirement_ids"]:
        path, line = r["source"].split("#L")
        dup_links.setdefault(r["id"], []).append(f"[{path}#L{line}]({FPRIME_BLOB.format(sha=sha, path=path)}#L{line})")
    for rid, links in dup_links.items():
        rows.append(f"| `{rid}` | {'<br>'.join(links)} | same ID defined {len(links)} times |")
    pairs: dict[tuple[str, str], list[str]] = {}
    for r in s["findings"]["fp_cross_component_citations"]:
        pairs.setdefault((r["owner"], r["cited_by"]), []).append(r["id"])
    for (owner, by), ids in pairs.items():
        rows.append(f"| {', '.join(f'`{i}`' for i in ids)} | owned by `{owner}` | also cited by unit tests of `{by}` |")
    b["findings"] = "\n".join(rows)
    keys = [("requirements", "Requirements"), ("requirements_cited_by_own_tests", "Cited by own test cases"),
            ("cited_percent", "Cited %"), ("test_cases", "Unit-test cases"), ("components", "Components"),
            ("findings_unknown_citations", "Undefined cited IDs"), ("findings_duplicate_ids", "Duplicate IDs"),
            ("findings_cross_component", "Cross-component citations")]
    rel = s["releases"]
    b["releases"] = "\n".join(["| | " + " | ".join(f"{r['tag']} ({r['date']})" for r in rel) + " |",
                               "|---|" + "---|" * len(rel)] +
                              [f"| {label} | " + " | ".join(str(r[k]) for r in rel) + " |" for k, label in keys])
    if "mutation" in s:
        m = s["mutation"]
        lines = ["| Planted defect (real F´ sources) | Mutations | GoNoGo detected | grep, whole tree | "
                 "grep, per component | With collateral |", "|---|---|---|---|---|---|"]
        for cls, v in m["summary"].items():
            lines.append(f"| `{cls}` | {v['mutations']} | {v['gonogo_detected']} | {v['grep_detected']} | "
                         f"{v.get('grep_scoped_detected', '–')} | {v['with_collateral']} |")
        keys = ("mutations", "gonogo_detected", "grep_detected", "grep_scoped_detected")
        tot = {k: sum(v.get(k, 0) for v in m["summary"].values()) for k in keys}
        lines += ["", f"Totals: GoNoGo {tot['gonogo_detected']}/{tot['mutations']}, grep over the whole tree "
                  f"{tot['grep_detected']}/{tot['mutations']}, grep per component "
                  f"{tot['grep_scoped_detected']}/{tot['mutations']}; seed {m['seed']}, {m['seconds']} s, every "
                  f"mutation re-runs text → dataset → Relate → Cypher."]
        for c in m["collateral"]:
            lines.append(f"Collateral case: `{c['class']}` on `{c['target']}` also changed {', '.join(f'`{x}`' for x in c['collateral'])}.")
        b["mutation"] = "\n".join(lines)
    modes = s.get("modes") or old_modes
    size_line = f"Committed directory store `data/fprime/`: {_mb(s['store_dir_bytes'])}."
    if modes:
        size_line += (f" Same graph measured in both modes: directory {_mb(modes['directory_bytes'])}, "
                      f"single `.rl` file {_mb(modes['single_file_bytes'])}.")
    b["modes"] = size_line
    if s.get("replays"):
        lines = ["| nasa/fprime PR | Gate | Why | Status of those requirements at the pinned devel commit |",
                 "|---|---|---|---|"]
        for r in s["replays"]:
            why = "; ".join(r["blocking"] or r["warnings"]) or "no findings"
            today = ", ".join(f"`{k}`: {'GO' if not v else ', '.join(v)}" for k, v in r["today"].items()) or "n/a"
            lines.append(f"| [#{r['pr']}](https://github.com/nasa/fprime/pull/{r['pr']}) {r['title']} | **{r['result']}** | "
                         f"{why} | {today} |")
        b["replays"] = "\n".join(lines)
    for k in ("why_hth", "why_tlc", "impact_sched", "impact_health", "impact_lib", "suggest_tlc"):
        b[k] = "```text\n" + s["cli"][k] + "\n```"
    if s.get("suggest"):
        g = s["suggest"]
        lines = [f"{g['requirements']} requirements in {g['components']} components are cited today by test code of "
                 f"their own component. For each, `suggest` ranked that component's test code without reading the "
                 f"citations (median {g['median_candidate_sites']} test bodies and helpers, "
                 f"{g['median_candidate_cases']} test cases per requirement), and we looked up where F´ developers "
                 f"actually wrote the macro:", "",
                 "| Ranking | Cite site in top 1 | top 3 | top 5 | Citing test case in top 1 | top 3 | top 5 |",
                 "|---|---:|---:|---:|---:|---:|---:|"]
        names = {"full": "`suggest`", "identifiers": "identifiers only (no TEST_CASE/COMMENT text)",
                 "no call graph": "case ranked by its own TEST body only", "random": "random order (exact expectation)"}
        for key, label in names.items():
            si, ca = g["sites"].get(key), g["cases"].get(key)
            cell = lambda m, k: f"{100 * m[f'hit@{k}']:.0f} %" if m else "–"  # noqa: E731
            lines.append(f"| {label} | " + " | ".join(cell(si, k) for k in (1, 3, 5)) + " | "
                         + " | ".join(cell(ca, k) for k in (1, 3, 5)) + " |")
        ov = g["description_overlap"]["answer"]
        lines += ["", f"Generated by [`eval/suggest_eval.py`](eval/suggest_eval.py) ({g['seconds']} s); details in "
                  f"[`eval/SUGGEST_RESULTS.md`](eval/SUGGEST_RESULTS.md). Leak check: in {ov['ge80']} of {ov['n']} "
                  f"(requirement, cite site) pairs do the test descriptions carry 80 % or more of the requirement's "
                  f"words."]
        b["suggest_eval"] = "\n".join(lines)
    if s.get("impact_replay"):
        r = s["impact_replay"]
        b["impact_replay"] = (
            f"Replayed on the {r['pull_requests']} most recent merged nasa/fprime pull requests that touch source code "
            f"({r['oldest']} to {r['newest']}, chosen by a fixed rule): the median re-run set is "
            f"**{r['median_test_cases']:g} test cases, {100 * r['median_share']:.1f} %** of the "
            f"{r['suite_test_cases']}-case suite. {r['zero_test_prs']} needed no unit tests (documentation or units "
            f"without tests), {r['full_suite_prs']} needed the full suite (an FPP configuration file), and the "
            f"`#include` graph added components in {r['prs_with_include_additions']}. {r['unmapped_files']} of "
            f"{r['source_files']} changed source files did not map. Every pull request is listed in "
            f"[`eval/IMPACT_REPLAY.md`](eval/IMPACT_REPLAY.md). This measures the size of the re-run set, not whether "
            f"it would have caught a breakage: these pull requests were merged with green CI.")
    if s.get("suggest_heldout"):
        h = s["suggest_heldout"]
        line = (f"**Held out in time.** The forward question, ranking an older release's test code and scoring "
                f"against citations written later, has only {h['later_cited']} cases across the "
                f"{len(h['releases'])} older releases, {h['findable']} of them in test code that already existed")
        if h.get("suggest"):
            line += (f": `suggest` puts the later citation in its top 3 for {100 * h['suggest']['hit@3']:.0f} % of "
                     f"them (random order: {100 * h['random']['hit@3']:.0f} %)")
        b["suggest_heldout"] = (line + ". That is far too few to estimate accuracy; details in "
                                "[`eval/SUGGEST_HELDOUT.md`](eval/SUGGEST_HELDOUT.md).")
    if s.get("closed_loop"):  # the summary part of eval/closed_loop.py's report; the full gate report is linked
        head = s["closed_loop"].split("\n## GoNoGo traceability gate", 1)[0]
        head = head.replace("## Closing the loop on nasa/fprime#5170\n\n", "").replace(
            "([patch](pr5170-suggested.patch))", "([patch](data/replay/pr5170-suggested.patch))").rstrip().rstrip(":")
        b["closed_loop"] = head + " ([full report](data/replay/pr5170-suggested-check.md))."
    return b


def apply_blocks(text: str, b: dict[str, str]) -> tuple[str, list[str]]:
    done = []
    for name, body in b.items():
        pat = re.compile(rf"(<!-- gonogo:{name} -->)(.*?)(<!-- /gonogo:{name} -->)", re.S)
        if pat.search(text):
            text = pat.sub(lambda m: f"{m.group(1)}\n{body}\n{m.group(3)}", text)
            done.append(name)
    return text, done


def write_readme(readme: Path, b: dict[str, str]) -> list[str]:
    text, done = apply_blocks(readme.read_text(encoding="utf-8"), b)
    readme.write_text(text, encoding="utf-8")
    return done


def stale_blocks(readme: Path, b: dict[str, str], skip: set[str]) -> list[str]:
    """Blocks whose generated content differs from what the README holds now."""
    current = readme.read_text(encoding="utf-8")
    out = []
    for name, body in b.items():
        if name in skip:
            continue
        new, done = apply_blocks(current, {name: body})
        if done and new != current:
            out.append(name)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--readme")
    ap.add_argument("--measure-modes", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if any generated README block is out of date (store sizes are not compared)")
    a = ap.parse_args(argv)
    if a.check:
        old = json.loads((ROOT / "docs" / "stats.json").read_text(encoding="utf-8"))
        s = compute(False)
        s["modes"] = old.get("modes")
        stale = stale_blocks(Path(a.readme or ROOT / "README.md"), blocks(s), skip={"modes"})
        print("README is up to date" if not stale else f"stale README blocks: {', '.join(stale)}")
        return 1 if stale else 0
    out_json = ROOT / "docs" / "stats.json"
    old = json.loads(out_json.read_text(encoding="utf-8")) if out_json.exists() else {}
    s = compute(a.measure_modes)
    if "modes" not in s and "modes" in old:
        s["modes"] = old["modes"]
    out_json.parent.mkdir(exist_ok=True)
    out_json.write_text(json.dumps({k: v for k, v in s.items() if k != "cli"}, indent=1, default=str), encoding="utf-8")
    if a.readme:
        print("updated blocks:", write_readme(Path(a.readme), blocks(s)))
    else:
        print(json.dumps({k: v for k, v in s.items() if k not in ("cli", "findings")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
