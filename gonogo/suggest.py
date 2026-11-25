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
    if drift:
        edit = (f'rename {drift[0]["cited"]} to {rid.split("@")[0]} in REQUIREMENT() at '
                + ", ".join(a for d in drift for a in d["at"]))
    elif sites:
        top = sites[0]
        where = "the TEST body" if top["kind"] == "TEST" else f"function {top['name']}"
        edit = f'add REQUIREMENT("{rid.split("@")[0]}"); to {where} ({", ".join(top["at"])}), if it checks this'
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


def patch(repo: Path, rid: str, site: dict) -> str | None:
    """A unified diff (for `git apply`) that writes REQUIREMENT("ID"); as the first statement of the suggested
    TEST body or helper. The person who applies it decides whether that test really checks the requirement."""
    ref = rid.split("@")[0]
    for at in sorted(site["at"], key=lambda a: not a.split(":")[0].endswith((".cpp", ".cc"))):
        file, line = at.rsplit(":", 1)
        path = repo / file
        if not path.is_file():
            continue
        lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)
        i = _body_start(lines, int(line))
        if i is None:
            continue
        eol = "\r\n" if lines[i].endswith("\r\n") else "\n"
        nxt = next((x for x in lines[i + 1:] if x.strip()), "    ")
        indent = re.match(r"[ \t]*", nxt).group(0) or "    "
        after = lines[: i + 1] + [f'{indent}REQUIREMENT("{ref}");{eol}'] + lines[i + 1:]
        if not any(UNIT_TEST_H in x for x in lines):
            last_inc = max((k for k, x in enumerate(after) if x.lstrip().startswith("#include")), default=-1)
            after.insert(last_inc + 1, f"#include <{UNIT_TEST_H}>{eol}")
        return "".join(difflib.unified_diff(lines, after, f"a/{file}", f"b/{file}"))
    return None
