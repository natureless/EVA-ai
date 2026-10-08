-- Legacy records remain unknown. Do not infer provenance from their contents.
ALTER TABLE world_entities ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}';
