"""
Validates a {"tool", "arguments"} payload — as returned by app/llm_client.py
— against app/tool_registry.py. This is step 2 of POST /ask, deliberately
separate from the LLM call: the model's output is never trusted just
because it satisfied Ollama's structured-output constraint. Anything that
doesn't match the registry exactly is rejected with a specific reason —
never guessed at or silently coerced.
"""

from typing import Tuple

import jsonschema

from app.tool_registry import get_tool


class ToolSelectionError(Exception):
    """The model's tool choice or arguments don't match the registry."""


def validate_tool_call(parsed: dict) -> Tuple[str, dict]:
    if not isinstance(parsed, dict):
        raise ToolSelectionError(f"Expected a JSON object, got: {parsed!r}")

    tool_name = parsed.get("tool")
    arguments = parsed.get("arguments")

    if not isinstance(tool_name, str) or not tool_name:
        raise ToolSelectionError(f"Missing or non-string 'tool' field: {parsed!r}")
    if not isinstance(arguments, dict):
        raise ToolSelectionError(f"Missing or non-object 'arguments' field: {parsed!r}")

    tool_def = get_tool(tool_name)
    if tool_def is None:
        raise ToolSelectionError(f"Unknown tool '{tool_name}' is not in the registry")

    try:
        jsonschema.validate(instance=arguments, schema=tool_def["parameters"])
    except jsonschema.ValidationError as exc:
        raise ToolSelectionError(
            f"Arguments for tool '{tool_name}' failed validation: {exc.message}"
        ) from exc

    return tool_name, arguments
