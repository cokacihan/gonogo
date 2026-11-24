import relate
db=relate.connect(":memory:")
for q in ["CREATE INDEX entity_id IF NOT EXISTS FOR (n:Entity) ON (n.id)",
          "CREATE (:Entity:Part {id:'a'})",
          "MATCH (n:Entity {id:'a'}) RETURN labels(n) AS l",
          "SHOW INDEXES YIELD name, type RETURN name, type"]:
    print(q, [r.data() for r in db.execute(q)])
