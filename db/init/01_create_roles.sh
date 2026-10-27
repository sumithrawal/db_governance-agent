#!/bin/bash
# Creates the two application roles with minimal, distinct privileges.
# Runs as part of docker-entrypoint-initdb.d, as the Postgres superuser,
# with DB_READONLY_USER/PASSWORD and DB_AUDIT_USER/PASSWORD passed through
# from docker-compose's environment.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_READONLY_USER}') THEN
            CREATE ROLE "${DB_READONLY_USER}" LOGIN PASSWORD '${DB_READONLY_PASSWORD}';
        END IF;
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_AUDIT_USER}') THEN
            CREATE ROLE "${DB_AUDIT_USER}" LOGIN PASSWORD '${DB_AUDIT_PASSWORD}';
        END IF;
    END
    \$\$;

    GRANT CONNECT ON DATABASE "${POSTGRES_DB}" TO "${DB_READONLY_USER}";
    GRANT CONNECT ON DATABASE "${POSTGRES_DB}" TO "${DB_AUDIT_USER}";
EOSQL
