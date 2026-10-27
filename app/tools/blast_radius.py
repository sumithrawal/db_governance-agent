"""
Blast-radius analysis: given a table, find what else depends on it through
foreign keys — directly and transitively — so a human (or a later
governance layer) can see the dependency surface before acting on that
table. This intentionally says nothing about whether acting on the table
is safe; it only reports foreign-key structure, and says so explicitly in
`limitations`.
"""

from collections import deque
from typing import List

from app.tools.schema_introspect import introspect_schema

_LIMITATIONS = (
    "Only foreign-key relationships declared in this schema are "
    "considered. Views, functions/triggers, and application code that "
    "reference this table are not detected."
)


def compute_blast_radius(tables: List[dict], target: str) -> dict:
    """
    Pure function: no database access. `tables` is the list returned by
    introspect_schema()["tables"]. Walks the foreign-key graph breadth-first
    to find every table that (directly or transitively) depends on `target`
    via a foreign key pointing at it. Tracks visited tables so a cycle in
    the FK graph terminates instead of looping forever, and a table's
    self-referencing FK is ignored (it is not a dependency on anything
    else).
    """
    table_names = {t["table_name"] for t in tables}
    if target not in table_names:
        raise ValueError(f"unknown table '{target}'")

    # reverse_adj[referenced_table] = [(dependent_table, fk_column), ...]
    reverse_adj: dict = {}
    for t in tables:
        t_name = t["table_name"]
        for fk in t.get("foreign_keys", []):
            ref_table = fk["references_table"]
            if ref_table == t_name:
                continue  # self-reference: not a dependency on another table
            reverse_adj.setdefault(ref_table, []).append((t_name, fk["column_name"]))

    direct_map: dict = {}
    for dep_table, column in reverse_adj.get(target, []):
        direct_map.setdefault(dep_table, []).append(column)
    direct_dependents = [
        {"table": table, "columns": columns} for table, columns in direct_map.items()
    ]

    visited = {target}
    queue = deque()
    for dep_table in direct_map:
        visited.add(dep_table)
        queue.append((dep_table, 1, [target, dep_table]))

    transitive_dependents = []
    while queue:
        current, depth, path = queue.popleft()
        transitive_dependents.append(
            {"table": current, "depth": depth, "path": path}
        )
        enqueued_this_round = set()
        for next_table, _column in reverse_adj.get(current, []):
            if next_table not in visited and next_table not in enqueued_this_round:
                enqueued_this_round.add(next_table)
                visited.add(next_table)
                queue.append((next_table, depth + 1, path + [next_table]))

    return {
        "table": target,
        "direct_dependents": direct_dependents,
        "transitive_dependents": transitive_dependents,
        "total_dependents": len(transitive_dependents),
        "limitations": _LIMITATIONS,
    }


def blast_radius(schema: str = "public", table: str = None) -> dict:
    """Tool function: introspects `schema` (database access happens only
    here) and runs the pure compute_blast_radius() against the result."""
    if not table:
        raise ValueError("'table' is required")
    result = introspect_schema(schema)
    return compute_blast_radius(result["tables"], table)
