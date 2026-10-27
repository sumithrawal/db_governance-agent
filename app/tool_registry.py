"""
JSON-schema-described registry of the tools this agent exposes. This is the
contract Phase 2 (LLM integration) will consume for tool-calling — it is
deliberately not wired to any LLM yet. It must be importable and
introspectable on its own:

    python -m app.tool_registry
"""

import json
from typing import Optional

from pydantic import BaseModel


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict
    returns: dict


TOOL_REGISTRY: list = [
    ToolDefinition(
        name="schema_introspect",
        description=(
            "List tables in a schema with columns, primary keys, foreign "
            "keys, indexes, and estimated row counts."
        ),
        parameters={
            "type": "object",
            "properties": {
                "schema": {
                    "type": "string",
                    "default": "public",
                    "description": "Postgres schema name to introspect.",
                }
            },
            "required": [],
            "additionalProperties": False,
        },
        returns={
            "type": "object",
            "description": (
                "{schema, tables: [{table_name, estimated_row_count, "
                "columns, primary_key, foreign_keys, indexes}]}"
            ),
        },
    ),
    ToolDefinition(
        name="unused_objects",
        description=(
            "Flag tables with no recorded scan activity within a window, "
            "and duplicate indexes. Returns evidence and confidence for "
            "each finding, never a bare 'unused' verdict."
        ),
        parameters={
            "type": "object",
            "properties": {
                "schema": {
                    "type": "string",
                    "default": "public",
                    "description": "Postgres schema name to analyze.",
                },
                "days": {
                    "type": "integer",
                    "default": 90,
                    "minimum": 0,
                    "description": (
                        "Lookback window in days for scan-activity findings."
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        returns={
            "type": "object",
            "description": (
                "{schema, window_days, stats_reset_at, "
                "server_supports_last_scan_columns, tables, "
                "unused_candidates, duplicate_indexes}"
            ),
        },
    ),
    ToolDefinition(
        name="storage_stats",
        description=(
            "Report table/index/total size per table, sorted descending, "
            "flagging tables where index size is large relative to table "
            "size as a signal worth a second look."
        ),
        parameters={
            "type": "object",
            "properties": {
                "schema": {
                    "type": "string",
                    "default": "public",
                    "description": "Postgres schema name to analyze.",
                },
                "ratio_threshold": {
                    "type": "number",
                    "default": 1.0,
                    "exclusiveMinimum": 0,
                    "description": (
                        "Flag tables where index_size / table_size exceeds "
                        "this ratio."
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        returns={
            "type": "object",
            "description": "{schema, ratio_threshold, tables: [...]}",
        },
    ),
]


def get_registry() -> list:
    return [tool.model_dump() for tool in TOOL_REGISTRY]


def get_tool(name: str) -> Optional[dict]:
    for tool in TOOL_REGISTRY:
        if tool.name == name:
            return tool.model_dump()
    return None


if __name__ == "__main__":
    print(json.dumps(get_registry(), indent=2))
