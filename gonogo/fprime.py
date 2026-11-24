"""Import a real digital thread from NASA JPL's F Prime (F´) flight software framework.

Source: https://github.com/nasa/fprime (Apache-2.0, Copyright California Institute of Technology).
Only plain text is read (Markdown SDDs, FPP models, C++ test sources); nothing from the
checkout is executed.

Extracted:
  * Part         one per component directory (e.g. Svc/CmdDispatcher) with its FPP type names
  * Requirement  rows of the "Requirements" table in <dir>/docs/sdd.md (IDs as written, e.g. CD-002)
  * Verification one per (requirement, declared method), status DECLARED (we do not run F´ tests)
  * TestFile     unit-test sources that cite requirements with the REQUIREMENT("ID") macro
  * Instance     component instances of the Ref deployment and the subtopologies it uses
  * CONNECTS_TO  explicit port connections (output -> input) of the Ref deployment
"""
from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path

ROOTS = ["Svc", "Drv", "Fw", "Os", "CFDP", "Utils", "TestDeploymentsProject/Ref"]
REF_TOP = "TestDeploymentsProject/Ref/Top"
SKIP_PARTS = {"googletest", "test", "build-fprime-automatic-native"}

REQ_ID = re.compile(r"^[A-Z][A-Z0-9]*(?:[-_][A-Z0-9]+)*[-_]\d+[A-Za-z]?$")
REQ_MACRO = re.compile(r'REQUIREMENT\(\s*"([^"]+)"\s*\)')
FUNC_DEF = re.compile(r"^\s*(?:void|bool|int|[A-Za-z_:<>]+)\s+(?:[A-Za-z_]\w*::)*([A-Za-z_]\w*)\s*\([^;]*$")
COMPONENT = re.compile(r"^\s*(active|passive|queued)\s+component\s+(\w+)\b")
MODULE = re.compile(r"^\s*module\s+(\w+)\s*\{")
INSTANCE_DEF = re.compile(r"^\s*instance\s+(\$?\w+)\s*:\s*([\w.]+)")
CONNECTION = re.compile(r"^\s*([\w.$]+?)\.(\$?\w+)(?:\[[^\]]*\])?\s*->\s*([\w.$]+?)\.(\$?\w+)(?:\[[^\]]*\])?\s*$")
PORT_ALIAS = re.compile(r"^\s*port\s+(\w+)\s*=\s*(\$?\w+)\.(\$?\w+)")
SUBTOP_USE = re.compile(r"^\s*instance\s+(\w+)\.Subtopology\s*$")
TOP_INSTANCE_USE = re.compile(r"^\s*instance\s+(\$?\w+)\s*$")

METHOD_NORMAL = {"unit test": "Unit Test", "unit tests": "Unit Test", "unittest": "Unit Test",
                 "inspection": "Inspection", "system test": "System Test", "analysis": "Analysis",
                 "demonstration": "Demonstration", "test": "Test", "integration test": "Integration Test"}


def normalize_methods(raw: str) -> list[str]:
    """Known verification methods named in a table cell; free text (e.g. a rationale) yields []."""
    out = set()
    for tok in re.split(r"[,/;&]| and ", raw):
        key = re.sub(r"\s+", " ", re.sub(r"[-_]", " ", tok)).strip().lower().rstrip(".")
        if key in METHOD_NORMAL:
            out.add(METHOD_NORMAL[key])
    return sorted(out)


def _words(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).lower()


def _rel(p: Path, repo: Path) -> str:
    return p.relative_to(repo).as_posix()


def _skip(p: Path) -> bool:
    return any(part in SKIP_PARTS for part in p.parts)


# ---------------------------------------------------------------- FPP parsing
def _strip_fpp(text: str) -> list[str]:
    """Remove triple-quoted code blocks and comments; keep line structure."""
    text = re.sub(r'"""[\s\S]*?"""', lambda m: "\n" * m.group(0).count("\n"), text)
    return [re.split(r"(?<!\S)[#@]", ln, maxsplit=1)[0] for ln in text.splitlines()]


def component_types(repo: Path) -> dict[str, str]:
    """Qualified FPP component type (e.g. Svc.CommandDispatcher) -> component directory."""
    out: dict[str, str] = {}
    for root in ROOTS:
        for f in sorted((repo / root).rglob("*.fpp")):
            if _skip(f.relative_to(repo)):
                continue
            stack: list[tuple[str, int]] = []
            depth = 0
            for ln in _strip_fpp(f.read_text(encoding="utf-8", errors="replace")):
                m = MODULE.match(ln)
                if m:
                    stack.append((m.group(1), depth))
                c = COMPONENT.match(ln)
                if c:
                    qual = ".".join([s for s, _ in stack] + [c.group(2)])
                    out.setdefault(qual, _rel(f.parent, repo))
                depth += ln.count("{") - ln.count("}")
                while stack and depth <= stack[-1][1]:
                    stack.pop()
    return out


# ---------------------------------------------------------------- SDD parsing
def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def parse_sdd(path: Path) -> list[dict]:
    """Rows of the first Markdown table under a heading containing 'requirement'."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    reqs: list[dict] = []
    in_section = False
    i = 0
    while i < len(lines):
        ln = lines[i]
        if re.match(r"^#+\s", ln):
            in_section = "requirement" in ln.lower()
        elif in_section and "|" in ln and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1]):
            header = [h.lower() for h in _cells(ln)]
            col = {"desc": None, "method": None, "rationale": None}
            for k, h in enumerate(header):
                if "desc" in h and col["desc"] is None:
                    col["desc"] = k
                elif ("verif" in h or "valid" in h or "method" in h) and col["method"] is None:
                    col["method"] = k
                elif "rational" in h and col["rationale"] is None:
                    col["rationale"] = k
            j = i + 2
            while j < len(lines) and "|" in lines[j]:
                cells = _cells(lines[j])
                rid = re.sub(r"[*`]", "", cells[0]).strip()
                if REQ_ID.match(rid):
                    def get(key):
                        k = col[key]
                        return cells[k] if k is not None and k < len(cells) else ""
                    raw_methods = get("method")
                    methods = normalize_methods(raw_methods)
                    reqs.append({"id": rid, "text": get("desc"), "rationale": get("rationale"),
                                 "methods_raw": raw_methods, "methods": methods, "line": j + 1})
                j += 1
            i = j
            continue
        i += 1
    return reqs


# ---------------------------------------------------------------- tests
def test_citations(comp_dir: Path, repo: Path) -> list[dict]:
    """REQUIREMENT("ID") citations in the component's test sources, with the enclosing function."""
    out = []
    test_dir = comp_dir / "test"
    if not test_dir.is_dir():
        return out
    for f in sorted(test_dir.rglob("*")):
        if f.suffix not in (".cpp", ".hpp", ".c", ".h") or not f.is_file():
            continue
        func = None
        cited: dict[str, set[str]] = defaultdict(set)
        for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = FUNC_DEF.match(ln)
            if m:
                func = m.group(1)
            for rid in REQ_MACRO.findall(ln):
                cited[rid].add(func or "?")
        if cited:
            out.append({"path": _rel(f, repo), "cites": {k: sorted(v) for k, v in sorted(cited.items())}})
    return out


# ---------------------------------------------------------------- topology
def _instance_defs(files: list[Path], prefix: str) -> dict[str, str]:
    defs = {}
    for f in files:
        for ln in _strip_fpp(f.read_text(encoding="utf-8", errors="replace")):
            m = INSTANCE_DEF.match(ln)
            if m:
                defs[prefix + m.group(1)] = m.group(2)
    return defs


def _topologies(lines: list[str]) -> dict[str, dict]:
    """topology NAME { ... } blocks: instances, imports, connections, port aliases."""
    tops: dict[str, dict] = {}
    cur, depth, graph = None, 0, None
    for ln in lines:
        m = re.match(r"^\s*(?:deployment\s+)?topology\s+(\w+)\s*\{", ln)
        if m and cur is None:
            cur = {"instances": [], "imports": [], "connections": [], "aliases": {}}
            tops[m.group(1)] = cur
            depth = 0
        if cur is not None:
            if (g := re.match(r"^\s*connections\s+(\w+)\s*\{", ln)):
                graph = g.group(1)
            elif (im := re.match(r"^\s*import\s+([\w.]+)\s*$", ln)):
                cur["imports"].append(im.group(1))
            elif (iu := re.match(r"^\s*instance\s+([\w.$]+)\s*$", ln)):
                cur["instances"].append(iu.group(1))
            elif (c := CONNECTION.match(ln)) and graph:
                cur["connections"].append((c.group(1), c.group(2), c.group(3), c.group(4), graph))
            elif (pa := PORT_ALIAS.match(ln)):
                cur["aliases"][pa.group(1)] = (pa.group(2), pa.group(3))
            depth += ln.count("{") - ln.count("}")
            if depth <= 0:
                cur, graph = None, None
    return tops


class _Model:
    """All topologies of the Ref deployment and of the subtopology modules in Svc/Subtopologies."""

    def __init__(self, repo: Path):
        self.tops: dict[str, dict] = {}   # "Module.Topology" -> block (+ "module")
        self.defs: dict[str, str] = {}    # qualified instance -> component type
        ref_files = sorted((repo / REF_TOP).glob("*.fpp"))
        self.defs.update(_instance_defs(ref_files, ""))
        for f in ref_files:
            for name, t in _topologies(_strip_fpp(f.read_text(encoding="utf-8"))).items():
                self.tops[f"Ref.{name}"] = dict(t, module="")
        for st_dir in sorted(p for p in (repo / "Svc" / "Subtopologies").iterdir() if p.is_dir()):
            mod = st_dir.name
            files = sorted(st_dir.rglob("*.fpp"))
            self.defs.update(_instance_defs(files, f"{mod}."))
            for f in files:
                for name, t in _topologies(_strip_fpp(f.read_text(encoding="utf-8", errors="replace"))).items():
                    self.tops[f"{mod}.{name}"] = dict(t, module=mod)

    def qualify(self, top: str, local: str) -> str:
        mod = self.tops[top]["module"]
        return f"{mod}.{local}" if mod else local

    def _imported(self, top: str, name: str) -> str | None:
        t = self.tops[top]
        for imp in t["imports"]:
            full = imp if "." in imp else (f"{t['module']}.{imp}" if t["module"] else imp)
            if imp.split(".")[-1] == name or imp == name:
                return full if full in self.tops else None
        return None

    def resolve(self, top: str, inst: str, port: str, depth: int = 0) -> tuple[str | None, str | None]:
        """Map an endpoint written inside topology `top` to a concrete (instance, port)."""
        if depth > 10:
            return None, None
        target = inst if inst in self.tops else self._imported(top, inst)
        if target:  # endpoint is a port exported by another topology
            alias = self.tops[target]["aliases"].get(port)
            return self.resolve(target, *alias, depth=depth + 1) if alias else (None, None)
        q = self.qualify(top, inst)
        return (q, port) if q in self.defs else (None, None)

    def closure(self, top: str) -> list[str]:
        out, stack = [], [top]
        while stack:
            cur = stack.pop()
            if cur in out:
                continue
            out.append(cur)
            t = self.tops[cur]
            stack += [x for x in (self._imported(cur, i.split(".")[-1]) for i in t["imports"]) if x]
            stack += [i for i in t["instances"] if i in self.tops]
        return out


def ref_topology(repo: Path) -> dict:
    m = _Model(repo)
    tops = m.closure("Ref.Ref")
    instances: dict[str, str] = {}
    resolved, unresolved = [], []
    for top in tops:
        t = m.tops[top]
        for i in t["instances"]:
            q = m.qualify(top, i)
            if q in m.defs:
                instances[q] = m.defs[q]
        for a, ap, b, bp, graph in t["connections"]:
            ra, rap = m.resolve(top, a, ap)
            rb, rbp = m.resolve(top, b, bp)
            if ra and rb:
                resolved.append({"src": ra, "src_port": rap, "dst": rb, "dst_port": rbp, "graph": graph,
                                 "declared_in": top})
            else:
                unresolved.append([top, a, ap, b, bp, graph])
    for c in resolved:  # instances reached through aliases belong to the deployment too
        instances.setdefault(c["src"], m.defs[c["src"]])
        instances.setdefault(c["dst"], m.defs[c["dst"]])
    subtops = sorted({x.split(".")[0] for x in tops if not x.startswith("Ref.")})
    return {"subtopologies": subtops, "topologies": tops, "instances": instances,
            "connections": resolved, "unresolved": unresolved}


# ---------------------------------------------------------------- dataset
def _git_sha(repo: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:  # not a git checkout
        return "unknown"


def build_dataset(repo: Path) -> dict:
    repo = repo.resolve()
    types = component_types(repo)
    dirs_with_types: dict[str, list[str]] = defaultdict(list)
    for t, d in types.items():
        dirs_with_types[d].append(t)

    sdds = {}
    for root in ROOTS:
        for f in sorted((repo / root).rglob("sdd.md")):
            if f.parent.name == "docs" and not _skip(f.relative_to(repo)):
                sdds[_rel(f.parent.parent, repo)] = f

    part_dirs = sorted(set(dirs_with_types) | set(sdds))
    nodes: dict[str, list[dict]] = defaultdict(list)
    edges: list[dict] = []
    req_seen: dict[str, str] = {}
    duplicates: list[dict] = []

    for d in part_dirs:
        comp_dir = repo / d
        has_ut = (comp_dir / "test" / "ut").is_dir() or any((comp_dir / "test").glob("**/*Test*.cpp"))
        t = sorted(dirs_with_types.get(d, []))
        nodes["Part"].append({"id": d, "name": ", ".join(t) or d, "types": t, "subsystem": d.split("/")[0],
                              "has_sdd": d in sdds, "has_unit_tests": bool(has_ut),
                              "text": f"{d} {' '.join(t)} {_words(d.split('/')[-1])} component"})
        if d in sdds:
            doc_id = _rel(sdds[d], repo)
            nodes["Document"].append({"id": doc_id, "title": f"{d} software design document",
                                      "text": f"Software design document of {d}"})
            for r in parse_sdd(sdds[d]):
                rid = r["id"]
                if rid in req_seen:
                    duplicates.append({"id": rid, "first": req_seen[rid], "again": d})
                    rid = f"{rid}@{d}"
                req_seen.setdefault(r["id"], d)
                nodes["Requirement"].append({"id": rid, "level": "COMPONENT", "subsystem": d.split("/")[0],
                                             "component": d, "text": r["text"], "rationale": r["rationale"],
                                             "methods": r["methods"], "methods_raw": r["methods_raw"],
                                             "source": f"{doc_id}#L{r['line']}"})
                edges.append({"src": d, "type": "SATISFIES", "dst": rid})
                edges.append({"src": rid, "type": "DOCUMENTED_IN", "dst": doc_id})
                for m in r["methods"]:
                    vid = f"VER:{rid}:{m.upper().replace(' ', '_')}"
                    nodes["Verification"].append({"id": vid, "method": m, "status": "DECLARED",
                                                  "text": f"{m} verification of {rid} declared in SDD"})
                    edges.append({"src": rid, "type": "VERIFIED_BY", "dst": vid})
        for tf in test_citations(repo / d, repo):
            nodes["TestFile"].append({"id": tf["path"], "text": f"Unit test source {tf['path']}",
                                      "cited_requirements": sorted(tf["cites"])})
            edges.append({"src": tf["path"], "type": "TESTS", "dst": d})
            for rid, funcs in tf["cites"].items():
                edges.append({"src": tf["path"], "type": "CITES", "dst": rid, "functions": funcs})

    req_ids = {r["id"] for r in nodes["Requirement"]}
    dangling = [e for e in edges if e["type"] == "CITES" and e["dst"] not in req_ids]
    unknown_by_file: dict[str, list[str]] = defaultdict(list)
    for e in dangling:
        unknown_by_file[e["src"]].append(e["dst"])
    for tf in nodes["TestFile"]:
        tf["unknown_citations"] = sorted(unknown_by_file.get(tf["id"], []))
    edges = [e for e in edges if not (e["type"] == "CITES" and e["dst"] not in req_ids)]
    # evidence: a Unit Test verification is evidenced by a test file citing that requirement
    for e in [x for x in edges if x["type"] == "CITES"]:
        vid = f"VER:{e['dst']}:UNIT_TEST"
        if any(v["id"] == vid for v in nodes["Verification"]):
            edges.append({"src": vid, "type": "EVIDENCED_BY", "dst": e["src"]})

    topo = ref_topology(repo)
    part_ids = {p["id"] for p in nodes["Part"]}
    for name, typ in sorted(topo["instances"].items()):
        nodes["Instance"].append({"id": f"Ref:{name}", "name": name, "type": typ, "deployment": "Ref",
                                  "text": f"Ref deployment instance {name} of {typ}"})
        comp = types.get(typ)
        if comp in part_ids:
            edges.append({"src": f"Ref:{name}", "type": "INSTANCE_OF", "dst": comp})
    for c in topo["connections"]:
        edges.append({"src": f"Ref:{c['src']}", "type": "CONNECTS_TO", "dst": f"Ref:{c['dst']}",
                      "src_port": c["src_port"], "dst_port": c["dst_port"], "graph": c["graph"]})

    return {
        "source": {"repo": "https://github.com/nasa/fprime", "commit": _git_sha(repo), "license": "Apache-2.0",
                   "copyright": "California Institute of Technology"},
        "kind": "fprime",
        "mission": "NASA F Prime (real, open source)",
        "seed": None,
        "nodes": dict(nodes),
        "edges": edges,
        "diagnostics": {"duplicate_requirement_ids": duplicates, "dangling_citations": dangling,
                        "unresolved_connections": topo["unresolved"], "subtopologies": topo["subtopologies"],
                        "instance_types_without_component": sorted({t for t in topo["instances"].values()
                                                                    if types.get(t) not in part_ids})},
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Import NASA F Prime into a TraceGraph dataset")
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[1] / "data" / "raw" / "fprime"))
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "data" / "fprime_dataset.json"))
    a = ap.parse_args()
    ds = build_dataset(Path(a.repo))
    Path(a.out).write_text(json.dumps(ds, indent=1), encoding="utf-8")
    print({k: len(v) for k, v in ds["nodes"].items()}, "edges:", len(ds["edges"]))
    print({k: (len(v) if isinstance(v, list) else v) for k, v in ds["diagnostics"].items()})
