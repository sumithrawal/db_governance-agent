#!/bin/bash
# audit_log itself is created by app/schema.sql (mounted as
# 02_audit_schema.sql). This grants only what the audit-writer role needs:
# INSERT on the table and USAGE on its sequence, nothing else.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    GRANT INSERT ON audit_log TO "${DB_AUDIT_USER}";
    GRANT USAGE, SELECT ON SEQUENCE audit_log_id_seq TO "${DB_AUDIT_USER}";
EOSQL
