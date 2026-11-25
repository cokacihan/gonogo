import init, { RelateDb } from "../vendor/relate-wasm/relate.js";
import { classifyFiles, deploymentParts, impact, loadBundle, poll, rows, run, verdicts } from "./graph.js";

const $ = (sel) => document.querySelector(sel);
const S = { scope: "ref", selected: null };
const FPRIME = "https://github.com/nasa/fprime";

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => Number(n).toLocaleString("en-US");
// requirement text uses Markdown inline code (`Svc::Health`); escape first, then format the backticks
const md = (v) => esc(v).replace(/`([^`]+)`/g, "<code>$1</code>");
const words = (name) => name.replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/_/g, " ");
// let the status line paint before the synchronous load; setTimeout (not requestAnimationFrame) so a tab opened
// in the background still starts
const nextFrame = () => new Promise((r) => setTimeout(r, 30));
const status = (html) => { $("#status").innerHTML = html; };

async function fetchJson(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

async function main() {
  const [, bundle, queries, stats] = await Promise.all([
    init(), fetchJson("data/graph.json"), fetchJson("data/queries.json"), fetchJson("data/stats.json").catch(() => null),
  ]);
  Object.assign(S, { bundle, queries, stats, runId: bundle.meta.run?.id ?? null, sha: bundle.meta.source.commit });
  const nNodes = Object.values(bundle.nodes).reduce((s, l) => s + l.length, 0);
  const nRels = Object.values(bundle.edges).reduce((s, l) => s + l.length, 0);
  status(`Loading ${fmt(nNodes)} nodes and ${fmt(nRels)} relationships into Relate…`);
  await nextFrame();
  S.db = new RelateDb();
  const t0 = performance.now();
  loadBundle(S.db, bundle);
  const t1 = performance.now();
  S.rows = verdicts(S.db, queries, S.runId);
  const t2 = performance.now();
  S.byId = new Map(S.rows.map((r) => [r.id, r]));
  S.table = poll(S.rows, deploymentParts(S.db));
  status(`Relate loaded ${fmt(nNodes)} nodes and ${fmt(nRels)} relationships in ${Math.round(t1 - t0)} ms and decided
    ${fmt(S.rows.length)} verdicts in ${Math.round(t2 - t1)} ms, here in this tab.`);
  const run = bundle.meta.run;
  $("#provenance").innerHTML = `NASA F´ at commit <a href="${FPRIME}/tree/${esc(S.sha)}">${esc(S.sha.slice(0, 10))}</a>,
    unit-test results from <a href="${esc(run?.url)}">CI run ${esc(run?.id?.replace("run:", ""))}</a>`;
  renderBoard(true);
  renderReleases();
  setupScope();
  setupImpact();
  setupQuery();
  openDeepLink();
}

// ?c=Svc/Health&r=HTH-001 opens a component and one requirement (links in the README and the demo use it)
function openDeepLink() {
  const q = new URLSearchParams(location.search);
  const rid = q.get("r");
  const c = q.get("c") || (rid && (S.byId.get(rid) || S.rows.find((x) => x.id.split("@")[0] === rid))?.component);
  if (!c || !S.table.some((p) => p.component === c)) return;
  if (!S.table.find((p) => p.component === c).inDeployment) {
    document.querySelector('.scope button[data-scope="all"]')?.click();
  }
  select(c);
  const r = rid;
  // the full ID (FH-002@Svc/FatalHandler/docs/sdd.md for a duplicated one) or the plain ID, which opens its first row
  const toggles = [...document.querySelectorAll("#detail .req-toggle")];
  const toggle = r && (toggles.find((t) => t.dataset.id === r) || toggles.find((t) => t.dataset.id.split("@")[0] === r));
  if (toggle) {
    toggle.click();
    toggle.scrollIntoView({ block: "center" });
  } else {
    $("#detail").scrollIntoView({ block: "start" });
  }
}

// ---------------------------------------------------------------- board
function visible() {
  return S.table.filter((p) => S.scope === "all" || p.inDeployment);
}

function renderBoard(powerOn = false) {
  const board = $("#board");
  board.classList.toggle("power-on", powerOn);
  board.innerHTML = visible().map((p, i) => {
    const parts = p.component.split("/");
    const name = words(parts[parts.length - 1]);
    const prefix = parts.slice(0, -1).join("/").replace("TestDeploymentsProject/", "");
    const segs = Array.from({ length: p.requirements }, (_, k) =>
      `<i class="${k < p.GO ? "g" : k < p.GO + p["NO-GO"] ? "n" : ""}"></i>`).join("");
    const cls = p.verdict === "GO" ? "all-go" : p["NO-GO"] ? "has-nogo" : "";
    const label = `${p.component}: ${p.GO} of ${p.requirements} requirements GO, ${p["NO-GO"]} NO-GO, ${p.INCOMPLETE} incomplete`;
    return `<button type="button" class="lamp ${cls}" style="--i:${i}" data-c="${esc(p.component)}"
      aria-pressed="${S.selected === p.component}" aria-label="${esc(label)}">
      <span><span class="legend">${esc(name)}</span><br><span class="path">${esc(prefix)}</span></span>
      <span class="segments" aria-hidden="true">${segs}</span>
      <span class="counts" aria-hidden="true">${p.GO}/${p.requirements} GO${p["NO-GO"] ? `, ${p["NO-GO"]} NO-GO` : ""}</span>
    </button>`;
  }).join("");
  board.querySelectorAll(".lamp").forEach((b) => b.addEventListener("click", () => select(b.dataset.c)));
  const v = visible();
  const t = (k) => v.reduce((s, p) => s + p[k], 0);
  const full = v.filter((p) => p.verdict === "GO").length;
  $("#totals").textContent = `${fmt(t("requirements"))} requirements in ${v.length} components: ${fmt(t("GO"))} GO, ` +
    `${fmt(t("NO-GO"))} NO-GO, ${fmt(t("INCOMPLETE"))} incomplete. ${full} component${full === 1 ? "" : "s"} fully GO.`;
}

function setupScope() {
  document.querySelectorAll(".scope button").forEach((b) => b.addEventListener("click", () => {
    S.scope = b.dataset.scope;
    document.querySelectorAll(".scope button").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
    renderBoard();
  }));
}

// ---------------------------------------------------------------- detail
function select(component) {
  S.selected = component;
  document.querySelectorAll(".lamp").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.c === component)));
  const reqs = S.rows.filter((r) => r.component === component);
  const p = S.table.find((x) => x.component === component);
  const el = $("#detail");
  el.innerHTML = `<h2 id="detail-title">${esc(component)}</h2>
    <p class="summary">${p.GO} of ${p.requirements} requirements GO, ${p["NO-GO"]} NO-GO, ${p.INCOMPLETE} incomplete.
    Source: <a href="${FPRIME}/tree/${esc(S.sha)}/${esc(component)}">${esc(component)} on GitHub</a>.</p>
    <ul class="reqs">${reqs.map((r, i) => reqItem(r, i)).join("")}</ul>`;
  el.querySelectorAll(".req-toggle").forEach((b, i) => b.addEventListener("click", () => {
    const open = b.getAttribute("aria-expanded") === "true";
    b.setAttribute("aria-expanded", String(!open));
    document.getElementById(b.getAttribute("aria-controls")).hidden = open;
    if (!open && !b.dataset.paths) { b.dataset.paths = "1"; fillPaths(reqs[i], i); }
  }));
  el.querySelectorAll("[data-load-query]").forEach((b) => b.addEventListener("click", () => {
    loadQuery(b.dataset.loadQuery);
    $("#cypher").scrollIntoView({ behavior: "smooth", block: "center" });
  }));
  if (window.matchMedia("(max-width: 1099px)").matches) el.scrollIntoView({ behavior: "smooth", block: "start" });
}

function testList(items, cls, chains = {}) {
  if (!items.length) return "none";
  return `<ul>${items.map((t) => {
    const i = t.lastIndexOf(" ");
    const [id, st] = i > 0 ? [t.slice(0, i), t.slice(i + 1)] : [t, ""];
    const stCls = st === "PASSED" ? "passed" : st === "FAILED" ? "failed" : "other";
    const chain = chains[id.trim()];
    return `<li><code>${esc(id.split("::")[1] || id)}</code> <span class="${cls || stCls}">${esc(st.toLowerCase().replace("_", " "))}</span>
      ${chain ? `<br><span class="chain">${chain}</span>` : ""}</li>`;
  }).join("")}</ul>`;
}

// The evidence for one requirement as graph paths: TEST -> helper calls -> REQUIREMENT("ID"), asked on demand.
function fillPaths(r, i) {
  const ref = r.id.split("@")[0];
  const fmtChain = (names, cited) => names.map(esc).join(" → ") + ` → <code>REQUIREMENT("${esc(cited)}")</code>`;
  const chains = {};
  for (const p of rows(S.db, S.queries.evidence_paths, { id: r.id, run: S.runId })) chains[p.test_case] = fmtChain(p.chain, ref);
  for (const t of r.drift_tests) {
    const d = rows(S.db, S.queries.drift_path, { test_case: t, ref })[0];
    if (d) chains[t] = fmtChain(d.chain, d.cited);
  }
  const put = (sel, html) => { const n = document.getElementById(sel); if (n) n.innerHTML = html; };
  put(`own-${i}`, testList(r.own_tests, null, chains));
  if (r.other_tests.length) put(`other-${i}`, testList(r.other_tests, null, chains));
  if (r.drift_tests.length) put(`drift-${i}`, testList(r.drift_tests.map((t) => t + " "), "other", chains));
}

function reqItem(r, i) {
  const [path, line] = (r.source || "").split("#L");
  const reasons = r.reasons.length
    ? `<ul>${r.reasons.map((x) => `<li><span class="tag">${esc(x)}</span>${esc(S.queries.reason_text[x] || "")}</li>`).join("")}</ul>`
    : "every citing test case of this component passed";
  const q = `MATCH (p:Part)-[:SATISFIES]->(r:Requirement {id: '${r.id.replace(/'/g, "\\'")}'})\n` +
    "MATCH (r)<-[:CITES]-(site)-[:TESTS]->(owner:Part)\nMATCH (tc:TestCase)-[:TESTS]->(owner)\n" +
    "MATCH path = shortestPath((tc)-[:CALLS*0..8]->(site))\nOPTIONAL MATCH (tc)-[x:RAN_IN]->(:TestRun)\n" +
    "RETURN owner.id AS test_component, tc.gtest_filter AS test_case, x.status AS status,\n" +
    "       [n IN nodes(path) | n.name] AS call_chain";
  return `<li>
    <button type="button" class="req-toggle" aria-expanded="false" aria-controls="req-${i}" data-id="${esc(r.id)}">
      <span class="dot ${esc(r.verdict)}" role="img" aria-label="${esc(r.verdict)}"></span>
      <span class="req-id">${esc(r.id.split("@")[0])}</span>
      <span class="req-text">${md(r.text)}</span>
    </button>
    <div class="req-body" id="req-${i}" hidden>
      <dl>
        <dt>Verdict</dt><dd><strong>${esc(r.verdict)}</strong></dd>
        <dt>Why</dt><dd>${reasons}</dd>
        <dt>Declared method</dt><dd>${esc(r.methods.join(", ") || "none")}</dd>
        <dt>Tests of this component</dt><dd id="own-${i}">${testList(r.own_tests)}</dd>
        ${r.other_tests.length ? `<dt>Tests of other components</dt><dd id="other-${i}">${testList(r.other_tests)}</dd>` : ""}
        ${r.drift_tests.length ? `<dt>Tests citing a near-miss ID</dt><dd id="drift-${i}">${testList(r.drift_tests.map((t) => t + " "), "other")}</dd>` : ""}
        <dt>Defined in</dt><dd><a href="${FPRIME}/blob/${esc(S.sha)}/${esc(path)}#L${esc(line)}">${esc(path)}, line ${esc(line)}</a></dd>
      </dl>
      <details class="receipt"><summary>Receipt: the Cypher that decided this verdict</summary>
        <pre>${esc(S.queries.verdicts)}</pre>
        <p class="meta">Parameters: run = ${esc(S.runId)}, accept_declared = false. The same query runs in the
        Python CLI (<code>python -m gonogo why ${esc(r.id)}</code>).</p>
      </details>
      <p><button type="button" class="quiet" data-load-query="${esc(q)}">Trace the call paths in the query box</button></p>
    </div>
  </li>`;
}

// ---------------------------------------------------------------- impact
function setupImpact() {
  $("#impact-form").addEventListener("submit", (e) => { e.preventDefault(); renderImpact(); });
  document.querySelectorAll("[data-example]").forEach((b) => b.addEventListener("click", () => {
    $("#files").value = b.dataset.example;
    renderImpact();
  }));
}

function renderImpact() {
  const files = $("#files").value.split(/\r?\n/);
  const cls = classifyFiles(S.db, files);
  const out = $("#impact-out");
  const docsNote = cls.docs.length ? `<p class="meta">Documentation changed, no unit tests to re-run (the CI gate
    re-checks the requirements it defines): ${cls.docs.map((f) => `<code>${esc(f)}</code>`).join(", ")}</p>` : "";
  if (!cls.components.length && !cls.port_types.length && !cls.ripple.length && !cls.config.length) {
    out.innerHTML = (cls.docs.length ? docsNote : `<p class="error">None of these paths is inside an F´ component or
      library, or defines a port type. Use paths from the repository root, such as
      <code>Svc/Health/Health.cpp</code>.</p>`) +
      (cls.topology.length ? `<p>Topology files changed: ${cls.topology.map((f) => `<code>${esc(f)}</code>`).join(", ")}.
      Review the deployment connections; no unit tests change.</p>` : "");
    return;
  }
  const res = impact(S.db, S.queries, cls.components, cls.port_types, S.rows, cls.ripple, cls.config.length > 0);
  const SHOWN = 25; // a header in Fw reaches most of the repository: show the first rows, keep the rest folded
  const rowHtml = res.rerun.map((r) => {
    const why = r.changed_directly ? "changed"
      : r.via_ports.length ? `uses ${r.via_ports.map((v) => v.replace("port:", "")).join(", ")}`
      : r.include_chain.length ? `includes ${r.include_chain.slice(1).join(" → ")}`
      : "FPP configuration changed (visible to every component)";
    const filter = [...r.filters].sort().join(":");
    return `<tr><td><code>${esc(r.component)}</code></td><td>${esc(why)}</td><td>${fmt(r.test_cases)}</td>
      <td>${r.test_cases ? `<button type="button" class="quiet" data-copy="${esc(filter)}">Copy gtest filter</button>` : "no unit tests"}</td></tr>`;
  });
  const head = "<thead><tr><th>Component</th><th>Why</th><th>Test cases</th><th></th></tr></thead>";
  const table = `<table>${head}<tbody>${rowHtml.slice(0, SHOWN).join("")}</tbody></table>` +
    (rowHtml.length > SHOWN ? `<details><summary>Show the other ${rowHtml.length - SHOWN} components</summary>
      <table>${head}<tbody>${rowHtml.slice(SHOWN).join("")}</tbody></table></details>` : "");
  out.innerHTML = `<p><span class="big">${fmt(res.testCases)}</span> unit-test cases to re-run in ${res.rerun.length}
    component${res.rerun.length === 1 ? "" : "s"}. ${fmt(res.requirements.length)} requirements depend on them;
    ${fmt(res.goPending.length)} are GO today and stay pending until the re-run.</p>
    ${docsNote}
    ${cls.unmatched.length ? `<p class="meta">Not mapped: ${cls.unmatched.map(esc).join(", ")}</p>` : ""}
    ${table}
    ${res.integration.length ? `<h3>Integration review</h3><p>${res.integration.length} deployment connections touch the
      changed components. They do not change which unit tests break.</p>
      <table><thead><tr><th>Instance</th><th>Direction</th><th>Peer</th><th>Ports</th></tr></thead><tbody>
      ${res.integration.slice(0, 30).map((c) => `<tr><td><code>${esc(c.instance.replace("Ref:", ""))}</code></td>
        <td>${esc(c.direction)}</td><td><code>${esc(c.peer.replace("Ref:", ""))}</code></td>
        <td><code>${esc(c.src_port)} → ${esc(c.dst_port)}</code></td></tr>`).join("")}</tbody></table>` : ""}`;
  out.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(`--gtest_filter=${b.dataset.copy}`); b.textContent = "Copied"; }
    catch { b.textContent = "Copy failed: select it from the query box"; }
  }));
}

// ---------------------------------------------------------------- releases
function renderReleases() {
  const fig = $("#release-chart");
  const rel = S.stats?.releases;
  if (!rel?.length) { fig.innerHTML = "<p>No release data in this build.</p>"; return; }
  const W = 760, H = 300, L = 56, R = 56, T = 24, B = 54;
  const maxReq = Math.max(...rel.map((r) => r.requirements));
  const scaleY = (v) => T + (H - T - B) * (1 - v / maxReq);
  const scaleP = (v) => T + (H - T - B) * (1 - v / 50);
  const step = (W - L - R) / rel.length;
  const bars = rel.map((r, i) => {
    const x = L + i * step + step * 0.22, w = step * 0.56, y = scaleY(r.requirements);
    return `<rect x="${x}" y="${y}" width="${w}" height="${H - B - y}" rx="3" fill="#c9d3dc"/>
      <text x="${x + w / 2}" y="${y - 6}" text-anchor="middle" class="v">${fmt(r.requirements)}</text>
      <text x="${x + w / 2}" y="${H - B + 20}" text-anchor="middle" class="t">${esc(r.tag)}</text>
      <text x="${x + w / 2}" y="${H - B + 38}" text-anchor="middle" class="d">${esc(r.date)}</text>`;
  }).join("");
  const pts = rel.map((r, i) => [L + i * step + step / 2, scaleP(r.cited_percent)]);
  const line = `<polyline fill="none" stroke="#39b66a" stroke-width="3" points="${pts.map((p) => p.join(",")).join(" ")}"/>` +
    pts.map(([x, y], i) => `<circle cx="${x}" cy="${y}" r="5" fill="#39b66a"/>
      <text x="${x + 9}" y="${y - 8}" class="p">${rel[i].cited_percent}%</text>`).join("");
  fig.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-labelledby="rel-t rel-d">
    <title id="rel-t">Requirements and cited share across F´ releases</title>
    <desc id="rel-d">${rel.map((r) => `${r.tag}: ${r.requirements} requirements, ${r.cited_percent}% cited by own tests`).join("; ")}</desc>
    <style>.v{font:600 13px var(--display);fill:#4a5560}.t{font:600 14px var(--display);fill:#1d232a}
      .d{font:12px var(--body);fill:#4a5560}.p{font:700 13px var(--display);fill:#1f7a45}</style>
    <line x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}" stroke="#aab3bc"/>
    ${bars}${line}</svg>
    <figcaption>Requirements grew ${fmt(rel[0].requirements)} → ${fmt(rel[rel.length - 1].requirements)}; the share
    cited by a test of their own component went ${rel[0].cited_percent}% → ${rel[rel.length - 1].cited_percent}%.
    Computed by <code>python -m gonogo.stats</code> from one Relate store per release.</figcaption>`;
}

// ---------------------------------------------------------------- query box
const EXAMPLES = [
  ["Call chains from TlmChan test cases to REQUIREMENT(\"TLC-001\")",
    "MATCH (r:Requirement {id: 'TLC-001'})<-[:CITES]-(site)-[:TESTS]->(p:Part)\nMATCH (tc:TestCase)-[:TESTS]->(p)\nMATCH path = shortestPath((tc)-[:CALLS*0..8]->(site))\nRETURN tc.gtest_filter AS test_case, [n IN nodes(path) | n.name] AS call_chain, length(path) AS calls"],
  ["Requirements of Svc/Health and the test cases citing them",
    "MATCH (p:Part {id: 'Svc/Health'})-[:SATISFIES]->(r:Requirement)\nOPTIONAL MATCH (r)<-[:CITES]-(site)-[:TESTS]->(p)\nOPTIONAL MATCH (tc:TestCase)-[:TESTS]->(p)\n  WHERE site IS NOT NULL AND EXISTS { MATCH (tc)-[:CALLS*0..8]->(site) }\nRETURN r.id AS requirement, collect(DISTINCT tc.gtest_filter) AS citing_test_cases ORDER BY requirement"],
  ["Test code that cites IDs no SDD defines",
    "MATCH (site)-[:TESTS]->(p:Part)\nWHERE (site:TestCase OR site:TestFunction) AND size(site.unknown_citations) > 0\nRETURN p.id AS component, site.name AS written_in, site.unknown_citations AS undefined_ids ORDER BY component"],
  ["Components using the Svc.Sched port type",
    "MATCH (p:Part)-[u:USES_PORT]->(:PortType {id: 'port:Svc.Sched'})\nRETURN p.id AS component, u.port AS port, u.direction AS direction ORDER BY component"],
  ["Requirements cited by tests of another component",
    "MATCH (owner:Part)-[:SATISFIES]->(r:Requirement)<-[:CITES]-(site)-[:TESTS]->(by:Part)\nWHERE owner <> by\nMATCH (tc:TestCase)-[:TESTS]->(by) WHERE EXISTS { MATCH (tc)-[:CALLS*0..8]->(site) }\nRETURN r.id AS requirement, owner.id AS owner, by.id AS cited_by, count(DISTINCT tc) AS test_cases ORDER BY requirement"],
  ["Deepest call chains behind a REQUIREMENT() in the CI run",
    "MATCH (r:Requirement)<-[:CITES]-(site)-[:TESTS]->(p:Part)\nMATCH (tc:TestCase)-[:TESTS]->(p)\nMATCH path = shortestPath((tc)-[:CALLS*0..8]->(site))\nMATCH (tc)-[x:RAN_IN]->(:TestRun)\nRETURN r.id AS requirement, tc.gtest_filter AS test_case, x.status AS status, length(path) AS calls\nORDER BY calls DESC, requirement LIMIT 10"],
  ["Who the rate group driver feeds in the Ref deployment",
    "MATCH (:Instance {id: 'Ref:rateGroupDriverComp'})-[c:CONNECTS_TO]->(o:Instance)-[:INSTANCE_OF]->(p:Part)\nRETURN o.name AS instance, p.id AS component, c.src_port + ' -> ' + c.dst_port AS ports"],
  ["The verdict query itself", null],
];

function setupQuery() {
  const sel = $("#examples");
  sel.innerHTML = EXAMPLES.map(([t], i) => `<option value="${i}">${esc(t)}</option>`).join("");
  sel.addEventListener("change", () => loadQuery(EXAMPLES[+sel.value][1] ?? S.queries.verdicts));
  loadQuery(EXAMPLES[0][1]);
  $("#query-form").addEventListener("submit", (e) => { e.preventDefault(); runQuery(); });
}

function loadQuery(text) {
  $("#cypher").value = text;
}

function runQuery() {
  const text = $("#cypher").value;
  const known = { run: S.runId, accept_declared: false, test_methods: S.queries.test_methods, components: [], port_types: [] };
  const params = {};
  for (const m of text.matchAll(/\$([A-Za-z_]\w*)/g)) if (m[1] in known) params[m[1]] = known[m[1]];
  const out = $("#query-out");
  try {
    const res = run(S.db, text, Object.keys(params).length ? params : null);
    $("#query-meta").textContent = `${res.rows.length} row${res.rows.length === 1 ? "" : "s"} in ${res.elapsed_ms.toFixed(1)} ms`;
    if (!res.rows.length) { out.innerHTML = "<p>The query ran and returned no rows.</p>"; return; }
    const cols = res.columns;
    const cell = (v) => typeof v === "object" && v !== null ? `<code>${esc(JSON.stringify(v))}</code>` : esc(v);
    out.innerHTML = `<table><thead><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>
      ${res.rows.slice(0, 200).map((r) => `<tr>${cols.map((c) => `<td>${cell(r[c])}</td>`).join("")}</tr>`).join("")}
      </tbody></table>${res.rows.length > 200 ? `<p class="meta">Showing the first 200 of ${res.rows.length} rows.</p>` : ""}`;
  } catch (err) {
    $("#query-meta").textContent = "";
    out.innerHTML = `<p class="error">Relate rejected the query: ${esc(err.message)}</p>`;
  }
}

main().catch((err) => {
  status(`<span class="error">The explorer could not start: ${esc(err.message)}. It needs a browser with WebAssembly
    and must be served over HTTP (for example <code>python -m http.server</code> in the <code>web</code> folder).</span>`);
  console.error(err);
});
