"""
Unused-object detection.

This module never claims a table is "unused". It returns a status plus the
evidence and reasoning behind that status, so a later governance layer can
decide what (if anything) to do about it. Two independent signals are
produced:

1. Scan-activity findings, based on pg_stat_user_tables. These are
   inherently bounded by how long Postgres has been collecting stats, so
   every finding is qualified by confidence and an explicit reason.
2. Duplicate-index findings, based on comparing leading index columns per
   table. These don't depend on the stats window at all, so they're always
   reported at high confidence.
"""

import datetime
from typing import Optional

import psycopg2

from app.db import get_readonly_connection, dict_cursor

STATUS_ACTIVE = "active"
STATUS_NO_ACTIVITY_IN_WINDOW = "no_activity_in_window"
STATUS_NO_ACTIVITY_EVER_RECORDED = "no_activity_ever_recorded"
STATUS_INSUFFICIENT_HISTORY = "insufficient_history"


class UnusedObjectsError(Exception):
    """Raised when the unused-object detection queries fail."""


def _server_supports_last_scan_columns(cur) -> bool:
    # last_seq_scan / last_idx_scan were added to pg_stat_user_tables in
    # Postgres 16.
    cur.execute("SHOW server_version_num")
    version_num = int(cur.fetchone()["server_version_num"])
    return version_num >= 160000


def _fetch_stats_reset(cur) -> Optional[datetime.datetime]:
    cur.execute(
        "SELECT stats_reset FROM pg_stat_database WHERE datname = current_database()"
    )
    row = cur.fetchone()
    return row["stats_reset"] if row else None


def _fetch_table_stats(cur, schema: str, has_last_scan_cols: bool) -> list:
    last_scan_cols = ", last_seq_scan, last_idx_scan" if has_last_scan_cols else ""
    cur.execute(
        f"""
        SELECT
            relname AS table_name,
            seq_scan,
            idx_scan
            {last_scan_cols}
        FROM pg_stat_user_tables
        WHERE schemaname = %s
        ORDER BY relname
        """,
        (schema,),
    )
    return cur.fetchall()


def _classify_table(
    row: dict,
    window_start: datetime.datetime,
    stats_reset: Optional[datetime.datetime],
    has_last_scan_cols: bool,
    days: int,
) -> dict:
    seq_scan = row["seq_scan"] or 0
    idx_scan = row["idx_scan"] or 0
    total_scans = seq_scan + idx_scan

    if has_last_scan_cols:
        last_seq_scan = row.get("last_seq_scan")
        last_idx_scan = row.get("last_idx_scan")
        last_activity = max(
            [t for t in (last_seq_scan, last_idx_scan) if t is not None],
            default=None,
        )
        if last_activity is not None:
            if last_activity >= window_start:
                status, confidence, reason = (
                    STATUS_ACTIVE,
                    "high",
                    f"last scan recorded at {last_activity.isoformat()}, "
                    f"within the {days}-day window",
                )
            else:
                status, confidence, reason = (
                    STATUS_NO_ACTIVITY_IN_WINDOW,
                    "high",
                    f"last scan recorded at {last_activity.isoformat()}, "
                    f"before the {days}-day window started "
                    f"({window_start.isoformat()})",
                )
        elif total_scans > 0:
            # Counters are nonzero but per-scan timestamps are unavailable
            # for those counts (can happen right after upgrading to PG16).
            status, confidence, reason = (
                STATUS_INSUFFICIENT_HISTORY,
                "low",
                f"{total_scans} scans recorded cumulatively but no "
                f"last-scan timestamp is available to place them in time",
            )
        else:
            if stats_reset is not None and stats_reset < window_start:
                status, confidence, reason = (
                    STATUS_NO_ACTIVITY_EVER_RECORDED,
                    "high",
                    f"zero scans recorded and stats have been collected "
                    f"since {stats_reset.isoformat()}, which predates the "
                    f"{days}-day window",
                )
            else:
                if stats_reset is None:
                    reset_clause = (
                        "stats have never been reset for this database, so "
                        "there is no way to confirm they cover the full "
                        "window"
                    )
                else:
                    reset_clause = (
                        f"stats were reset on {stats_reset.isoformat()}, "
                        f"which is less than {days} days ago"
                    )
                status, confidence, reason = (
                    STATUS_INSUFFICIENT_HISTORY,
                    "low",
                    f"zero scans recorded, but {reset_clause} — insufficient "
                    f"history to cover the {days}-day window",
                )
        return {
            **row,
            "status": status,
            "confidence": confidence,
            "reason": reason,
        }

    # Pre-PG16: only cumulative counters, no per-scan timestamps.
    if total_scans > 0:
        status, confidence, reason = (
            STATUS_ACTIVE,
            "medium",
            f"{total_scans} scans recorded since stats reset on "
            f"{stats_reset.isoformat() if stats_reset else 'unknown'}, but "
            f"this Postgres version has no last-scan timestamp to confirm "
            f"activity fell within the last {days} days specifically",
        )
    elif stats_reset is not None and stats_reset < window_start:
        status, confidence, reason = (
            STATUS_NO_ACTIVITY_EVER_RECORDED,
            "high",
            f"zero scans recorded since stats reset on "
            f"{stats_reset.isoformat()}, which predates the {days}-day "
            f"window",
        )
    else:
        if stats_reset is None:
            reset_clause = (
                "stats have never been reset for this database, so there "
                "is no way to confirm they cover the full window"
            )
        else:
            reset_clause = (
                f"stats were reset on {stats_reset.isoformat()}, which is "
                f"less than {days} days ago"
            )
        status, confidence, reason = (
            STATUS_INSUFFICIENT_HISTORY,
            "low",
            f"zero scans recorded, but {reset_clause} — insufficient "
            f"history for the {days}-day window",
        )
    return {**row, "status": status, "confidence": confidence, "reason": reason}


def _fetch_duplicate_indexes(cur, schema: str) -> list:
    cur.execute(
        """
        SELECT
            t.relname AS table_name,
            i.relname AS index_name,
            ix.indisunique AS is_unique,
            ix.indisprimary AS is_primary,
            array_agg(a.attname ORDER BY k.ord) AS columns
        FROM pg_index ix
        JOIN pg_class t ON t.oid = ix.indrelid
        JOIN pg_class i ON i.oid = ix.indexrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ord) ON true
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
        WHERE n.nspname = %s
        GROUP BY t.relname, i.relname, ix.indisunique, ix.indisprimary
        ORDER BY t.relname, i.relname
        """,
        (schema,),
    )
    rows = cur.fetchall()

    groups: dict = {}
    for row in rows:
        key = (row["table_name"], tuple(row["columns"]))
        groups.setdefault(key, []).append(row)

    duplicates = []
    for (table_name, columns), indexes in groups.items():
        if len(indexes) > 1:
            index_names = [ix["index_name"] for ix in indexes]
            duplicates.append(
                {
                    "table_name": table_name,
                    "columns": list(columns),
                    "index_names": index_names,
                    "confidence": "high",
                    "reason": (
                        f"indexes {', '.join(index_names)} on "
                        f"{table_name}({', '.join(columns)}) share the same "
                        f"leading columns and are redundant with each other"
                    ),
                }
            )
    return duplicates


def find_unused_objects(schema: str = "public", days: int = 90) -> dict:
    """
    Flag tables with no recorded scan activity in the last `days` days, and
    duplicate indexes (same table, same leading columns), as findings with
    evidence — never as a bare "unused" verdict.
    """
    if days < 0:
        raise ValueError("days must be >= 0")

    conn = get_readonly_connection()
    try:
        with dict_cursor(conn) as cur:
            try:
                has_last_scan_cols = _server_supports_last_scan_columns(cur)
                stats_reset = _fetch_stats_reset(cur)
                window_start = datetime.datetime.now(
                    datetime.timezone.utc
                ) - datetime.timedelta(days=days)

                raw_tables = _fetch_table_stats(cur, schema, has_last_scan_cols)
                classified = [
                    _classify_table(
                        dict(row), window_start, stats_reset, has_last_scan_cols, days
                    )
                    for row in raw_tables
                ]
                duplicate_indexes = _fetch_duplicate_indexes(cur, schema)
            except psycopg2.Error as exc:
                raise UnusedObjectsError(
                    f"Failed to detect unused objects in schema '{schema}': {exc}"
                ) from exc
    finally:
        conn.close()

    unused_candidates = [
        t for t in classified if t["status"] != STATUS_ACTIVE
    ]

    return {
        "schema": schema,
        "window_days": days,
        "stats_reset_at": stats_reset.isoformat() if stats_reset else None,
        "server_supports_last_scan_columns": has_last_scan_cols,
        "tables": classified,
        "unused_candidates": unused_candidates,
        "duplicate_indexes": duplicate_indexes,
    }
