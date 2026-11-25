// Runs the browser explorer's own code (web/js/graph.js) on the vendored Relate WebAssembly build under Node and prints
// its verdicts and impact results as JSON. tests/test_web.py compares them with the Python CLI.
//   node tests/web_parity.mjs
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { initSync, RelateDb } from "../web/vendor/relate-wasm/relate.js";
import { classifyFiles, deploymentParts, impact, loadBundle, poll, verdicts } from "../web/js/graph.js";

const web = join(dirname(fileURLToPath(import.meta.url)), "..", "web");
initSync({ module: readFileSync(join(web, "vendor/relate-wasm/relate_bg.wasm")) });
const bundle = JSON.parse(readFileSync(join(web, "data/graph.json"), "utf8"));
const queries = JSON.parse(readFileSync(join(web, "data/queries.json"), "utf8"));
const db = new RelateDb();
loadBundle(db, bundle);
const rows = verdicts(db, queries, bundle.meta.run?.id ?? null);
const table = poll(rows, deploymentParts(db));

const IMPACT_INPUTS = [
  ["Svc/Health/Health.cpp"], ["Svc/Sched/Sched.fpp"], ["Utils/Types/CircularBuffer.hpp"],
  ["Svc/DpCompressProc/DpCompressProc.fpp"], ["default/config/ComCfg.fpp"], ["README.md"],
];
const impacts = IMPACT_INPUTS.map((files) => {
  const cls = classifyFiles(db, files);
  const res = impact(db, queries, cls.components, cls.port_types, rows, cls.ripple, cls.config.length > 0);
  return { files, components: res.rerun.map((r) => r.component).sort(), test_cases: res.testCases,
    requirements: res.requirements.length, go_pending: res.goPending.length };
});

console.log(JSON.stringify({
  verdicts: Object.fromEntries(rows.map((r) => [r.id, [r.verdict, [...r.reasons].sort()]])),
  poll: Object.fromEntries(table.map((p) => [p.component, [p.verdict, p.GO, p["NO-GO"], p.INCOMPLETE, p.inDeployment]])),
  impacts,
}));
