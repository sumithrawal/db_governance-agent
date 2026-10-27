"""
Minimal client for a local Ollama server's structured-output (JSON schema)
chat endpoint. This is the only place an LLM is called from in this
project — no retries, no fallback. If Ollama can't be reached, or the
model's output isn't parseable JSON despite the schema constraint, that's
surfaced as an error, not papered over.
"""

import json
import urllib.error
import urllib.request


class OllamaError(Exception):
    """Ollama is unreachable, misconfigured, or returned something that
    isn't valid JSON."""


def build_router_schema(registry: list) -> dict:
    """The JSON schema handed to Ollama's `format` option, built from the
    tool registry: only the top-level {tool, arguments} shape is enforced
    here (tool name constrained to the registry's names); per-tool argument
    validity is checked separately in app/tool_selector.py."""
    return {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "enum": [t["name"] for t in registry]},
            "arguments": {"type": "object"},
        },
        "required": ["tool", "arguments"],
    }


def _build_system_prompt(registry: list) -> str:
    lines = [
        "You are a tool-selection router for a read-only database "
        "governance agent. Given the user's question, choose exactly one "
        "tool from the list below and the arguments to call it with. "
        "Respond with JSON only, matching the required schema exactly — "
        "use only the parameter names listed for the chosen tool.",
        "",
        "Available tools:",
    ]
    for tool in registry:
        lines.append(f"- {tool['name']}: {tool['description']}")
        lines.append(f"  parameters schema: {json.dumps(tool['parameters'])}")
    return "\n".join(lines)


def choose_tool(
    question: str,
    registry: list,
    model: str,
    host: str,
    timeout: int = 60,
) -> dict:
    """Ask the local Ollama model to pick a tool + arguments for `question`.
    Returns the parsed {"tool": ..., "arguments": {...}} dict as-is —
    unvalidated against the registry. Raises OllamaError on any failure to
    reach Ollama or to parse its output as JSON."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _build_system_prompt(registry)},
            {"role": "user", "content": question},
        ],
        "format": build_router_schema(registry),
        "stream": False,
        "options": {"temperature": 0},
    }
    url = f"{host.rstrip('/')}/api/chat"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_body = resp.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise OllamaError(
            f"Could not reach Ollama at {url}: {exc}. Is `ollama serve` "
            f"running and has model '{model}' been pulled (`ollama pull "
            f"{model}`)?"
        ) from exc

    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise OllamaError(
            f"Ollama's HTTP response was not valid JSON: {exc}"
        ) from exc

    if "error" in body:
        raise OllamaError(f"Ollama returned an error: {body['error']}")

    content = body.get("message", {}).get("content")
    if not content:
        raise OllamaError(f"Ollama response had no message content: {body}")

    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise OllamaError(
            f"Model output was not valid JSON despite the structured-output "
            f"constraint: {content!r} ({exc})"
        ) from exc
