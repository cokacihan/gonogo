import { readFileSync } from "node:fs";
import { initSync, RelateDb } from "@relate/relate-wasm";
initSync({ module: readFileSync("node_modules/@relate/relate-wasm/relate_bg.wasm") });
const db = new RelateDb();
const script = `CREATE (:Req {id:'R0'})-[:DERIVES]->(:Req {id:'R1'})-[:DERIVES]->(:Req {id:'R2'});`;
console.log(db.executeScript(script, null).slice(0,200));
console.log(db.execute("MATCH p=(a:Req {id:'R0'})-[:DERIVES*1..5]->(b) RETURN b.id AS id, length(p) AS d", null));
console.log(db.execute("UNWIND $rows AS r CREATE (:Doc {id:r.id, emb: vector(r.e, 2, FLOAT32)})", JSON.stringify({rows:[{id:'D1',e:[1,0]},{id:'D2',e:[0,1]}]})).slice(0,200));
console.log(db.execute("MATCH (d:Doc) RETURN d.id AS id, vector.similarity.cosine(d.emb, vector([1,0.1],2,FLOAT32)) AS s ORDER BY s DESC", null));
