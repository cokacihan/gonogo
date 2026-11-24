"""Mutation evaluation on REAL F´ sources, with a grep baseline.

A copy of the F´ checkout is mutated one defect at a time. After every mutation the whole GoNoGo pipeline runs
again (repository text -> dataset -> Relate store -> Cypher verdicts/findings/impact), and we check whether the
planted defect is reported (detection) and whether anything else changed (collateral). The expected effect of
each mutation is known by construction, so no question was written by the author of the rules.

Defect classes
  citation_id_drift      REQUIREMENT("X") -> REQUIREMENT("ISF-X") for an X cited exactly once
  citation_deleted       the only REQUIREMENT("X") line is removed
  duplicate_id           the ID of one SDD row is replaced by the ID of another row of the same table
  method_blanked         the verification-method cell of one SDD row is emptied
  cross_component_cite   REQUIREMENT("Y") for an uncited requirement Y of component D is added to tests of C != D
  port_type_use_added    `output port gonogoMutOut: T` is added to component C, which did not use T

Baseline: what a careful engineer gets from grep over the same tree (emulated in Python so it runs anywhere):
  ids      = grep -rhoE '^\\|? *[A-Z][A-Z0-9_-]*[0-9]+ *\\|' */docs/sdd.md
  cited    = grep -rhoE 'REQUIREMENT\\("[^"]+"\\)' */test/
  uncited  = ids - cited ; dangling = cited - ids ; dup = ids | sort | uniq -d
  empty    = SDD table rows whose last cell is empty
  users(T) = grep -lE '(input|output) port \\w+: *(\\[.*\\] *)?(Svc\\.)?T\\b' --include=*.fpp

Usage: python -m eval.mutation_eval [--per-class 20] [--seed 2026]
Writes eval/mutation_results.json and eval/MUTATION_RESULTS.md.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import statistics
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from gonogo import fprime
from gonogo.db import build, connect
from gonogo.impact import impact
from gonogo.queries import run
from gonogo.verdict import verdicts

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "raw" / "fprime"
OUT = Path(__file__).resolve().parent
ROW_ID = re.compile(r"^\s*\|?\s*([A-Z][A-Z0-9]*(?:[-_][A-Z0-9]+)*[-_]\d+[A-Za-z]?)\s*\|")
CITE = re.compile(r'REQUIREMENT\(\s*"([^"]+)"\s*\)')


# ---------------------------------------------------------------- pipeline
def all_pass_results(ds: dict) -> dict:
    cases = [{"id": c["id"], "component": c["id"].split("::")[0], "suite": c["suite"], "name": c["name"],
              "status": "PASSED", "time_s": 0.0} for c in ds["nodes"]["TestCase"]]
    return {"fprime_commit": ds["source"]["commit"], "run": {"id": "mutation", "url": ""}, "cases": cases}


def pipeline(repo: Path, store: Path) -> dict:
    ds = fprime.build_dataset(repo)
    build(ds, store, all_pass_results(ds))
    db = connect(store)
    try:
        rows = {r["id"]: r for r in verdicts(db, "run:mutation")}
        unknown = {u for r in run(db, "fp_unknown_citations")[0] for u in r["unknown_ids"]}
        dups = {r["id"] for r in run(db, "fp_duplicate_requirement_ids")[0]}
        cross = {r["id"] for r in run(db, "fp_cross_component_citations")[0]}
        return {"ds": ds, "rows": rows, "unknown": unknown, "dups": dups, "cross": cross, "db_path": store}
    finally:
        db.close()


def impact_components(store: Path, port_type: str) -> set[str]:
    db = connect(store)
    try:
        return {r["component"] for r in impact(db, port_types=[port_type])["rerun"]}
    finally:
        db.close()


# ---------------------------------------------------------------- grep baseline
def grep_view(repo: Path) -> dict:
    ids, empty_rows = [], set()
    for f in repo.rglob("sdd.md"):
        if f.parent.name != "docs":
            continue
        for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = ROW_ID.match(ln)
            if m:
                ids.append(m.group(1))
                cells = [x.strip() for x in ln.strip().strip("|").split("|")]
                if cells and cells[-1] == "":
                    empty_rows.add(m.group(1))
    cited = set()
    for f in repo.rglob("*"):
        if "test" in f.parts and f.suffix in (".cpp", ".hpp", ".h", ".cc") and f.is_file():
            cited |= set(CITE.findall(f.read_text(encoding="utf-8", errors="replace")))
    seen, dup = set(), set()
    for i in ids:
        (dup if i in seen else seen).add(i)
    s = set(ids)
    return {"uncited": s - cited, "dangling": cited - s, "dup": dup, "empty": empty_rows}


def grep_port_users(repo: Path, port_type: str) -> set[str]:
    short = port_type.removeprefix("port:").split(".")[-1]
    pat = re.compile(rf"(input|output)\s+port\s+\w+\s*:\s*(\[[^\]]*\]\s*)?([\w.]*\.)?{re.escape(short)}\b")
    users = set()
    for f in repo.rglob("*.fpp"):
        if pat.search(f.read_text(encoding="utf-8", errors="replace")):
            users.add(f.parent.relative_to(repo).as_posix())
    return users


# ---------------------------------------------------------------- mutation targets
def _citation_sites(repo: Path, ds: dict) -> dict[str, list[tuple[Path, int]]]:
    owner = {e["dst"]: e["src"] for e in ds["edges"] if e["type"] == "SATISFIES"}
    sites = defaultdict(list)
    for comp in {p["id"] for p in ds["nodes"]["Part"]}:
        td = repo / comp / "test"
        if not td.is_dir():
            continue
        for f in td.rglob("*"):
            if f.suffix in (".cpp", ".hpp") and f.is_file():
                for i, ln in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines()):
                    for rid in CITE.findall(ln):
                        if owner.get(rid) == comp:
                            sites[rid].append((f, i))
    return sites


def targets(repo: Path, base: dict, rng: random.Random, n: int) -> list[dict]:
    ds, rows = base["ds"], base["rows"]
    sites = _citation_sites(repo, ds)
    once = sorted(r for r, s in sites.items() if len(s) == 1 and rows.get(r, {}).get("verdict") == "GO")
    out = []
    for rid in rng.sample(once, min(n, len(once))):
        f, i = sites[rid][0]
        out.append({"cls": "citation_id_drift", "req": rid, "file": f, "line": i})
    for rid in rng.sample(once, min(n, len(once))):
        f, i = sites[rid][0]
        out.append({"cls": "citation_deleted", "req": rid, "file": f, "line": i})
    reqs = [r for r in ds["nodes"]["Requirement"] if "@" not in r["id"]]
    by_doc = defaultdict(list)
    for r in reqs:
        doc, line = r["source"].split("#L")
        by_doc[doc].append((r, int(line)))
    docs = sorted(d for d, rs in by_doc.items() if len(rs) >= 2)
    for d in rng.sample(docs, min(n, len(docs))):
        (a, _), (b, lb) = rng.sample(by_doc[d], 2)
        out.append({"cls": "duplicate_id", "req": a["id"], "other": b["id"], "file": repo / d, "line": lb - 1})
    with_method = [r for r in reqs if len(r["methods"]) == 1 and r["methods_raw"].strip()]
    for r in rng.sample(with_method, min(n, len(with_method))):
        doc, line = r["source"].split("#L")
        out.append({"cls": "method_blanked", "req": r["id"], "raw": r["methods_raw"], "file": repo / doc, "line": int(line) - 1})
    uncited = sorted(r["id"] for r in reqs if r["id"] not in sites and rows.get(r["id"], {}).get("reasons") == ["NO_CITING_TEST"])
    owner = {e["dst"]: e["src"] for e in ds["edges"] if e["type"] == "SATISFIES"}
    hosts = sorted(r for r in sites if len(sites[r]) >= 1)
    for y in rng.sample(uncited, min(n, len(uncited))):
        host = rng.choice([h for h in hosts if owner[h] != owner[y]])
        f, i = sites[host][0]
        out.append({"cls": "cross_component_cite", "req": y, "host": host, "file": f, "line": i})
    types = fprime.component_types(repo)
    used = defaultdict(set)
    for e in ds["edges"]:
        if e["type"] == "USES_PORT":
            used[e["src"]].add(e["dst"])
    ptypes = sorted(p["id"] for p in ds["nodes"]["PortType"] if p.get("defined_in") and not p["id"].startswith("port:Fw."))
    comps = sorted({d for d in types.values() if (repo / d).is_dir()})
    for _ in range(n):
        c = rng.choice(comps)
        t = rng.choice([p for p in ptypes if p not in used[c]])
        fpp = next((f for f in sorted((repo / c).glob("*.fpp")) if re.search(r"^\s*(active|passive|queued)\s+component\s+\w+", f.read_text(encoding="utf-8", errors="replace"), re.M)), None)
        if fpp:
            out.append({"cls": "port_type_use_added", "comp": c, "port": t, "file": fpp})
    return out


# ---------------------------------------------------------------- apply / check
def apply(t: dict) -> str:
    f: Path = t["file"]
    original = f.read_text(encoding="utf-8", errors="replace")
    lines = original.split("\n")
    cls = t["cls"]
    if cls == "citation_id_drift":
        lines[t["line"]] = lines[t["line"]].replace(f'"{t["req"]}"', f'"ISF-{t["req"]}"', 1)
    elif cls == "citation_deleted":
        lines[t["line"]] = CITE.sub("", lines[t["line"]]).replace(";", "", 1) if lines[t["line"]].strip().startswith("REQUIREMENT") else CITE.sub("(void)0", lines[t["line"]])
    elif cls == "duplicate_id":
        lines[t["line"]] = lines[t["line"]].replace(t["other"], t["req"], 1)
    elif cls == "method_blanked":
        ln = lines[t["line"]]
        idx = ln.rfind(t["raw"].strip())
        lines[t["line"]] = ln[:idx] + " " * len(t["raw"].strip()) + ln[idx + len(t["raw"].strip()):]
    elif cls == "cross_component_cite":
        indent = re.match(r"\s*", lines[t["line"]]).group(0)
        lines.insert(t["line"] + 1, f'{indent}REQUIREMENT("{t["req"]}");')
    elif cls == "port_type_use_added":
        name = t["port"].removeprefix("port:")
        for i, ln in enumerate(lines):
            if fprime.COMPONENT.match(ln):
                lines.insert(i + 1, f"    output port gonogoMutOut: {name}")
                break
    f.write_text("\n".join(lines), encoding="utf-8")
    return original


def check(t: dict, after: dict, grep: dict, repo: Path, store: Path) -> tuple[bool, bool]:
    cls, rows = t["cls"], after["rows"]
    r = rows.get(t.get("req"), {})
    if cls == "citation_id_drift":
        ours = r.get("verdict") == "INCOMPLETE" and "CITED_UNDER_DIFFERENT_ID" in r["reasons"] and f"ISF-{t['req']}" in after["unknown"]
        g = t["req"] in grep["uncited"] and f"ISF-{t['req']}" in grep["dangling"]
    elif cls == "citation_deleted":
        ours = r.get("verdict") == "INCOMPLETE" and "NO_CITING_TEST" in r["reasons"]
        g = t["req"] in grep["uncited"]
    elif cls == "duplicate_id":
        ours = t["req"] in after["dups"] and "AMBIGUOUS_ID" in r.get("reasons", [])
        g = t["req"] in grep["dup"]
    elif cls == "method_blanked":
        ours = "NO_METHOD" in r.get("reasons", [])
        g = t["req"] in grep["empty"]
    elif cls == "cross_component_cite":
        ours = "CITED_BY_OTHER_COMPONENT" in r.get("reasons", []) and t["req"] in after["cross"]
        g = t["req"] in grep["uncited"]   # grep sees "cited" and cannot tell which component cites it
    else:  # port_type_use_added
        ours = t["comp"] in impact_components(store, t["port"])
        g = t["comp"] in grep_port_users(repo, t["port"])
    return ours, g


def collateral(t: dict, base: dict, after: dict) -> list[str]:
    touched = {t.get("req"), t.get("other")}
    if t["cls"] == "duplicate_id":
        touched |= {f"{t['req']}@{c}" for c in {p["id"] for p in after["ds"]["nodes"]["Part"]}}
        touched.discard(t.get("other"))
    changed = []
    for rid, r in base["rows"].items():
        if rid in touched:
            continue
        a = after["rows"].get(rid)
        if a is None or (a["verdict"], a["reasons"]) != (r["verdict"], r["reasons"]):
            if t["cls"] == "duplicate_id" and rid == t.get("other"):
                continue
            changed.append(rid)
    return changed


def main() -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=20)
    ap.add_argument("--seed", type=int, default=2026)
    a = ap.parse_args()
    if not SRC.exists():
        raise SystemExit("data/raw/fprime missing: clone nasa/fprime at the dataset commit first")
    work = Path(tempfile.mkdtemp(prefix="gonogo_mut_"))
    repo, store = work / "repo", work / "store"
    for root in fprime.ROOTS:
        shutil.copytree(SRC / root, repo / root, ignore=shutil.ignore_patterns(".git", "build-*"))
    t0 = time.time()
    base = pipeline(repo, store)
    base_grep = grep_view(repo)
    rng = random.Random(a.seed)
    plan = targets(repo, base, rng, a.per_class)
    results = []
    for t in plan:
        original = apply(t)
        try:
            after = pipeline(repo, store)
            ours, g = check(t, after, grep_view(repo), repo, store)
            coll = collateral(t, base, after) if t["cls"] != "port_type_use_added" else []
        finally:
            t["file"].write_text(original, encoding="utf-8")
        results.append({"class": t["cls"], "target": t.get("req") or f"{t['comp']} uses {t['port']}",
                        "file": str(t["file"].relative_to(repo)), "gonogo": ours, "grep": g, "collateral": coll})
        print(f"{t['cls']:<22} {results[-1]['target']:<40} gonogo={ours!s:<5} grep={g!s:<5} collateral={len(coll)}")
    shutil.rmtree(work, ignore_errors=True)
    summary = {}
    for cls in sorted({r["class"] for r in results}):
        rs = [r for r in results if r["class"] == cls]
        summary[cls] = {"mutations": len(rs), "gonogo_detected": sum(r["gonogo"] for r in rs),
                        "grep_detected": sum(r["grep"] for r in rs),
                        "with_collateral": sum(bool(r["collateral"]) for r in rs)}
    res = {"seed": a.seed, "per_class": a.per_class, "fprime_commit": fprime._git_sha(SRC), "baseline_unchanged_check":
           base_grep is not None, "seconds": round(time.time() - t0, 1), "summary": summary, "mutations": results}
    (OUT / "mutation_results.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    (OUT / "MUTATION_RESULTS.md").write_text(render(res), encoding="utf-8")
    return res


def render(res: dict) -> str:
    lines = ["# Mutation evaluation on real F´ sources (generated by `python -m eval.mutation_eval`; do not edit)", "",
             f"F´ commit `{res['fprime_commit']}`, seed {res['seed']}, up to {res['per_class']} mutations per class, "
             f"{res['seconds']} s. Every mutation re-runs the full pipeline (text -> dataset -> Relate -> Cypher).", "",
             "| Defect class | Mutations | GoNoGo detected | grep baseline detected | Mutations with collateral changes |",
             "|---|---|---|---|---|"]
    for cls, s in res["summary"].items():
        lines.append(f"| {cls} | {s['mutations']} | {s['gonogo_detected']} | {s['grep_detected']} | {s['with_collateral']} |")
    lines += ["", "Collateral = a requirement other than the planted one whose verdict or reasons changed.",
              "The grep baseline is global (not scoped per component); see the module docstring for the exact commands."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
