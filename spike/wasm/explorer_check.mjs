// Node check: the browser explorer's loader + verdict query give the same results as the Python CLI.
import { readFileSync } from "node:fs";
import { initSync, RelateDb } from "@relate/relate-wasm";
import { loadBundle, verdicts, poll, deploymentParts, impact, classifyFiles } from "../../web/js/graph.js";

initSync({ module: readFileSync("node_modules/@relate/relate-wasm/relate_bg.wasm") });
const bundle = JSON.parse(readFileSync("../../web/data/graph.json", "utf8"));
const queries = JSON.parse(readFileSync("../../web/data/queries.json", "utf8"));
const db = new RelateDb();
let t = performance.now();
const n = loadBundle(db, bundle);
console.log(`loaded ${n} items in ${(performance.now() - t).toFixed(0)} ms`);
t = performance.now();
const vs = verdicts(db, queries, bundle.meta.run.id);
console.log(`verdicts: ${vs.length} rows in ${(performance.now() - t).toFixed(0)} ms`);
const dep = deploymentParts(db);
const p = poll(vs, dep).filter((x) => x.inDeployment);
const sum = (k) => p.reduce((s, x) => s + x[k], 0);
console.log(JSON.stringify({ ref: { requirements: sum("requirements"), GO: sum("GO"), "NO-GO": sum("NO-GO"), INCOMPLETE: sum("INCOMPLETE") } }));
const cls = classifyFiles(db, ["Svc/Sched/Sched.fpp", "Svc/Health/Health.cpp", "README.md"]);
t = performance.now();
const im = impact(db, queries, cls.components, cls.port_types, vs);
console.log(JSON.stringify({ cls, testCases: im.testCases, components: im.rerun.length, ms: Math.round(performance.now() - t) }));
const hth = vs.find((r) => r.id === "HTH-001");
console.log(JSON.stringify({ id: hth.id, verdict: hth.verdict, reasons: hth.reasons }));
// call-graph evidence paths (variable-length + shortestPath) in WASM
const { rows } = await import("../../web/js/graph.js");
t = performance.now();
const ev = rows(db, queries.evidence_paths, { id: "TLC-001", run: bundle.meta.run.id });
console.log(`evidence paths: ${ev.length} in ${(performance.now() - t).toFixed(0)} ms`, JSON.stringify(ev[0]));
const dr = rows(db, queries.drift_path, { test_case: "Svc/Health::Test.NominalTlm", ref: "HTH-001" });
console.log("drift", JSON.stringify(dr));
