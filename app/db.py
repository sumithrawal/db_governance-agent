"""
Postgres connection management for the DB governance agent.

Two distinct credentials are used on purpose:

- DB_READONLY_*: a role granted SELECT only. Used for every introspection /
  analysis tool. If this credential is ever compromised, it cannot write
  anything, anywhere.
- DB_AUDIT_*: a role granted INSERT only on the audit_log table. Used only
  by app/audit.py. If this credential is ever compromised, it cannot read
  any of the target data it is auditing access to.

Both roles are verified on connect: we don't just trust the .env file, we
check the role's actual grants in pg_catalog / information_schema and refuse
to proceed if they look wrong. This is meant to fail loudly at startup
rather than silently operate with the wrong blast radius.
"""

import os
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# Load .env from the project root regardless of the current working
# directory (e.g. running pytest from a subdirectory still works).
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH if _ENV_PATH.exists() else None)


class DBConfigError(Exception):
    """Required connection configuration is missing or malformed."""


class DBConnectionError(Exception):
    """Could not connect to Postgres with the given credentials."""


class DBPermissionError(Exception):
    """Connected, but the role's grants don't match what we expect."""


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        raise DBConfigError(
            f"Missing required environment variable '{name}'. "
            f"Copy .env.example to .env and fill in real values."
        )
    return value


def _connect(user_env: str, password_env: str) -> "psycopg2.extensions.connection":
    host = _require_env("PGHOST")
    port = _require_env("PGPORT")
    dbname = _require_env("PGDATABASE")
    user = _require_env(user_env)
    password = _require_env(password_env)

    try:
        conn = psycopg2.connect(
            host=host,
            port=port,
            dbname=dbname,
            user=user,
            password=password,
            connect_timeout=5,
        )
    except psycopg2.OperationalError as exc:
        raise DBConnectionError(
            f"Could not connect to Postgres at {host}:{port}/{dbname} "
            f"as role '{user}': {exc}"
        ) from exc

    conn.set_session(readonly=True, autocommit=True)
    return conn


def _non_select_privileges(cur, current_user: str) -> list:
    """Any INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER grant this role
    holds directly on a table in the public schema, keyed by table."""
    cur.execute(
        """
        SELECT table_name, privilege_type
        FROM information_schema.role_table_grants
        WHERE grantee = %s
          AND table_schema = 'public'
          AND privilege_type NOT IN ('SELECT')
        ORDER BY table_name, privilege_type
        """,
        (current_user,),
    )
    return cur.fetchall()


def get_readonly_connection() -> "psycopg2.extensions.connection":
    """Connect with the read-only role and verify it is actually read-only."""
    conn = _connect("DB_READONLY_USER", "DB_READONLY_PASSWORD")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user")
            current_user = cur.fetchone()[0]

            cur.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
                "FROM pg_roles WHERE rolname = %s",
                (current_user,),
            )
            row = cur.fetchone()
            if row is None:
                raise DBPermissionError(
                    f"Could not find role '{current_user}' in pg_roles."
                )
            rolsuper, rolcreatedb, rolcreaterole, rolbypassrls = row
            if rolsuper or rolcreatedb or rolcreaterole or rolbypassrls:
                raise DBPermissionError(
                    f"Read-only role '{current_user}' has elevated attributes "
                    f"(superuser={rolsuper}, createdb={rolcreatedb}, "
                    f"createrole={rolcreaterole}, bypassrls={rolbypassrls}). "
                    f"Expected a plain, unprivileged role."
                )

            bad_grants = _non_select_privileges(cur, current_user)
            if bad_grants:
                details = ", ".join(f"{t}:{p}" for t, p in bad_grants)
                raise DBPermissionError(
                    f"Read-only role '{current_user}' has non-SELECT grants "
                    f"it should not have: {details}"
                )
    except psycopg2.Error as exc:
        conn.close()
        raise DBConnectionError(f"Permission check query failed: {exc}") from exc
    except Exception:
        conn.close()
        raise
    return conn


def get_audit_connection() -> "psycopg2.extensions.connection":
    """Connect with the audit-writer role and verify it can only write
    audit_log and cannot read arbitrary target data."""
    conn = _connect("DB_AUDIT_USER", "DB_AUDIT_PASSWORD")
    # Audit writes need a real transaction, not a read-only session.
    conn.set_session(readonly=False, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user")
            current_user = cur.fetchone()[0]

            cur.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
                "FROM pg_roles WHERE rolname = %s",
                (current_user,),
            )
            row = cur.fetchone()
            if row is None:
                raise DBPermissionError(
                    f"Could not find role '{current_user}' in pg_roles."
                )
            rolsuper, rolcreatedb, rolcreaterole, rolbypassrls = row
            if rolsuper or rolcreatedb or rolcreaterole or rolbypassrls:
                raise DBPermissionError(
                    f"Audit-writer role '{current_user}' has elevated "
                    f"attributes (superuser={rolsuper}, createdb={rolcreatedb}, "
                    f"createrole={rolcreaterole}, bypassrls={rolbypassrls})."
                )

            cur.execute(
                """
                SELECT table_name, privilege_type
                FROM information_schema.role_table_grants
                WHERE grantee = %s AND table_schema = 'public'
                ORDER BY table_name, privilege_type
                """,
                (current_user,),
            )
            grants = cur.fetchall()
            for table_name, privilege_type in grants:
                if table_name != "audit_log":
                    raise DBPermissionError(
                        f"Audit-writer role '{current_user}' has a grant on "
                        f"'{table_name}' ({privilege_type}). It must only "
                        f"have access to audit_log."
                    )
                if privilege_type != "INSERT":
                    raise DBPermissionError(
                        f"Audit-writer role '{current_user}' has a "
                        f"'{privilege_type}' grant on audit_log; it should "
                        f"only have INSERT."
                    )
            if not any(t == "audit_log" for t, _ in grants):
                raise DBPermissionError(
                    f"Audit-writer role '{current_user}' has no INSERT grant "
                    f"on audit_log. Check the init scripts."
                )
    except psycopg2.Error as exc:
        conn.close()
        raise DBConnectionError(f"Permission check query failed: {exc}") from exc
    except Exception:
        conn.close()
        raise
    return conn


def check_health() -> dict:
    """Used by GET /health. Verifies both roles can connect and pass their
    permission checks. Raises rather than returning a degraded status."""
    result = {}
    conn = get_readonly_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        result["readonly_db"] = "connected"
    finally:
        conn.close()

    conn = get_audit_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        result["audit_db"] = "connected"
    finally:
        conn.close()

    return result


def dict_cursor(conn):
    """Convenience wrapper returning a RealDictCursor for JSON-friendly rows."""
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
