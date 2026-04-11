-- Memory tables for the Personal Assistant.
-- Run once against the pa database.

CREATE TABLE IF NOT EXISTS memory_facts (
    id SERIAL PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,
    value TEXT NOT NULL,
    source TEXT DEFAULT 'user',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS memory_rules (
    id SERIAL PRIMARY KEY,
    rule TEXT UNIQUE NOT NULL,
    reason TEXT DEFAULT '',
    source TEXT DEFAULT 'reflection',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast prefix/keyword lookups.
CREATE INDEX IF NOT EXISTS idx_facts_key ON memory_facts (key);
CREATE INDEX IF NOT EXISTS idx_rules_rule ON memory_rules USING gin (to_tsvector('english', rule));
