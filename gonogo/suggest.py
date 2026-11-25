"""Close the loop: for a requirement that no own test cites, which test code most likely verifies it already, and
where would the `REQUIREMENT("ID");` line go?

Two steps, both in Relate:
  1. the full-text index `test_vocab` ranks the requirement's words against the words of every test body (TEST
     cases and helper functions; identifiers only, no comments or strings) of the requirement's own component;
  2. the call graph lifts a matching helper to the test cases that execute it:
     (tc:TestCase)-[:CALLS*0..8]->(site). A case ranks by its best-matching site, then by the sum.
The suggestion is a candidate for a human to confirm, never evidence: a requirement only turns GO when the
REQUIREMENT() line is written and the citing test passes in CI. `eval/suggest_eval.py` measures how often the
place where F´ developers actually wrote the macro is in the top k, against a random ranking.
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path

from gonogo.verdict import CITE_HOPS, current_run
from gonogo.vocab import code_words, text_words

INDEX = "test_vocab"  # identifiers + TEST_CASE()/COMMENT() descriptions; "test_code_vocab" has identifiers only

SITES_CYPHER = """
MATCH (p:Part)-[:SATISFIES]->(r:Requirement {id: $id})
CALL db.index.fulltext.queryNodes($index, $terms) YIELD node AS site, score
WITH p, site, score WHERE (site)-[:TESTS]->(p)
RETURN site.id AS site, coalesce(site.gtest_filter, site.name) AS name,
       CASE WHEN site:TestCase THEN 'TEST' ELSE 'function' END AS kind,
       coalesce(site.at, [site.file + ':' + toString(site.line)]) AS at, score
ORDER BY score DESC, site
LIMIT $k
"""

CASES_CYPHER = f"""
MATCH (p:Part)-[:SATISFIES]->(r:Requirement {{id: $id}})
CALL db.index.fulltext.queryNodes($index, $terms) YIELD node AS site, score
WITH p, site, score WHERE (site)-[:TESTS]->(p)
MATCH (tc:TestCase)-[:TESTS]->(p)
WHERE tc = site OR ($lift AND EXISTS {{ MATCH (tc)-[:CALLS*1..{CITE_HOPS}]->(site) }})
WITH tc, site, score ORDER BY score DESC
WITH tc, collect(coalesce(site.name, site.gtest_filter)) AS via, max(score) AS best, sum(score) AS total
OPTIONAL MATCH (tc)-[ran:RAN_IN]->(:TestRun {{id: $run}})
RETURN tc.id AS test_case, tc.gtest_filter AS gtest_filter, best, total, via[..3] AS via,
       coalesce(ran.status, 'NOT_RUN') AS status
ORDER BY best DESC, total DESC, test_case
LIMIT $k
"""

REQ_CYPHER = """
MATCH (p:Part)-[:SATISFIES]->(r:Requirement {id: $id})
OPTIONAL MATCH (r)<-[:CITES]-(s)-[:TESTS]->(p) WHERE s:TestCase OR s:TestFunction
RETURN r.id AS id, r.text AS text, p.id AS component, collect(coalesce(s.name, s.gtest_filter)) AS cited_at
"""

# a near-miss ID written in this component's tests (ISF-HTH-001 for HTH-001): the fix is a rename, not a new test
DRIFT_CYPHER = """
MATCH (p:Part)-[:SATISFIES]->(r:Requirement {id: $id})
MATCH (s)-[:TESTS]->(p) WHERE (s:TestCase OR s:TestFunction)
  AND any(u IN s.unknown_citations WHERE u ENDS WITH '-' + $ref OR u ENDS WITH '_' + $ref)
RETURN coalesce(s.name, s.gtest_filter) AS site, coalesce(s.at, [s.file + ':' + toString(s.line)]) AS at,
       [u IN s.unknown_citations WHERE u ENDS WITH '-' + $ref OR u ENDS WITH '_' + $ref][0] AS cited
ORDER BY site
"""


def query_terms(text: str, component: str) -> list[str]:
    """Requirement words without the component's own name: inside one component it matches every test."""
    own = code_words(component.replace("/", " "))
    terms = [w for w in text_words(text) if w not in own]
    return terms or text_words(text)


def requirement(db, rid: str) -> dict | None:
    row = db.execute(REQ_CYPHER, {"id": rid}).fetchone()
    return row.data() if row and row["id"] else None


def rank_sites(db, rid: str, terms: list[str], k: int = 5, index: str = INDEX) -> list[dict]:
    return [r.data() for r in db.execute(SITES_CYPHER, {"id": rid, "terms": " ".join(terms), "k": k, "index": index})]


def rank_cases(db, rid: str, terms: list[str], k: int = 5, run: str | None = None, lift: bool = True,
               index: str = INDEX) -> list[dict]:
    return [r.data() for r in db.execute(CASES_CYPHER, {"id": rid, "terms": " ".join(terms), "k": k,
                                                        "run": run, "lift": lift, "index": index})]


def suggest(db, rid: str, k: int = 5) -> dict | None:
    req = requirement(db, rid)
    if req is None:
        return None
    terms = query_terms(req["text"], req["component"])
    drift = [r.data() for r in db.execute(DRIFT_CYPHER, {"id": rid, "ref": rid.split("@")[0]})]
    sites = rank_sites(db, rid, terms, k)
    cases = rank_cases(db, rid, terms, k, run=current_run(db))
    ref = rid.split("@")[0]
    if req["cited_at"]:  # nothing to add: the component's tests already cite it (its verdict says whether they passed)
        edit = f"none: already cited by test code of {req['component']} at {', '.join(req['cited_at'])} (gonogo why {ref})"
    elif drift:
        edit = f'rename {drift[0]["cited"]} to {ref} in REQUIREMENT() at ' + ", ".join(a for d in drift for a in d["at"])
    elif not (req["text"] or "").strip():
        edit = "none proposed: the SDD row has no requirement text to match against the test code"
    elif sites:
        top = sites[0]
        where = "the TEST body" if top["kind"] == "TEST" else f"function {top['name']}"
        edit = f'add REQUIREMENT("{ref}"); to {where} ({", ".join(top["at"])}), if it checks this'
    else:
        edit = "no test code of the component shares a word with the requirement: a new test is needed"
    return {**req, "terms": terms, "drift": drift, "sites": sites, "cases": cases, "edit": edit}


UNIT_TEST_H = "Fw/Test/UnitTest.hpp"  # defines REQUIREMENT(), TEST_CASE(), COMMENT() in F´


def _body_start(lines: list[str], line: int) -> int | None:
    """Index of the line holding the opening brace of the definition that starts at 1-based `line`;
    None for a declaration (a `;` comes first)."""
    for i in range(line - 1, min(len(lines), line + 8)):
        code = lines[i].split("//", 1)[0]
        if "{" in code:
            return i
        if ";" in code:
            return None
    return None


def _definition_line(lines: list[str], line: int, site: dict) -> int | None:
    """The 1-based line where the site is defined. The graph's line is used when it still names the site; otherwise
    (a checkout at another commit) the definition is looked up by name, so the macro never lands in another body."""
    name = site.get("name")
    if not name:
        return line
    if site.get("kind") == "TEST":
        suite, _, case = name.partition(".")
        pattern = re.compile(rf"^\s*TEST(?:_F)?\(\s*{re.escape(suite)}\s*,\s*{re.escape(case)}\s*\)")
    else:  # a top-level definition starts in column 0 in F´ sources: `void Tester::name(...) {`
        pattern = re.compile(rf"^\S.*\b{re.escape(name)}\s*\(")
    if 0 < line <= len(lines) and pattern.search(lines[line - 1]):
        return line
    return next((k + 1 for k, x in enumerate(lines) if pattern.search(x) and _body_start(lines, k + 1) is not None),
                None)


def _read_lines(path: Path) -> list[str]:
    r"""Lines with their endings, split at "\n" only, as git counts them (str.splitlines also splits at \x0c etc.)."""
    text = path.read_bytes().decode("utf-8")
    parts = text.split("\n")
    lines = [x + "\n" for x in parts[:-1]]
    return lines + ([parts[-1]] if parts[-1] else [])


def _unified(before: list[str], after: list[str], file: str) -> str:
    """A unified diff that `git apply` accepts, with the marker for a last line without a newline."""
    out = []
    for line in difflib.unified_diff(before, after, f"a/{file}", f"b/{file}"):
        out.append(line)
        if not line.endswith("\n"):
            out.append("\n\\ No newline at end of file\n")
    return "".join(out)


def _insert(lines: list[str], rid: str, site: dict, line: int) -> list[str] | None:
    """`lines` with REQUIREMENT("ID"); as the first statement of the site's body, or None if the body is not found."""
    start = _definition_line(lines, line, site)
    i = None if start is None else _body_start(lines, start)
    if i is None:
        return None
    ref = rid.split("@")[0]
    eol = "\r\n" if lines[i].endswith("\r\n") else "\n"
    code = lines[i].split("//", 1)[0]
    brace = code.index("{")
    if code[brace + 1:].strip():  # a one-line body `TEST(A, B) { run(); }`: write the macro right after the brace
        out = list(lines)
        out[i] = lines[i][: brace + 1] + f' REQUIREMENT("{ref}");' + lines[i][brace + 1:]
        return out
    nxt = next((x for x in lines[i + 1:] if x.strip()), "    ")
    indent = re.match(r"[ \t]*", nxt).group(0) or "    "
    return lines[: i + 1] + [f'{indent}REQUIREMENT("{ref}");{eol}'] + lines[i + 1:]


def patch_many(repo: Path, edits: list[tuple[str, dict]]) -> str | None:
    """One unified diff (for `git apply`) that writes REQUIREMENT("ID"); as the first statement of each suggested TEST
    body or helper. Edits to the same file are applied one after another to one copy, so they never conflict. The
    person who applies it decides whether each test really checks its requirement."""
    files: dict[str, tuple[list[str], list[str]]] = {}
    for rid, site in edits:
        for at in sorted(site["at"], key=lambda a: not a.split(":")[0].endswith((".cpp", ".cc"))):
            file, line = at.rsplit(":", 1)
            if file not in files:
                if not (repo / file).is_file():
                    continue
                original = _read_lines(repo / file)
                files[file] = (original, list(original))
            original, current = files[file]
            new = _insert(current, rid, site, int(line))
            if new is not None:
                files[file] = (original, new)
                break
    out = []
    for file, (original, current) in sorted(files.items()):
        if current == original:
            continue
        if not any(UNIT_TEST_H in x for x in current):
            eol = "\r\n" if any(x.endswith("\r\n") for x in current) else "\n"
            last_inc = max((k for k, x in enumerate(current) if x.lstrip().startswith("#include")), default=-1)
            current = current[: last_inc + 1] + [f"#include <{UNIT_TEST_H}>{eol}"] + current[last_inc + 1:]
        out.append(_unified(original, current, file))
    return "".join(out) or None


def patch(repo: Path, rid: str, site: dict) -> str | None:
    """The diff for one requirement; see patch_many."""
    return patch_many(repo, [(rid, site)])


def rename_patch(repo: Path, rid: str, drift: list[dict]) -> str | None:
    """A unified diff that renames the near-miss ID in REQUIREMENT("ISF-HTH-003") to the defined one (HTH-003)."""
    ref, out = rid.split("@")[0], []
    cited = {d["cited"] for d in drift}
    for file in sorted({a.rsplit(":", 1)[0] for d in drift for a in d["at"]}):
        path = repo / file
        if not path.is_file():
            continue
        lines = _read_lines(path)
        after = [re.sub(r'REQUIREMENT\(\s*"([^"]+)"\s*\)',
                        lambda m: f'REQUIREMENT("{ref}")' if m.group(1) in cited else m.group(0), x) for x in lines]
        if after != lines:
            out.append(_unified(lines, after, file))
    return "".join(out) or None
