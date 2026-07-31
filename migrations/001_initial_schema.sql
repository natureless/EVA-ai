-- EVA v0.1 initial schema
-- Applied by migration runner on first boot.

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY, type TEXT NOT NULL, source TEXT NOT NULL,
    timestamp TEXT NOT NULL, payload TEXT NOT NULL,
    correlation_id TEXT, status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS episodic_memory (
    id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL, summary TEXT NOT NULL,
    payload TEXT NOT NULL, importance REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS traces (
    id TEXT PRIMARY KEY, loop_id TEXT NOT NULL,
    timestamp TEXT NOT NULL, event_type TEXT NOT NULL,
    decision TEXT NOT NULL, agent TEXT NOT NULL,
    result_summary TEXT NOT NULL, duration_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS persona_profiles (
    persona_id TEXT PRIMARY KEY, name TEXT NOT NULL,
    role_definition TEXT NOT NULL, tone_style TEXT NOT NULL,
    hard_constraints TEXT NOT NULL, soft_preferences TEXT NOT NULL,
    value_weights TEXT NOT NULL, version INTEGER NOT NULL,
    updated_at TEXT NOT NULL, confidence REAL NOT NULL,
    source_event_id TEXT
);

CREATE TABLE IF NOT EXISTS memory_items (
    id TEXT PRIMARY KEY, memory_type TEXT NOT NULL,
    content TEXT NOT NULL, source_event_id TEXT,
    salience REAL NOT NULL DEFAULT 0.0,
    confidence REAL NOT NULL DEFAULT 0.5, ttl_seconds INTEGER,
    embedding_ref TEXT, summary_ref TEXT,
    conflict_keys_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    last_accessed_at TEXT, status TEXT NOT NULL DEFAULT 'active',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_memories_type_status
    ON memory_items(memory_type, status);
CREATE INDEX IF NOT EXISTS idx_memories_salience
    ON memory_items(salience);
CREATE INDEX IF NOT EXISTS idx_memories_source_event
    ON memory_items(source_event_id);

-- S2: Working Memory
CREATE TABLE IF NOT EXISTS working_memory (
    id TEXT PRIMARY KEY, content TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
    priority INTEGER NOT NULL DEFAULT 2,
    tags_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wm_expires ON working_memory(expires_at);
CREATE INDEX IF NOT EXISTS idx_wm_created ON working_memory(created_at);

-- S3: Long-term Memory
CREATE TABLE IF NOT EXISTS long_term_memory (
    id TEXT PRIMARY KEY, content TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    embedding_ref TEXT NOT NULL DEFAULT '',
    importance REAL NOT NULL DEFAULT 0.5,
    source_event_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ltm_category_status ON long_term_memory(category, status);
CREATE INDEX IF NOT EXISTS idx_ltm_importance ON long_term_memory(importance);
CREATE INDEX IF NOT EXISTS idx_ltm_content ON long_term_memory(content COLLATE NOCASE);

-- S4: World Model
CREATE TABLE IF NOT EXISTS world_entities (
    id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL,
    properties_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_we_type ON world_entities(type);

CREATE TABLE IF NOT EXISTS world_edges (
    source TEXT NOT NULL, target TEXT NOT NULL,
    relation TEXT NOT NULL, weight REAL NOT NULL DEFAULT 1.0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (source, target, relation)
);

-- Executor Audit
CREATE TABLE IF NOT EXISTS executor_audit (
    id TEXT PRIMARY KEY, executor_type TEXT NOT NULL,
    action TEXT NOT NULL, task_id TEXT NOT NULL,
    token_id TEXT NOT NULL DEFAULT '',
    parameters_json TEXT NOT NULL DEFAULT '{}',
    result_summary TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL, timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_type_status ON executor_audit(executor_type, status);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON executor_audit(timestamp);
