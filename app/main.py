"""
FastAPI app exposing each tool as a plain REST endpoint, plus POST /ask
(Phase 2) which lets a local Ollama model pick one of these same tools for
a free-text question. The deterministic /tools/* endpoints are unchanged
and remain fully usable without any LLM involved.
"""

import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.audit import audited
from app.db import DBConfigError, DBConnectionError, DBPermissionError, check_health
from app.llm_client import OllamaError, choose_tool
from app.tool_registry import get_registry
from app.tool_selector import ToolSelectionError, validate_tool_call
from app.tools.blast_radius import blast_radius
from app.tools.schema_introspect import SchemaIntrospectionError, introspect_schema
from app.tools.storage_stats import StorageStatsError, get_storage_stats
from app.tools.unused_objects import UnusedObjectsError, find_unused_objects

app = FastAPI(
    title="DB Governance Agent",
    description="Deterministic schema introspection, unused-object detection, "
    "and storage stats tools, plus an LLM tool-selection router (POST /ask).",
)

_introspect_schema_audited = audited("schema_introspect")(introspect_schema)
_find_unused_objects_audited = audited("unused_objects")(find_unused_objects)
_get_storage_stats_audited = audited("storage_stats")(get_storage_stats)
_blast_radius_audited = audited("blast_radius")(blast_radius)

_TOOL_DISPATCH = {
    "schema_introspect": _introspect_schema_audited,
    "unused_objects": _find_unused_objects_audited,
    "storage_stats": _get_storage_stats_audited,
    "blast_radius": _blast_radius_audited,
}

_CONFIG_ERRORS = (DBConfigError, DBPermissionError)
_TOOL_ERRORS = (SchemaIntrospectionError, UnusedObjectsError, StorageStatsError)


def _invoke_tool(fn, **kwargs):
    """Shared error-to-HTTP-status mapping for every tool call, whether it
    came from a direct /tools/* request or from the /ask router."""
    try:
        return fn(**kwargs)
    except _CONFIG_ERRORS as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except DBConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except _TOOL_ERRORS as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class SchemaIntrospectRequest(BaseModel):
    schema_name: str = Field("public", alias="schema")

    class Config:
        populate_by_name = True


class UnusedObjectsRequest(BaseModel):
    schema_name: str = Field("public", alias="schema")
    days: int = Field(90, ge=0)
    include_blast_radius: bool = False

    class Config:
        populate_by_name = True


class StorageStatsRequest(BaseModel):
    schema_name: str = Field("public", alias="schema")
    ratio_threshold: float = Field(1.0, gt=0)

    class Config:
        populate_by_name = True


class BlastRadiusRequest(BaseModel):
    schema_name: str = Field("public", alias="schema")
    table: str

    class Config:
        populate_by_name = True


class AskRequest(BaseModel):
    question: str


@app.get("/health")
def health():
    try:
        return {"status": "ok", **check_health()}
    except _CONFIG_ERRORS as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except DBConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/tools")
def list_tools():
    return {"tools": get_registry()}


@app.post("/tools/schema_introspect")
def schema_introspect_endpoint(body: SchemaIntrospectRequest):
    return _invoke_tool(_introspect_schema_audited, schema=body.schema_name)


@app.post("/tools/unused_objects")
def unused_objects_endpoint(body: UnusedObjectsRequest):
    return _invoke_tool(
        _find_unused_objects_audited,
        schema=body.schema_name,
        days=body.days,
        include_blast_radius=body.include_blast_radius,
    )


@app.post("/tools/storage_stats")
def storage_stats_endpoint(body: StorageStatsRequest):
    return _invoke_tool(
        _get_storage_stats_audited,
        schema=body.schema_name,
        ratio_threshold=body.ratio_threshold,
    )


@app.post("/tools/blast_radius")
def blast_radius_endpoint(body: BlastRadiusRequest):
    return _invoke_tool(_blast_radius_audited, schema=body.schema_name, table=body.table)


@app.post("/ask")
def ask_endpoint(body: AskRequest):
    model = os.environ.get("OLLAMA_MODEL")
    host = os.environ.get("OLLAMA_HOST")
    if not model or not host:
        raise HTTPException(
            status_code=500,
            detail="OLLAMA_MODEL and OLLAMA_HOST must be set in .env",
        )

    try:
        raw = choose_tool(body.question, get_registry(), model=model, host=host)
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        tool_name, arguments = validate_tool_call(raw)
    except ToolSelectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    result = _invoke_tool(_TOOL_DISPATCH[tool_name], **arguments)

    return {"tool_chosen": tool_name, "arguments": arguments, "result": result}
