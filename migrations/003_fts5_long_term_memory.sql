-- EVA v0.1 — FTS5 full-text search on long_term_memory
-- Applied by migration runner after 001_initial_schema.
--
-- Creates a content-sync FTS5 table over long_term_memory.content.
-- No triggers needed — FTS5 reads directly from the source table.
-- This avoids integrity_check failures from INSERT OR REPLACE on
-- external-content FTS tables.

CREATE VIRTUAL TABLE IF NOT EXISTS long_term_memory_fts USING fts5(
    content,
    category,
    content='long_term_memory',
    content_rowid='rowid',
    tokenize='porter unicode61'
);

-- Rebuild index from existing rows (safety net)
INSERT INTO long_term_memory_fts(long_term_memory_fts) VALUES ('rebuild');
