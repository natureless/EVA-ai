-- NULL denotes a historical stable row. Original IDs, payload and time remain intact.
-- Envelope JSON contains the exact version and provenance for new writes.
ALTER TABLE events ADD COLUMN event_contract TEXT;
