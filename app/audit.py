"""
Audit log writer.

Every tool invocation writes exactly one row to audit_log via the
@audited decorator, so individual tools never have to remember to log
themselves. This is a stub: it records what happened, not whether it
should have been allowed. The governance/policy layer that decides what
to do with this history comes in a later phase.
"""

import functools
import json
from typing import Any, Callable, Optional

from app.db import get_audit_connection, DBConnectionError, DBConfigError, DBPermissionError


def _infer_row_count(result: Any) -> Optional[int]:
    if isinstance(result, dict):
        for key in ("tables", "results", "items", "rows"):
            value = result.get(key)
            if isinstance(value, list):
                return len(value)
        return 1
    if isinstance(result, list):
        return len(result)
    return None


def write_audit_log(
    tool_name: str,
    parameters: dict,
    row_count_returned: Optional[int],
    caller: str = "local_user",
    error: Optional[str] = None,
) -> None:
    conn = get_audit_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_log
                    (tool_name, parameters, row_count_returned, caller, error)
                VALUES (%s, %s::jsonb, %s, %s, %s)
                """,
                (
                    tool_name,
                    json.dumps(parameters, default=str),
                    row_count_returned,
                    caller,
                    error,
                ),
            )
    finally:
        conn.close()


def audited(tool_name: str) -> Callable:
    """Decorator: wraps a tool function so every call is recorded in
    audit_log, whether it succeeds or raises."""

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, caller: str = "local_user", **kwargs):
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                try:
                    write_audit_log(
                        tool_name=tool_name,
                        parameters=kwargs,
                        row_count_returned=None,
                        caller=caller,
                        error=str(exc),
                    )
                except (DBConnectionError, DBConfigError, DBPermissionError):
                    # Auditing failed too; don't hide the original error.
                    pass
                raise
            write_audit_log(
                tool_name=tool_name,
                parameters=kwargs,
                row_count_returned=_infer_row_count(result),
                caller=caller,
            )
            return result

        return wrapper

    return decorator
