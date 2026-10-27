-- Audit log table. Safe to run repeatedly (idempotent).
-- This file is the single source of truth for the audit_log schema; it is
-- mounted verbatim into the docker-compose Postgres init sequence.

CREATE TABLE IF NOT EXISTS audit_log (
    id                  BIGSERIAL PRIMARY KEY,
    ts                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    tool_name           TEXT NOT NULL,
    parameters          JSONB NOT NULL DEFAULT '{}'::jsonb,
    row_count_returned  INTEGER,
    caller              TEXT NOT NULL DEFAULT 'local_user',
    error               TEXT
);

CREATE INDEX IF NOT EXISTS audit_log_ts_idx ON audit_log (ts);
CREATE INDEX IF NOT EXISTS audit_log_tool_name_idx ON audit_log (tool_name);
