CREATE VECTOR INDEX `entity_embedding` FOR (n:`Entity`) ON (n.`embedding`) OPTIONS {indexConfig: {`vector.default_search_expansion_factor`: 3.0,`vector.dimensions`: 256,`vector.hnsw.ef_construction`: 100,`vector.hnsw.m`: 16,`vector.quantization.type`: 'BINARY',`vector.similarity_function`: 'COSINE'}};
CREATE RANGE INDEX `entity_id` FOR (n:`Entity`) ON (n.`id`);
CREATE FULLTEXT INDEX `entity_text` FOR (n:`Entity`) ON EACH [n.`text`, n.`id`] OPTIONS {indexConfig: {`fulltext.analyzer`: 'standard-no-stop-words',`fulltext.eventually_consistent`: false}};
