#!/bin/bash
# Runs after seed.sql (04) has created the business tables. Grants SELECT
# only, on every table in public, to the read-only role — explicitly
# excluding audit_log, which the read-only role has no business reading.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    GRANT USAGE ON SCHEMA public TO "${DB_READONLY_USER}";
    DO \$\$
    DECLARE
        tbl RECORD;
    BEGIN
        FOR tbl IN
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public' AND tablename != 'audit_log'
        LOOP
            EXECUTE format('GRANT SELECT ON %I TO %I', tbl.tablename, '${DB_READONLY_USER}');
        END LOOP;
    END
    \$\$;
EOSQL
