"""How big is the re-run set on real changes? Change impact replayed on merged nasa/fprime pull requests.

Selection rule (fixed before the first run): walking back along the history of nasa/fprime `devel` from the dataset
commit (read through the GitHub API; each single-parent commit there is one squash-merged pull request), take the
first N commits that change at least one file under F´'s source roots (Svc, Drv, Fw, Os, CFDP, Utils, TestDeploymentsProject, default/config). No commit is
skipped for any other reason.

For each one, `gonogo impact --files <the files that pull request changed>` runs on the dataset graph. That graph is
from the dataset commit, a little after these pull requests, so a file that a pull request created or later moved may
not map; such files are counted, not hidden.

What it measures: the size of the re-run set (test cases and components) against the full unit-test suite in the
graph, how many components only the #include graph added, and how many pull requests needed the full suite. What it
does not measure: whether the re-run set would have caught a real breakage. That needs failing CI runs, which these
merged pull requests do not have.

  python -m eval.impact_replay [--n 30]  -> eval/IMPACT_REPLAY.md, eval/impact_replay.json  (needs `gh` signed in)
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
from pathlib import Path

from gonogo.db import connect
from gonogo.impact import impact

OUT = Path(__file__).resolve().parent
ROOTS = ("Svc/", "Drv/", "Fw/", "Os/", "CFDP/", "Utils/", "TestDeploymentsProject/", "default/config/")


def gh(path: str):
    """Read-only GitHub REST call through the gh CLI (public nasa/fprime data)."""
    out = subprocess.run(["gh", "api", path], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return json.loads(out)


def pull_requests(head: str, n: int) -> list[dict]:
    """Squash-merged pull requests on nasa/fprime devel, newest first, from `head` backwards (one commit each)."""
    out, page = [], 1
    while len(out) < n and page <= 10:
        commits = gh(f"repos/nasa/fprime/commits?sha={head}&per_page=100&page={page}")
        if not commits:
            break
        for c in commits:
            if len(c["parents"]) != 1:
                continue  # not a squash-merged pull request
            detail = gh(f"repos/nasa/fprime/commits/{c['sha']}?per_page=300")
            files = [f["filename"] for f in detail.get("files", [])]
            if any(f.startswith(ROOTS) for f in files):
                out.append({"sha": c["sha"], "date": c["commit"]["committer"]["date"][:10],
                            "subject": c["commit"]["message"].split("\n", 1)[0], "files": files})
                if len(out) == n:
                    break
        page += 1
    return out


def main() -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    a = ap.parse_args()
    db = connect()
    head = db.execute("MATCH (m:Meta) RETURN m.source_commit AS c").fetchone()
    head = head["c"]
    suite = db.execute("MATCH (t:TestCase) RETURN count(t) AS n").fetchone()["n"]
    comps_total = db.execute("MATCH (p:Part) RETURN count(p) AS n").fetchone()["n"]
    prs = pull_requests(head, a.n)
    rows = []
    for pr in prs:
        src = [f for f in pr["files"] if f.startswith(ROOTS)]
        res = impact(db, files=src)
        inc_only = [r["component"] for r in res["rerun"] if r["via_includes"] and not r["changed_directly"]
                    and not r["via_ports"]]
        rows.append({"sha": pr["sha"][:10], "date": pr["date"], "subject": pr["subject"][:90],
                     "source_files": len(src), "unmapped": res["input"]["unmatched"],
                     "test_cases": res["test_cases"], "components": len(res["rerun"]),
                     "added_by_includes": len(inc_only), "full_suite": bool(res["input"]["config_files"]),
                     "share": round(res["test_cases"] / suite, 4)})
    db.close()
    shares = [r["share"] for r in rows]
    res = {"pull_requests": len(rows), "suite_test_cases": suite, "components_total": comps_total,
           "head": head, "oldest": rows[-1]["date"] if rows else None, "newest": rows[0]["date"] if rows else None,
           "median_share": statistics.median(shares) if rows else None,
           "median_test_cases": statistics.median(r["test_cases"] for r in rows) if rows else None,
           "full_suite_prs": sum(r["full_suite"] for r in rows),
           "zero_test_prs": sum(r["test_cases"] == 0 for r in rows),
           "prs_with_include_additions": sum(r["added_by_includes"] > 0 for r in rows),
           "unmapped_files": sum(len(r["unmapped"]) for r in rows),
           "source_files": sum(r["source_files"] for r in rows), "rows": rows}
    (OUT / "impact_replay.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    (OUT / "IMPACT_REPLAY.md").write_text(markdown(res), encoding="utf-8", newline="\n")
    return res


def markdown(res: dict) -> str:
    lines = ["# Change impact on merged nasa/fprime pull requests (generated by `python -m eval.impact_replay`)", "",
             f"{res['pull_requests']} merged pull requests ({res['oldest']} to {res['newest']}), chosen by the rule in "
             f"the module docstring, against {res['suite_test_cases']} unit-test cases in {res['components_total']} "
             f"components. Median re-run set: {res['median_test_cases']} test cases "
             f"({100 * res['median_share']:.1f} % of the suite). Full suite: {res['full_suite_prs']} pull requests "
             f"(FPP configuration changed). No unit tests to re-run: {res['zero_test_prs']}. The #include graph added "
             f"components in {res['prs_with_include_additions']}. Source files that did not map to the graph: "
             f"{res['unmapped_files']} of {res['source_files']}.", "",
             "| Merged | Pull request | Source files | Test cases to re-run | Share of suite | Components | "
             "of which only via #include | Unmapped |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    for r in res["rows"]:
        subject = r["subject"].replace("|", "\\|")
        lines.append(f"| {r['date']} `{r['sha']}` | {subject} | {r['source_files']} | {r['test_cases']} | "
                     f"{100 * r['share']:.1f} % | {r['components']} | {r['added_by_includes']} | {len(r['unmapped'])} |")
    lines += ["", "What this does not show: whether the re-run set would have caught a real breakage; these pull "
              "requests were merged with green CI, so there is no failing run to compare with.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    r = main()
    print(json.dumps({k: v for k, v in r.items() if k != "rows"}, indent=1))
