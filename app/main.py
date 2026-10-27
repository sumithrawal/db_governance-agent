"""
FastAPI app exposing each tool as a plain REST endpoint. No LLM involved —
this is meant to be curled or hit via the /docs UI to validate the
deterministic foundation before Phase 2 wires an LLM on top.
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.audit import audited
from app.db import DBConfigError, DBConnectionError, DBPermissionError, check_health
from app.tool_registry import get_registry
from app.tools.schema_introspect import SchemaIntrospectionError, introspect_schema
from app.tools.storage_stats import StorageStatsError, get_storage_stats
from app.tools.unused_objects import UnusedObjectsError, find_unused_objects

app = FastAPI(
    title="DB Governance Agent — Phase 1",
    description="Deterministic schema introspection, unused-object detection, "
    "and storage stats tools. No LLM integration yet.",
)

_introspect_schema_audited = audited("schema_introspect")(introspect_schema)
_find_unused_objects_audited = audited("unused_objects")(find_unused_objects)
_get_storage_stats_audited = audited("storage_stats")(get_storage_stats)

_CONFIG_ERRORS = (DBConfigError, DBPermissionError)


class SchemaIntrospectRequest(BaseModel):
    schema_name: str = Field("public", alias="schema")

    class Config:
        populate_by_name = True


class UnusedObjectsRequest(BaseModel):
    schema_name: str = Field("public", alias="schema")
    days: int = Field(90, ge=0)

    class Config:
        populate_by_name = True


class StorageStatsRequest(BaseModel):
    schema_name: str = Field("public", alias="schema")
    ratio_threshold: float = Field(1.0, gt=0)

    class Config:
        populate_by_name = True


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
    try:
        return _introspect_schema_audited(schema=body.schema_name)
    except _CONFIG_ERRORS as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except DBConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SchemaIntrospectionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/tools/unused_objects")
def unused_objects_endpoint(body: UnusedObjectsRequest):
    try:
        return _find_unused_objects_audited(schema=body.schema_name, days=body.days)
    except _CONFIG_ERRORS as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except DBConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except UnusedObjectsError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/tools/storage_stats")
def storage_stats_endpoint(body: StorageStatsRequest):
    try:
        return _get_storage_stats_audited(
            schema=body.schema_name, ratio_threshold=body.ratio_threshold
        )
    except _CONFIG_ERRORS as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except DBConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except StorageStatsError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
