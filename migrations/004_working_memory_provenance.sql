-- Keep legacy rows readable. An empty origin is interpreted as unknown.
ALTER TABLE working_memory ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}';
