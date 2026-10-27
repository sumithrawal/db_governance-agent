"""
Storage stats: table size, index size, and total size per table, sorted
descending, plus a signal (not a verdict) when index size looks large
relative to table size.
"""

import psycopg2

from app.db import get_readonly_connection, dict_cursor


class StorageStatsError(Exception):
    """Raised when the storage-stats queries fail."""


def _fetch_sizes(cur, schema: str) -> list:
    cur.execute(
        """
        SELECT
            c.relname AS table_name,
            pg_relation_size(c.oid) AS table_size_bytes,
            pg_indexes_size(c.oid) AS index_size_bytes,
            pg_total_relation_size(c.oid) AS total_size_bytes,
            pg_size_pretty(pg_relation_size(c.oid)) AS table_size_pretty,
            pg_size_pretty(pg_indexes_size(c.oid)) AS index_size_pretty,
            pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size_pretty
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relkind = 'r'
        ORDER BY pg_total_relation_size(c.oid) DESC
        """,
        (schema,),
    )
    return cur.fetchall()


def get_storage_stats(schema: str = "public", ratio_threshold: float = 1.0) -> dict:
    """
    Return per-table size breakdown sorted descending by total size. A table
    is flagged (not condemned) when index_size / table_size exceeds
    `ratio_threshold`, as a signal worth a second look.
    """
    if ratio_threshold <= 0:
        raise ValueError("ratio_threshold must be > 0")

    conn = get_readonly_connection()
    try:
        with dict_cursor(conn) as cur:
            try:
                rows = _fetch_sizes(cur, schema)
            except psycopg2.Error as exc:
                raise StorageStatsError(
                    f"Failed to fetch storage stats for schema '{schema}': {exc}"
                ) from exc
    finally:
        conn.close()

    tables = []
    for row in rows:
        row = dict(row)
        table_size = row["table_size_bytes"]
        index_size = row["index_size_bytes"]

        if table_size > 0:
            ratio = index_size / table_size
        elif index_size > 0:
            ratio = None  # indexes exist on an effectively empty table
        else:
            ratio = 0.0

        if ratio is None:
            flagged = True
            reason = (
                f"table has {index_size} bytes of indexes but no table data "
                f"(pg_relation_size is 0) — indexes may be unnecessary, "
                f"worth a second look"
            )
        elif ratio > ratio_threshold:
            flagged = True
            reason = (
                f"index size ({row['index_size_pretty']}) is {ratio:.2f}x "
                f"table size ({row['table_size_pretty']}), exceeding the "
                f"configured threshold of {ratio_threshold}x — worth a "
                f"second look, not necessarily a problem"
            )
        else:
            flagged = False
            reason = None

        row["index_to_table_ratio"] = ratio
        row["flagged"] = flagged
        row["reason"] = reason
        tables.append(row)

    return {
        "schema": schema,
        "ratio_threshold": ratio_threshold,
        "tables": tables,
    }
