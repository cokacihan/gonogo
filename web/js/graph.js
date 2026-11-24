// Load the GoNoGo bundle into an in-memory Relate (WASM) database and run the same Cypher as the CLI.
// No DOM here: this module also runs under Node for tests.

const LABEL_RE = /^[A-Za-z][A-Za-z0-9]*$/; // labels and relationship types come from our own bundle, still checked

export function run(db, cypher, params = null) {
  const res = JSON.parse(db.execute(cypher, params ? JSON.stringify(params) : null));
  if (res.error) throw new Error(`${res.error.code || "Relate error"}: ${res.error.message || JSON.stringify(res.error)}`);
  return res;
}

export function rows(db, cypher, params = null) {
  return run(db, cypher, params).rows;
}

function chunks(list, size) {
  const out = [];
  for (let i = 0; i < list.length; i += size) out.push(list.slice(i, i + size));
  return out;
}

export function loadBundle(db, bundle, onProgress = () => {}) {
  run(db, "CREATE INDEX entity_id IF NOT EXISTS FOR (n:Entity) ON (n.id)");
  const labels = Object.keys(bundle.nodes);
  for (const label of labels) {
    if (!LABEL_RE.test(label)) throw new Error(`unexpected label ${label}`);
    run(db, `CREATE INDEX ${label.toLowerCase()}_id IF NOT EXISTS FOR (n:${label}) ON (n.id)`);
  }
  let done = 0;
  const total = labels.reduce((s, l) => s + bundle.nodes[l].length, 0) +
    Object.values(bundle.edges).reduce((s, e) => s + e.length, 0);
  for (const label of labels) {
    for (const batch of chunks(bundle.nodes[label], 1000)) {
      run(db, `UNWIND $rows AS r CREATE (n:Entity:${label}) SET n = r`, { rows: batch });
      done += batch.length;
      onProgress(done, total);
    }
  }
  for (const [type, list] of Object.entries(bundle.edges)) {
    if (!LABEL_RE.test(type.replace(/_/g, ""))) throw new Error(`unexpected relationship type ${type}`);
    const shaped = list.map(({ src, dst, ...props }) => ({ src, dst, props }));
    for (const batch of chunks(shaped, 1000)) {
      run(db, `UNWIND $rows AS e MATCH (a:Entity {id: e.src}), (b:Entity {id: e.dst}) ` +
        `CREATE (a)-[r:${type}]->(b) SET r = e.props`, { rows: batch });
      done += batch.length;
      onProgress(done, total);
    }
  }
  return total;
}

export function verdicts(db, queries, runId, acceptDeclared = false) {
  return rows(db, queries.verdicts, {
    run: runId, accept_declared: acceptDeclared, test_methods: queries.test_methods,
  });
}

const ORDER = { GO: 0, INCOMPLETE: 1, "NO-GO": 2 };

export function poll(verdictRows, deploymentParts) {
  const by = new Map();
  for (const r of verdictRows) {
    if (!by.has(r.component)) by.set(r.component, []);
    by.get(r.component).push(r);
  }
  return [...by.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([component, rs]) => {
    const count = (v) => rs.filter((r) => r.verdict === v).length;
    const verdict = rs.reduce((w, r) => (ORDER[r.verdict] > ORDER[w] ? r.verdict : w), "GO");
    const reasons = new Map();
    rs.forEach((r) => r.reasons.forEach((x) => reasons.set(x, (reasons.get(x) || 0) + 1)));
    return {
      component, requirements: rs.length, GO: count("GO"), INCOMPLETE: count("INCOMPLETE"), "NO-GO": count("NO-GO"),
      verdict, reasons: [...reasons.entries()].sort((a, b) => b[1] - a[1]).map(([k]) => k),
      inDeployment: deploymentParts.has(component),
    };
  });
}

export function deploymentParts(db) {
  return new Set(rows(db, "MATCH (:Instance)-[:INSTANCE_OF]->(p:Part) RETURN DISTINCT p.id AS id").map((r) => r.id));
}

export function classifyFiles(db, files) {
  const parts = rows(db, "MATCH (p:Part) RETURN p.id AS id").map((r) => r.id);
  const defs = rows(db, "MATCH (t:PortType) WHERE t.defined_in IS NOT NULL RETURN t.id AS id, t.defined_in AS file");
  const comps = new Set(), ports = new Set(), topology = [], unmatched = [];
  for (let f of files) {
    f = f.trim().replace(/\\/g, "/").replace(/^\.\//, "");
    if (!f) continue;
    let hit = false;
    const portHits = defs.filter((d) => d.file === f).map((d) => d.id);
    portHits.forEach((p) => ports.add(p));
    if (portHits.length) hit = true;
    const owner = parts.filter((p) => f === p || f.startsWith(p + "/")).sort((a, b) => b.length - a.length)[0];
    if (owner && !portHits.length) { comps.add(owner); hit = true; }
    if (/(topology|instances)\.fpp$/.test(f) || f.includes("/Top/") || f.includes("Subtopologies/")) { topology.push(f); hit = true; }
    if (!hit) unmatched.push(f);
  }
  return { components: [...comps].sort(), port_types: [...ports].sort(), topology, unmatched };
}

export function impact(db, queries, components, portTypes, verdictRows) {
  const rerun = rows(db, queries.tests_to_rerun, { components, port_types: portTypes });
  const reqs = new Set(rerun.flatMap((r) => r.requirements));
  const pending = verdictRows.filter((v) => reqs.has(v.id) && v.verdict === "GO").map((v) => v.id);
  const integration = rows(db, queries.integration, { components });
  return {
    rerun, testCases: rerun.reduce((s, r) => s + r.test_cases, 0), requirements: [...reqs].sort(),
    goPending: pending, integration,
  };
}
