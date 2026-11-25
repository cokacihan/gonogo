"""How often does `gonogo suggest` point at the test code where F´ developers actually wrote REQUIREMENT("ID")?

Ground truth: every requirement that the test code of its own component cites today. For each one, the place
where the macro is written (a TEST body or a helper function: the cite site) and the test cases that reach it over
CALLS are the answers. `suggest` never reads CITES edges, and the importer strips string literals and comments
from the indexed identifiers, so the macro itself gives nothing away. One leak remains possible and is measured
rather than assumed away: a TEST_CASE()/COMMENT() description next to a citation could repeat the requirement's
words; the "identifiers only" row excludes those descriptions.

Rows (decided before the first run; nothing was tuned on these numbers):
  full            identifiers + TEST_CASE()/COMMENT() descriptions; cases lifted over the call graph
  identifiers     identifiers only (index test_code_vocab)
  no call graph   a case ranks only by its own TEST body, not by the helpers it calls
  random          expected value of a uniformly random order of the same candidates (exact, not sampled)
Metrics: hit@1, hit@3, hit@5 (an answer within the first k), and mean reciprocal rank (unranked answers count 0).

  python -m eval.suggest_eval            -> eval/SUGGEST_RESULTS.md and eval/suggest_results.json
"""
from __future__ import annotations

import json
import time
from math import comb
from pathlib import Path

from gonogo.db import connect
from gonogo.suggest import query_terms, rank_cases, rank_sites
from gonogo.verdict import CITE_HOPS

OUT = Path(__file__).resolve().parent
KS = (1, 3, 5)
DEPTH = 1000  # rank the whole candidate list, not only the first page

TRUTH_CYPHER = """
MATCH (p:Part)-[:SATISFIES]->(r:Requirement)<-[:CITES]-(site)-[:TESTS]->(p)
WHERE site:TestCase OR site:TestFunction
RETURN r.id AS id, r.text AS text, p.id AS component, collect(DISTINCT site.id) AS sites
ORDER BY id
"""
# the own test cases that reach a cite site: the same pattern as the verdict query
CASES_TRUTH_CYPHER = f"""
MATCH (p:Part)-[:SATISFIES]->(r:Requirement {{id: $id}})<-[:CITES]-(site)-[:TESTS]->(p)
MATCH (tc:TestCase)-[:TESTS]->(p) WHERE EXISTS {{ MATCH (tc)-[:CALLS*0..{CITE_HOPS}]->(site) }}
RETURN collect(DISTINCT tc.id) AS cases
"""
COUNT_CYPHER = """
MATCH (p:Part {id: $component})
RETURN COUNT { MATCH (c:TestCase)-[:TESTS]->(p) } AS n_cases,
       COUNT { MATCH (s)-[:TESTS]->(p) WHERE s:TestCase OR s:TestFunction } AS n_sites
"""


def random_hit(n: int, g: int, k: int) -> float:
    """P(at least one of g answers among the first k of n candidates in random order)."""
    if g == 0 or n == 0:
        return 0.0
    return 1.0 - comb(n - g, k) / comb(n, k) if n - g >= k else 1.0


def random_mrr(n: int, g: int) -> float:
    """Expected 1/rank of the first answer when g of n candidates are answers, random order."""
    if g == 0 or n == 0:
        return 0.0
    # P(first answer at position i) = C(n-i, g-1) / C(n, g)
    return sum(comb(n - i, g - 1) / comb(n, g) / i for i in range(1, n - g + 2))


def first_rank(ranked: list[tuple[str, tuple]], answers: set[str]) -> int | None:
    return next((i for i, (x, _) in enumerate(ranked, 1) if x in answers), None)


def tie_aware(ranked: list[tuple[str, tuple]], answers: set[str]) -> dict:
    """Expected hit@k and reciprocal rank of the first answer when equal scores are ordered at random, so that a tie
    is never broken in favour of the answer (the query itself breaks ties by id). `ranked` is (id, score key) in
    descending order; candidates without any matching word are not ranked and count as misses."""
    first = first_rank(ranked, answers)
    if first is None:
        return {f"hit@{k}": 0.0 for k in KS} | {"rr": 0.0}
    key = ranked[first - 1][1]
    group = [x for x, kk in ranked if kk == key]
    offset = sum(1 for _, kk in ranked if kk > key)  # better-scored candidates; none of them is an answer
    t, a = len(group), sum(1 for x in group if x in answers)
    hits = {f"hit@{k}": random_hit(t, a, k - offset) if k > offset else 0.0 for k in KS}
    rr = sum(comb(t - j, a - 1) / comb(t, a) / (offset + j) for j in range(1, t - a + 2))
    return hits | {"rr": rr}


def summarise(results: list[dict]) -> dict:
    n = len(results)
    out = {f"hit@{k}": sum(r[f"hit@{k}"] for r in results) / n for k in KS}
    out["mrr"] = sum(r["rr"] for r in results) / n
    return out


def main() -> dict:
    db = connect()
    t0 = time.time()
    truth = [r.data() for r in db.execute(TRUTH_CYPHER)]
    for t in truth:
        t["cases"] = db.execute(CASES_TRUTH_CYPHER, {"id": t["id"]}).fetchone()["cases"]
        t.update(db.execute(COUNT_CYPHER, {"component": t["component"]}).fetchone().data())
    rows = {"full": {"sites": [], "cases": []}, "identifiers": {"sites": [], "cases": []},
            "no call graph": {"cases": []}}
    rand = {"sites": {f"hit@{k}": 0.0 for k in KS} | {"mrr": 0.0},
            "cases": {f"hit@{k}": 0.0 for k in KS} | {"mrr": 0.0}}
    per_req = []
    overlap = {k: {"n": 0, "ge60": 0, "ge80": 0} for k in ("answer", "other")}
    for t in truth:
        terms = query_terms(t["text"], t["component"])
        sites, cases = set(t["sites"]), set(t["cases"])
        def by_site(rows_):  # sites rank by BM25 score; cases by best site score, then the sum (as the query does)
            return [(s["site"], (s["score"],)) for s in rows_]

        def by_case(rows_):
            return [(c["test_case"], (c["best"], c["total"])) for c in rows_]
        ranked = {
            ("full", "sites"): by_site(rank_sites(db, t["id"], terms, DEPTH)),
            ("full", "cases"): by_case(rank_cases(db, t["id"], terms, DEPTH)),
            ("identifiers", "sites"): by_site(rank_sites(db, t["id"], terms, DEPTH, index="test_code_vocab")),
            ("identifiers", "cases"): by_case(rank_cases(db, t["id"], terms, DEPTH, index="test_code_vocab")),
            ("no call graph", "cases"): by_case(rank_cases(db, t["id"], terms, DEPTH, lift=False)),
        }
        answers = {"sites": sites, "cases": cases}
        ranks = {key: first_rank(lst, answers[key[1]]) for key, lst in ranked.items()}
        for (row, level), lst in ranked.items():
            rows[row][level].append(tie_aware(lst, answers[level]))
        for level, n, g in (("sites", t["n_sites"], len(sites)), ("cases", t["n_cases"], len(cases))):
            for k in KS:
                rand[level][f"hit@{k}"] += random_hit(n, g, k) / len(truth)
            rand[level]["mrr"] += random_mrr(n, g) / len(truth)
        # leak check: how much of the requirement's wording sits in the descriptions of each candidate site
        for site_id, doc in docs(db, t["component"]).items():
            share = len(set(terms) & doc) / len(terms) if terms else 0.0
            key = "answer" if site_id in sites else "other"
            overlap[key]["n"] += 1
            overlap[key]["ge60"] += share >= 0.6
            overlap[key]["ge80"] += share >= 0.8
        per_req.append({"id": t["id"], "component": t["component"], "candidate_sites": t["n_sites"],
                        "answer_sites": len(sites), "candidate_cases": t["n_cases"], "answer_cases": len(cases),
                        "rank_site": ranks[("full", "sites")], "rank_case": ranks[("full", "cases")]})
    res = {"requirements": len(truth), "components": len({t["component"] for t in truth}),
           "median_candidate_sites": sorted(t["n_sites"] for t in truth)[len(truth) // 2],
           "median_candidate_cases": sorted(t["n_cases"] for t in truth)[len(truth) // 2],
           "seconds": round(time.time() - t0, 1), "description_overlap": overlap,
           "sites": {"full": summarise(rows["full"]["sites"]), "identifiers": summarise(rows["identifiers"]["sites"]),
                     "random": rand["sites"]},
           "cases": {"full": summarise(rows["full"]["cases"]), "identifiers": summarise(rows["identifiers"]["cases"]),
                     "no call graph": summarise(rows["no call graph"]["cases"]), "random": rand["cases"]},
           "per_requirement": per_req}
    (OUT / "suggest_results.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    (OUT / "SUGGEST_RESULTS.md").write_text(markdown(res), encoding="utf-8")
    return res


_DOCS: dict[str, dict[str, set[str]]] = {}


def docs(db, component: str) -> dict[str, set[str]]:
    """Description words (TEST_CASE()/COMMENT()) of every test body and helper of one component."""
    if component not in _DOCS:
        _DOCS[component] = {r["id"]: set((r["doc"] or "").split()) for r in db.execute(
            "MATCH (s)-[:TESTS]->(:Part {id: $c}) WHERE s:TestCase OR s:TestFunction "
            "RETURN s.id AS id, s.vocab_doc AS doc", {"c": component})}
    return _DOCS[component]


def markdown(res: dict) -> str:
    ov = res["description_overlap"]

    def table(level: str) -> list[str]:
        lines = ["| ranking | hit@1 | hit@3 | hit@5 | MRR |", "|---|---:|---:|---:|---:|"]
        for name, m in res[level].items():
            lines.append(f"| {name} | " + " | ".join(f"{m[f'hit@{k}']:.2f}" for k in KS) + f" | {m['mrr']:.2f} |")
        return lines
    out = ["# Where would the REQUIREMENT() line go? (`gonogo suggest`, measured)", "",
           f"{res['requirements']} requirements in {res['components']} F´ components are cited today by test code of "
           f"their own component. For each, `suggest` ranks the component's test code without reading the "
           f"citations, and we look for the place where the macro is actually written. Median candidates per "
           f"requirement: {res['median_candidate_sites']} test bodies and helpers, "
           f"{res['median_candidate_cases']} test cases. Generated by `python -m eval.suggest_eval` "
           f"({res['seconds']} s).", "",
           "## Cite site (the TEST body or helper function that holds the macro)", "", *table("sites"), "",
           "## Test case (a case that reaches the macro over CALLS)", "", *table("cases"), "",
           "`random` is the exact expected value of a uniformly random order of the same candidates. `identifiers` "
           "leaves out the TEST_CASE()/COMMENT() descriptions, which could repeat a requirement's words next to its "
           "citation. `no call graph` ranks a case only by its own TEST body. Candidates with equal scores count "
           "at their expected position under a random order, so a tie is never broken in favour of the answer; "
           "candidates that share no word with the requirement are not ranked and count as misses.", "",
           "## Do the descriptions give the answer away?", "",
           f"Share of a requirement's words found in the TEST_CASE()/COMMENT() descriptions of a candidate: "
           f"in {ov['answer']['ge80']} of {ov['answer']['n']} (requirement, answer site) pairs the descriptions carry "
           f"80 % or more of them, in {ov['answer']['ge60']} pairs 60 % or more (other candidates: "
           f"{ov['other']['ge80']} and {ov['other']['ge60']} of {ov['other']['n']} pairs). In these requirements the "
           f"descriptions say what a test checks rather than repeat the requirement sentence. Elsewhere in F´ they "
           f"sometimes do repeat it (Svc/Health's `faultTlm` writes HTH-003's sentence in a COMMENT next to its "
           f"near-miss citation ISF-HTH-003, outside this measurement), so the `identifiers` row is reported as the "
           f"conservative figure.", ""]
    return "\n".join(out)


if __name__ == "__main__":
    r = main()
    print(json.dumps({k: v for k, v in r.items() if k != "per_requirement"}, indent=1))
