"""GoNoGo MCP server (stdio, read-only), dependency-free.

Graph-specific tools for coding agents working on F´ flight software:
  poll            readiness poll per component (GO / NO-GO / INCOMPLETE counts)
  why             verdict of one requirement, with reasons and the citing test cases and their CI status
  tests_to_rerun  unit tests to re-run for changed files / components / port types, and GO verdicts put in question
  suggest         where the REQUIREMENT() line of an uncited requirement would go (candidates, not evidence)
  findings        traceability-hygiene findings (undefined cited IDs, duplicate IDs, cross-component citations)
  ask             rule-based question answering with the Cypher receipt
Relate's own server (`relate serve --mcp stdio --database data/fprime --read-only`) complements this with raw
read-only Cypher (`get_schema`, `read_cypher`).

Run:  python -m gonogo.mcp_server [--store data/fprime]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gonogo import db as store
from gonogo.impact import impact
from gonogo.queries import run as run_tpl
from gonogo.suggest import suggest
from gonogo.verdict import current_run, deployment_parts, poll, verdicts

PROTOCOL = "2025-06-18"

TOOLS = [
    {"name": "poll", "description": "Readiness poll of F´ components: GO / NO-GO / INCOMPLETE requirement counts per "
     "component, from SDD requirements, REQUIREMENT() citations and the loaded CI unit-test run.",
     "inputSchema": {"type": "object", "properties": {"all": {"type": "boolean", "description":
                     "all components instead of only those in the Ref deployment"}}}},
    {"name": "why", "description": "Verdict of one requirement ID (e.g. HTH-001) with reason codes, the citing test "
     "cases of its component and their status in the CI run.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "tests_to_rerun", "description": "Before or after editing F´ code: unit-test cases to re-run for the "
     "changed files (paths relative to the F´ repository), components or port types, the requirements depending on "
     "them, GO verdicts pending re-run, and deployment connections for integration review.",
     "inputSchema": {"type": "object", "properties": {
         "files": {"type": "array", "items": {"type": "string"}},
         "components": {"type": "array", "items": {"type": "string"}},
         "port_types": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "suggest", "description": "For a requirement no own test cites: the test bodies and helpers of its "
     "component that most likely verify it (Relate full-text index over test code, lifted to test cases over the "
     "call graph), a near-miss ID to rename if one exists, and the REQUIREMENT() line to add. Candidates for a "
     "reviewer, never evidence.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "k": {"type": "integer"}},
                     "required": ["id"]}},
    {"name": "findings", "description": "Traceability-hygiene findings: test files citing undefined requirement IDs, "
     "duplicate requirement IDs, requirements cited by tests of another component.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ask", "description": "Ask a question about the F´ traceability graph; the answer includes the Cypher "
     "that produced it, or 'I don't know' when the graph does not cover it.",
     "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
]
for t in TOOLS:
    t["annotations"] = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}


class Server:
    def __init__(self, store_path: Path):
        self.db = store.connect(store_path)
        self.run = current_run(self.db)
        self._rows = None

    @property
    def rows(self):
        if self._rows is None:
            self._rows = verdicts(self.db, self.run)
        return self._rows

    def call(self, name: str, args: dict) -> object:
        if name == "poll":
            table = poll(self.rows, deployment_parts(self.db))
            return {"run": self.run, "components": [p for p in table if args.get("all") or p["in_deployment"]]}
        if name == "why":
            row = next((r for r in self.rows if r["id"] == args["id"]), None)
            return row or {"error": f"I don't know: {args['id']} is not a requirement in the graph."}
        if name == "tests_to_rerun":
            res = impact(self.db, files=args.get("files"), components=args.get("components"),
                         port_types=args.get("port_types"), verdict_rows=self.rows)
            for r in res["rerun"]:
                r["gtest_filter"] = ":".join(sorted(r.pop("filters")))
            return res
        if name == "suggest":
            res = suggest(self.db, args["id"], k=int(args.get("k", 5)))
            return res or {"error": f"I don't know: {args['id']} is not a requirement in the graph."}
        if name == "findings":
            return {t: run_tpl(self.db, t)[0] for t in
                    ("fp_unknown_citations", "fp_duplicate_requirement_ids", "fp_cross_component_citations")}
        if name == "ask":
            from gonogo.agent import Agent
            a = Agent(self.db).ask(args["question"])
            return {k: v for k, v in a.to_dict().items() if k in ("status", "text", "cypher", "params", "answer_ids", "suggestions")}
        raise KeyError(name)

    def handle(self, msg: dict) -> dict | None:
        mid, method = msg.get("id"), msg.get("method")
        if mid is None:  # notification
            return None
        try:
            if method == "initialize":
                result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                          "serverInfo": {"name": "gonogo", "version": "0.3.0"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                p = msg.get("params", {})
                try:
                    out = self.call(p["name"], p.get("arguments") or {})
                    result = {"content": [{"type": "text", "text": json.dumps(out, default=str)}], "isError": False}
                except KeyError as e:
                    result = {"content": [{"type": "text", "text": f"unknown tool or argument: {e}"}], "isError": True}
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
            return {"jsonrpc": "2.0", "id": mid, "result": result}
        except Exception as e:  # never crash the session; report the error to the client
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=str(store.STORE))
    a = ap.parse_args(argv)
    srv = Server(Path(a.store))
    print(f"gonogo MCP: serving {a.store} read-only (run {srv.run})", file=sys.stderr)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = srv.handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
