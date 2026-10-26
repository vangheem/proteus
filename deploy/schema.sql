CREATE TABLE IF NOT EXISTS component_versions (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    source JSONB NOT NULL,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_hash TEXT NOT NULL,
    image_ref TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name, version),
    UNIQUE (name, source_hash)
);

CREATE TABLE IF NOT EXISTS components (
    name TEXT PRIMARY KEY,
    current_version INTEGER,
    status TEXT NOT NULL,
    route_prefix TEXT NOT NULL,
    endpoint TEXT,
    runtime_id TEXT,
    image_ref TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kv_entries (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kv_tags (
    key TEXT NOT NULL REFERENCES kv_entries(key) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (key, tag)
);

CREATE INDEX IF NOT EXISTS kv_tags_tag_idx ON kv_tags (tag);

CREATE TABLE IF NOT EXISTS coordinator_events (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    detail JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS global_styles (
    component TEXT PRIMARY KEY,
    css TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    prompt TEXT NOT NULL,
    status TEXT NOT NULL,
    component TEXT,
    detail JSONB,
    kind TEXT NOT NULL DEFAULT 'mutation',
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'mutation';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS error TEXT;
