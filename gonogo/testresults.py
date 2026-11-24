"""Turn gtest XML reports from an F´ unit-test run into a compact results file.

Standard library only, so it runs on a bare CI runner:
    python3 gonogo/testresults.py <xml-dir> <out.json> [--fprime-commit SHA]

Each F´ unit-test executable is named after its component directory, e.g. Svc_Health_ut_exe,
so the report file Svc_Health_ut_exe.xml maps to component "Svc/Health" and gtest case
Test.NominalTlm maps to GoNoGo test-case id "Svc/Health::Test.NominalTlm".
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

EXE_SUFFIX = re.compile(r"_ut_exe$|_ut$|_exe$")


def component_of(exe: str, known_dirs: set[str] | None = None) -> str:
    """Svc_Ccsds_TcDeframer_ut_exe -> Svc/Ccsds/TcDeframer (checked against known dirs when given)."""
    base = EXE_SUFFIX.sub("", exe)
    parts = base.split("_")
    if known_dirs:
        # directory names may themselves contain underscores: try every split, longest names first
        for k in range(len(parts), 0, -1):
            for cand in _joins(parts, k):
                if cand in known_dirs:
                    return cand
    return "/".join(parts)


def _joins(parts: list[str], groups: int):
    """All ways to join `parts` into `groups` path segments with '_' inside a segment."""
    if groups == 1:
        yield "_".join(parts)
        return
    for i in range(1, len(parts) - groups + 2):
        for rest in _joins(parts[i:], groups - 1):
            yield "_".join(parts[:i]) + "/" + rest


def parse_dir(xml_dir: Path, known_dirs: set[str] | None = None) -> dict:
    cases, executables = [], {}
    for f in sorted(xml_dir.glob("*.xml")):
        try:
            root = ET.parse(f).getroot()
        except ET.ParseError as e:  # a crashed executable can leave a truncated report
            executables[f.stem] = {"component": component_of(f.stem, known_dirs), "error": f"unparseable: {e}"}
            continue
        comp = component_of(f.stem, known_dirs)
        n = fails = 0
        for tc in root.iter("testcase"):
            n += 1
            if tc.find("failure") is not None or tc.find("error") is not None:
                status = "FAILED"
                fails += 1
            elif tc.get("result") == "skipped" or tc.get("status") == "notrun" or tc.find("skipped") is not None:
                status = "SKIPPED"
            else:
                status = "PASSED"
            suite, name = tc.get("classname", ""), tc.get("name", "")
            cases.append({"component": comp, "suite": suite, "name": name, "status": status,
                          "time_s": float(tc.get("time", 0) or 0), "id": f"{comp}::{suite}.{name}"})
        executables[f.stem] = {"component": comp, "tests": n, "failures": fails}
    return {"executables": executables, "cases": cases}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("xml_dir")
    ap.add_argument("out")
    ap.add_argument("--fprime-commit", default=os.environ.get("FPRIME_SHA", "unknown"))
    ap.add_argument("--known-dirs", help="optional JSON list of component directories for exact mapping")
    a = ap.parse_args(argv)
    known = set(json.loads(Path(a.known_dirs).read_text())) if a.known_dirs else None
    res = parse_dir(Path(a.xml_dir), known)
    server, repo, run_id = (os.environ.get(k, "") for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    res = {"fprime_commit": a.fprime_commit,
           "run": {"id": run_id, "url": f"{server}/{repo}/actions/runs/{run_id}" if run_id else "",
                   "runner": os.environ.get("RUNNER_OS", ""), "attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "")},
           **res}
    Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    total = len(res["cases"])
    failed = sum(c["status"] == "FAILED" for c in res["cases"])
    print(f"{len(res['executables'])} executables, {total} test cases, {failed} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
