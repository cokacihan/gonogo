"""Relate spike: single-file, write/read, var-length path, vector, aggregations."""
import os, shutil, time, relate
P = "spike/tmp_spike.rl"
for p in (P,):
    if os.path.exists(p): os.remove(p)
def run(db, q, params=None, label=""):
    try:
        t=time.perf_counter(); rows=[r.data() for r in db.execute(q, params or {})]
        print(f"OK   {label}: {rows[:5]} ({(time.perf_counter()-t)*1e3:.1f} ms)")
        return rows
    except Exception as e:
        print(f"FAIL {label}: {type(e).__name__}: {str(e)[:200]}")
db = relate.connect(P)
run(db, "UNWIND $rows AS r CREATE (:Req {id:r.id, txt:r.t})", {"rows":[{"id":f"R{i}","t":f"req {i}"} for i in range(6)]}, "unwind create")
run(db, "MATCH (a:Req),(b:Req) WHERE toInteger(substring(b.id,1)) = toInteger(substring(a.id,1))+1 CREATE (a)-[:DERIVES]->(b)", label="chain")
run(db, "MATCH p=(a:Req {id:'R0'})-[:DERIVES*1..5]->(b) RETURN b.id AS id, length(p) AS d ORDER BY d", label="varlen")
run(db, "MATCH (a:Req {id:'R0'})<-[:DERIVES*0..]-(b) RETURN count(b) AS n", label="varlen reverse 0..")
run(db, "MATCH p=shortestPath((a:Req {id:'R0'})-[*]-(b:Req {id:'R5'})) RETURN length(p) AS l", label="shortestPath")
run(db, "MATCH (a:Req) OPTIONAL MATCH (a)-[:DERIVES]->(b) RETURN a.id AS a, b.id AS b ORDER BY a", label="optional")
run(db, "MATCH (a:Req) WHERE NOT (a)-[:DERIVES]->() RETURN a.id AS leaf", label="pattern pred")
run(db, "MATCH (a:Req) WHERE NOT EXISTS { MATCH (a)<-[:DERIVES]-() } RETURN a.id AS root", label="exists subquery")
run(db, "MATCH (a:Req) RETURN collect(a.id)[0..3] AS c, [x IN collect(a.id) WHERE x<>'R0' | x+'!'][0..2] AS lc", label="list comp")
run(db, "MATCH (a:Req) CALL { WITH a MATCH (a)-[:DERIVES]->(b) RETURN count(b) AS k } RETURN a.id AS id, k", label="CALL subquery")
run(db, "MATCH (a:Req {id:'R0'}) SET a.emb = vector([0.1,0.2,0.3], 3, FLOAT32) RETURN a.id AS id", label="vector prop")
run(db, "MATCH (a:Req) WHERE a.id <> 'R0' SET a.emb = vector([rand(),rand(),rand()], 3, FLOAT32)", label="vector rand")
run(db, "CREATE VECTOR INDEX req_emb IF NOT EXISTS FOR (n:Req) ON n.emb OPTIONS {indexConfig: {`vector.dimensions`: 3, `vector.similarity_function`: 'cosine'}}", label="create vector index")
run(db, "CALL db.index.vector.queryNodes('req_emb', 3, vector([0.1,0.2,0.3],3,FLOAT32)) YIELD node, score RETURN node.id AS id, score", label="queryNodes")
run(db, "MATCH (a:Req) RETURN a.id AS id, vector.similarity.cosine(a.emb, vector([0.1,0.2,0.3],3,FLOAT32)) AS s ORDER BY s DESC LIMIT 3", label="similarity fn")
run(db, "MATCH (a:Req) RETURN a.id AS id, vector_distance(a.emb, vector([0.1,0.2,0.3],3,FLOAT32), COSINE) AS d ORDER BY d LIMIT 3", label="vector_distance")
run(db, "MATCH (n:Req) SEARCH n IN (VECTOR INDEX req_emb FOR vector([0.1,0.2,0.3],3,FLOAT32) LIMIT 3) SCORE AS s RETURN n.id AS id, s", label="SEARCH clause")
run(db, "CREATE FULLTEXT INDEX req_txt IF NOT EXISTS FOR (n:Req) ON EACH [n.txt]", label="fulltext idx")
run(db, "CALL db.index.fulltext.queryNodes('req_txt', 'req') YIELD node, score RETURN node.id AS id, score LIMIT 3", label="fulltext query")
run(db, "SHOW INDEXES", label="show indexes")
run(db, "CALL db.labels()", label="db.labels")
run(db, "CALL db.schema.visualization()", label="schema viz")
db.close()
db = relate.connect(P, read_only=True)
run(db, "MATCH (n) RETURN count(n) AS n", label="reopen ro")
run(db, "CREATE (:X)", label="ro write refused?")
db.close()
print("files:", os.listdir("spike"), os.path.getsize(P))
