#!/usr/bin/env python3
"""
Tool-selection accuracy eval.

Reads tests/golden_set.json ([{"question", "expected_tool"}, ...]), asks the
configured Ollama model to route each question to a tool, validates the
result against the registry, and reports accuracy + the list of misses.

This only evaluates routing (the LLM call + registry validation) — it never
executes a tool against the database, so it doesn't need docker-compose up,
only `ollama serve` running with OLLAMA_MODEL pulled.

Usage:
    python scripts/eval_tool_selection.py
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.db  # noqa: F401  (side effect: loads .env from the project root)
from app.llm_client import OllamaError, choose_tool
from app.tool_registry import get_registry
from app.tool_selector import ToolSelectionError, validate_tool_call

GOLDEN_SET_PATH = Path(__file__).resolve().parent.parent / "tests" / "golden_set.json"


def main() -> int:
    model = os.environ.get("OLLAMA_MODEL")
    host = os.environ.get("OLLAMA_HOST")
    if not model or not host:
        print("OLLAMA_MODEL and OLLAMA_HOST must be set (see .env).", file=sys.stderr)
        return 1

    with open(GOLDEN_SET_PATH) as f:
        golden_set = json.load(f)

    registry = get_registry()
    misses = []
    correct = 0

    for case in golden_set:
        question = case["question"]
        expected = case["expected_tool"]

        try:
            raw = choose_tool(question, registry, model=model, host=host)
            tool_name, _arguments = validate_tool_call(raw)
        except (OllamaError, ToolSelectionError) as exc:
            misses.append(
                {"question": question, "expected": expected, "got": None, "error": str(exc)}
            )
            print(f"ERROR  {question!r} -> {exc}")
            continue

        if tool_name == expected:
            correct += 1
            print(f"OK     {question!r} -> {tool_name}")
        else:
            misses.append(
                {"question": question, "expected": expected, "got": tool_name, "error": None}
            )
            print(f"MISS   {question!r} -> got {tool_name!r}, expected {expected!r}")

    total = len(golden_set)
    accuracy = correct / total if total else 0.0
    print()
    print(f"Accuracy: {correct}/{total} ({accuracy:.0%})")
    if misses:
        print("Misses:")
        for m in misses:
            detail = m["error"] or f"got {m['got']!r}"
            print(f"  - {m['question']!r}: expected {m['expected']!r}, {detail}")

    return 0 if not misses else 1


if __name__ == "__main__":
    raise SystemExit(main())
