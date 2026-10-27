"""
Schema introspection: tables, columns, constraints, foreign keys, indexes,
and row-count estimates for a given schema. This is the raw structural
picture that later phases (LLM, ML classifier) will consume — no
interpretation happens here, just facts read from Postgres catalogs.
"""

import psycopg2

from app.db import get_readonly_connection, dict_cursor


class SchemaIntrospectionError(Exception):
    """Raised when introspection queries fail against Postgres."""


def _fetch_tables(cur, schema: str) -> list:
    cur.execute(
        """
        SELECT
            c.relname AS table_name,
            GREATEST(c.reltuples, 0)::bigint AS estimated_row_count
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relkind = 'r'
        ORDER BY c.relname
        """,
        (schema,),
    )
    return cur.fetchall()


def _fetch_columns(cur, schema: str, table: str) -> list:
    cur.execute(
        """
        SELECT
            column_name,
            data_type,
            is_nullable = 'YES' AS nullable,
            column_default
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        (schema, table),
    )
    return cur.fetchall()


def _fetch_primary_key(cur, schema: str, table: str) -> list:
    # information_schema.table_constraints / key_column_usage only show rows
    # to roles holding INSERT/UPDATE/DELETE/REFERENCES/TRIGGER on the table
    # (or ownership) — a pure SELECT-only role sees nothing there. pg_catalog
    # has no such filter, so we query it directly.
    cur.execute(
        """
        SELECT a.attname AS column_name
        FROM pg_constraint con
        JOIN pg_class t ON t.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
        WHERE con.contype = 'p' AND n.nspname = %s AND t.relname = %s
        ORDER BY k.ord
        """,
        (schema, table),
    )
    return [row["column_name"] for row in cur.fetchall()]


def _fetch_foreign_keys(cur, schema: str, table: str) -> list:
    # See _fetch_primary_key: pg_catalog is used instead of
    # information_schema so this works for a SELECT-only role.
    cur.execute(
        """
        SELECT
            a.attname AS column_name,
            ft.relname AS references_table,
            fa.attname AS references_column,
            con.conname AS constraint_name
        FROM pg_constraint con
        JOIN pg_class t ON t.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_class ft ON ft.oid = con.confrelid
        JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
        JOIN LATERAL unnest(con.confkey) WITH ORDINALITY AS fk(attnum, ord)
            ON fk.ord = k.ord
        JOIN pg_attribute fa ON fa.attrelid = ft.oid AND fa.attnum = fk.attnum
        WHERE con.contype = 'f' AND n.nspname = %s AND t.relname = %s
        ORDER BY k.ord
        """,
        (schema, table),
    )
    return cur.fetchall()


def _fetch_indexes(cur, schema: str, table: str) -> list:
    cur.execute(
        """
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = %s AND tablename = %s
        ORDER BY indexname
        """,
        (schema, table),
    )
    return cur.fetchall()


def introspect_schema(schema: str = "public") -> dict:
    """Return table/column/constraint/index structure for `schema`."""
    conn = get_readonly_connection()
    try:
        with dict_cursor(conn) as cur:
            try:
                tables = _fetch_tables(cur, schema)
                result_tables = []
                for t in tables:
                    table_name = t["table_name"]
                    result_tables.append(
                        {
                            "table_name": table_name,
                            "estimated_row_count": t["estimated_row_count"],
                            "columns": _fetch_columns(cur, schema, table_name),
                            "primary_key": _fetch_primary_key(cur, schema, table_name),
                            "foreign_keys": _fetch_foreign_keys(cur, schema, table_name),
                            "indexes": _fetch_indexes(cur, schema, table_name),
                        }
                    )
            except psycopg2.Error as exc:
                raise SchemaIntrospectionError(
                    f"Failed to introspect schema '{schema}': {exc}"
                ) from exc
    finally:
        conn.close()

    return {"schema": schema, "tables": result_tables}
