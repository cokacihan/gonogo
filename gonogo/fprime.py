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
LEGACY_REF = "Ref"  # releases before 4.x keep the Ref deployment at the repository root


def roots(repo: Path) -> list[str]:
    """Source roots that exist in this checkout (layout differs between F´ releases)."""
    out = [r for r in ROOTS if (repo / r).is_dir()]
    if not (repo / "TestDeploymentsProject" / "Ref").is_dir() and (repo / LEGACY_REF).is_dir():
        out.append(LEGACY_REF)
    return out


def ref_top(repo: Path) -> Path | None:
    for cand in (REF_TOP, f"{LEGACY_REF}/Top"):
        if (repo / cand / "topology.fpp").exists():
            return repo / cand
    return None
SKIP_PARTS = {"googletest", "test", "build-fprime-automatic-native"}

REQ_ID = re.compile(r"^[A-Z][A-Z0-9]*(?:[-_][A-Z0-9]+)*[-_]\d+[A-Za-z]?$")
REQ_MACRO = re.compile(r'REQUIREMENT\(\s*"([^"]+)"\s*\)')
# function definition start, e.g. "void HealthTester ::nominalTlm() {" (return type, optional Class ::, name)
FUNC_DEF = re.compile(r"^\s*(?:[\w:<>,*&]+\s+)+(?:[A-Za-z_]\w*\s*::\s*)*~?([A-Za-z_]\w*)\s*\([^;]*$")
NOT_FUNCS = {"if", "for", "while", "switch", "return", "else", "sizeof", "catch"}
GTEST = re.compile(r"^\s*TEST(?:_F)?\(\s*(\w+)\s*,\s*(\w+)\s*\)")
METHOD_CALL = re.compile(r"\b\w+\s*(?:\.|->)\s*(\w+)\s*\(")
PORT_DECL = re.compile(r"^\s*(?:(sync|async|guarded)\s+)?(input|output)\s+port\s+(\w+)\s*:\s*(?:\[[^\]]*\]\s*)?([A-Za-z_][\w.]*)")
SPECIAL_PORT = re.compile(r"^\s*(command recv|command reg|command resp|event|text event|telemetry|time get|param get|"
                          r"param set|product get|product request|product recv|product send)\s+port\s+(\w+)")
PORT_TYPE_DEF = re.compile(r"^\s*port\s+(\w+)\s*(?:\(|$)")
SPECIAL_TYPES = {"command recv": "Fw.Cmd", "command reg": "Fw.CmdReg", "command resp": "Fw.CmdResponse",
                 "event": "Fw.Log", "text event": "Fw.LogText", "telemetry": "Fw.Tlm", "time get": "Fw.Time",
                 "param get": "Fw.PrmGet", "param set": "Fw.PrmSet", "product get": "Fw.DpGet",
                 "product request": "Fw.DpRequest", "product recv": "Fw.DpResponse", "product send": "Fw.DpSend"}
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
    for root in roots(repo):
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
            if m and m.group(1) not in NOT_FUNCS:
                func = m.group(1)
            for rid in REQ_MACRO.findall(ln):
                cited[rid].add(func or "?")
        if cited:
            out.append({"path": _rel(f, repo), "cites": {k: sorted(v) for k, v in sorted(cited.items())}})
    return out


CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
_STRING = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')


def _code_lines(text: str):
    """Yield (raw line, code without comments/strings) so braces in strings never confuse depth."""
    in_block = False
    for raw in text.splitlines():
        code = _STRING.sub('""', raw)
        if in_block:
            if "*/" not in code:
                yield raw, ""
                continue
            code, in_block = code.split("*/", 1)[1], False
        while "/*" in code:
            head, tail = code.split("/*", 1)
            if "*/" in tail:
                code = head + tail.split("*/", 1)[1]
            else:
                code, in_block = head, True
        yield raw, code.split("//", 1)[0]


def test_code(comp_dir: Path, repo: Path) -> dict:
    """Test sources of one component as a call graph: gtest cases, functions, calls between them, and the
    REQUIREMENT("ID") citations written in each body. No closure is computed here: the graph does that
    (TestCase)-[:CALLS*0..N]->(x)-[:CITES]->(Requirement). Same-named functions in one component's tests are
    merged into one node, which can over-approximate the calls.
    """
    test_dir = comp_dir / "test"
    if not test_dir.is_dir():
        return {"functions": {}, "cases": []}
    funcs: dict[str, dict] = defaultdict(lambda: {"calls": set(), "cites": set(), "files": set()})
    cases: list[dict] = []
    for f in sorted(p for p in test_dir.rglob("*") if p.suffix in (".cpp", ".hpp", ".cc", ".h") and p.is_file()):
        depth, cur, start, opened, own = 0, None, 0, False, None
        for raw, code in _code_lines(f.read_text(encoding="utf-8", errors="replace")):
            if cur is None:
                if (g := GTEST.match(code)):
                    cur = {"suite": g.group(1), "name": g.group(2), "file": _rel(f, repo), "calls": set(), "cites": set()}
                    cases.append(cur)
                    own = None
                elif (m := FUNC_DEF.match(code)) and m.group(1) not in NOT_FUNCS:
                    own = m.group(1)
                    cur = funcs[own]
                    cur["files"].add(_rel(f, repo))
                if cur is not None:
                    start, opened = depth, False
            if cur is not None:
                cur["cites"].update(REQ_MACRO.findall(raw))
                cur["calls"].update(c for c in CALL_RE.findall(code) if c != own and c not in NOT_FUNCS and not c.isupper())
            depth += code.count("{") - code.count("}")
            if cur is not None:
                if "{" in code:
                    opened = True
                if (opened and depth <= start) or (not opened and code.rstrip().endswith(";")):
                    cur = None
    known = set(funcs)
    functions = {n: {"calls": sorted(c for c in v["calls"] if c in known), "cites": sorted(v["cites"]),
                     "files": sorted(v["files"])} for n, v in funcs.items()}
    return {"functions": functions,
            "cases": [{"suite": c["suite"], "name": c["name"], "file": c["file"],
                       "calls": sorted(x for x in c["calls"] if x in known), "cites": sorted(c["cites"])} for c in cases]}


def citation_hops(functions: dict, case: dict) -> dict[str, int]:
    """Fewest CALLS hops from a test case to each requirement ID it cites (0 = cited in the TEST body itself)."""
    best = {r: 0 for r in case["cites"]}
    frontier, seen, hops = set(case["calls"]), set(), 1
    while frontier:
        nxt = set()
        for fn in frontier - seen:
            seen.add(fn)
            for r in functions[fn]["cites"]:
                best.setdefault(r, hops)
            nxt |= set(functions[fn]["calls"])
        frontier, hops = nxt - seen, hops + 1
    return best


def test_cases(comp_dir: Path, repo: Path) -> list[dict]:
    """Convenience view for tests: each case with the IDs it cites through the call graph."""
    code = test_code(comp_dir, repo)
    return [dict(c, cited=sorted(citation_hops(code["functions"], c))) for c in code["cases"]]


# ---------------------------------------------------------------- ports
def _walk_fpp(repo: Path):
    """Yield (file, module path list, inside-component name or None, stripped line) for FPP sources."""
    for root in roots(repo):
        for f in sorted((repo / root).rglob("*.fpp")):
            if _skip(f.relative_to(repo)):
                continue
            stack: list[tuple[str, int, str]] = []  # (name, depth before, kind)
            depth = 0
            for ln in _strip_fpp(f.read_text(encoding="utf-8", errors="replace")):
                if (m := MODULE.match(ln)):
                    stack.append((m.group(1), depth, "module"))
                elif (c := COMPONENT.match(ln)):
                    stack.append((c.group(2), depth, "component"))
                mods = [s for s, _, k in stack if k == "module"]
                comp = next((s for s, _, k in reversed(stack) if k == "component"), None)
                yield f, mods, comp, ln
                depth += ln.count("{") - ln.count("}")
                while stack and depth <= stack[-1][1]:
                    stack.pop()


def port_model(repo: Path, types: dict[str, str]) -> tuple[dict[str, str], dict[str, list[dict]]]:
    """Port type definitions (qualified name -> file) and port declarations per component directory."""
    defs: dict[str, str] = {}
    decls_raw: list[tuple[str, list[str], dict]] = []
    for f, mods, comp, ln in _walk_fpp(repo):
        if comp is None and (p := PORT_TYPE_DEF.match(ln)) and "=" not in ln:
            defs.setdefault(".".join(mods + [p.group(1)]), _rel(f, repo))
        if comp is None:
            continue
        qual = ".".join(mods + [comp])
        if (d := PORT_DECL.match(ln)):
            decls_raw.append((qual, mods, {"name": d.group(3), "direction": d.group(2), "kind": d.group(1) or "",
                                           "type": d.group(4), "special": False}))
        elif (s := SPECIAL_PORT.match(ln)):
            decls_raw.append((qual, mods, {"name": s.group(2), "direction": "special", "kind": s.group(1),
                                           "type": SPECIAL_TYPES[s.group(1)], "special": True}))
    by_dir: dict[str, list[dict]] = defaultdict(list)
    for qual, mods, d in decls_raw:
        t = d["type"]
        cands = [t] if "." in t else []
        cands += [".".join(mods[:k] + [t]) for k in range(len(mods), -1, -1)] + [f"Fw.{t}", f"Svc.{t}", f"Drv.{t}"]
        resolved = next((c for c in cands if c in defs), None)
        comp_dir = types.get(qual)
        if comp_dir:
            by_dir[comp_dir].append(dict(d, type=resolved or t, resolved=resolved is not None))
    return defs, dict(by_dir)


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
        top_dir = ref_top(repo)
        ref_files = sorted(top_dir.glob("*.fpp")) if top_dir else []
        self.defs.update(_instance_defs(ref_files, ""))
        for f in ref_files:
            for name, t in _topologies(_strip_fpp(f.read_text(encoding="utf-8"))).items():
                self.tops[f"Ref.{name}"] = dict(t, module="")
        sub_root = repo / "Svc" / "Subtopologies"
        for st_dir in sorted(p for p in sub_root.iterdir() if p.is_dir()) if sub_root.is_dir() else []:
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
    if "Ref.Ref" not in m.tops:
        return {"subtopologies": [], "topologies": [], "instances": {}, "connections": [], "unresolved": []}
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
    for root in roots(repo):
        for f in sorted((repo / root).rglob("sdd.md")):
            if f.parent.name == "docs" and not _skip(f.relative_to(repo)):
                sdds[_rel(f.parent.parent, repo)] = f

    part_dirs = sorted(set(dirs_with_types) | set(sdds))
    nodes: dict[str, list[dict]] = defaultdict(list)
    edges: list[dict] = []
    req_seen: dict[str, str] = {}
    duplicates: list[dict] = []
    max_hops = 0  # longest shortest CALLS chain from a test case to a REQUIREMENT() it relies on

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
                    n = 2
                    while any(x["id"] == rid for x in nodes["Requirement"]):  # defined 3+ times
                        rid, n = f"{r['id']}@{d}#{n}", n + 1
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
        # test code as a call graph: TestCase -CALLS-> TestFunction -CALLS-> ... ; CITES only where the macro is written
        code = test_code(repo / d, repo)
        fn_id = {name: f"{d}::fn:{name}" for name in code["functions"]}
        for name, fn in sorted(code["functions"].items()):
            nodes["TestFunction"].append({"id": fn_id[name], "name": name, "component": d, "files": fn["files"],
                                          "raw_cites": fn["cites"], "text": f"Test helper {_words(name)} of {d}"})
            edges.append({"src": fn_id[name], "type": "TESTS", "dst": d})
            edges += [{"src": fn_id[name], "type": "CALLS", "dst": fn_id[c]} for c in fn["calls"]]
        seen_cases: set[str] = set()
        for tc in code["cases"]:
            cid = f"{d}::{tc['suite']}.{tc['name']}"
            if cid in seen_cases:  # same gtest name in two test programs of one component
                cid = f"{cid}@{tc['file']}"
            n = 2
            while cid in seen_cases:  # same name twice in one file (e.g. under #ifdef branches)
                cid, n = f"{cid}#{n}", n + 1
            seen_cases.add(cid)
            max_hops = max([max_hops, *citation_hops(code["functions"], tc).values()])
            nodes["TestCase"].append({"id": cid, "suite": tc["suite"], "name": tc["name"], "file": tc["file"],
                                      "component": d, "gtest_filter": f"{tc['suite']}.{tc['name']}",
                                      "raw_cites": tc["cites"], "text": f"Unit test case {tc['suite']}.{tc['name']} of {d}"})
            edges.append({"src": cid, "type": "TESTS", "dst": d})
            edges += [{"src": cid, "type": "CALLS", "dst": fn_id[c]} for c in tc["calls"]]

    req_ids = {r["id"] for r in nodes["Requirement"]}
    for x in nodes["TestCase"] + nodes["TestFunction"]:
        raw = x.pop("raw_cites")
        x["unknown_citations"] = [r for r in raw if r not in req_ids]
        edges += [{"src": x["id"], "type": "CITES", "dst": r} for r in raw if r in req_ids]
    file_ids = {tf["id"] for tf in nodes["TestFile"]}
    dangling = [e for e in edges if e["type"] == "CITES" and e["src"] in file_ids and e["dst"] not in req_ids]
    unknown_by_file: dict[str, list[str]] = defaultdict(list)
    for e in dangling:
        unknown_by_file[e["src"]].append(e["dst"])
    for tf in nodes["TestFile"]:
        tf["unknown_citations"] = sorted(unknown_by_file.get(tf["id"], []))
    edges = [e for e in edges if not (e["type"] == "CITES" and e["dst"] not in req_ids)]
    # file-level evidence: a Unit Test verification is evidenced by a test file citing that requirement
    ver_ids = {v["id"] for v in nodes["Verification"]}
    for e in [x for x in edges if x["type"] == "CITES" and x["src"] in file_ids]:
        vid = f"VER:{e['dst']}:UNIT_TEST"
        if vid in ver_ids:
            edges.append({"src": vid, "type": "EVIDENCED_BY", "dst": e["src"]})

    port_defs, ports_by_dir = port_model(repo, types)
    used_types = {p["type"] for ps in ports_by_dir.values() for p in ps}
    for t in sorted(used_types | set(port_defs)):
        nodes["PortType"].append({"id": f"port:{t}", "name": t, "defined_in": port_defs.get(t),
                                  "text": f"F Prime port type {t} {_words(t.split('.')[-1])}"})
    for d, ps in sorted(ports_by_dir.items()):
        for p in ps:
            edges.append({"src": d, "type": "USES_PORT", "dst": f"port:{p['type']}", "port": p["name"],
                          "direction": p["direction"], "kind": p["kind"], "special": p["special"]})

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
                        "max_citation_hops": max_hops,
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
