"""SDK routes mounted by the server, not by component authors."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from proteus.abilities import abilities
from proteus.activity_def import registered_activities
from proteus.bus_client import bus
from proteus.cache import Cache
from proteus.store import Store
from proteus.styles import styles
from proteus.workflow_def import registered_workflows


class StorePut(BaseModel):
    value: Any = None
    tags: Any = None


class CachePut(BaseModel):
    value: Any = None
    ttl: Any = None


class BusCall(BaseModel):
    component: str
    name: str
    arg: Any = None


class StylePut(BaseModel):
    css: str = ""


class AbilityPrompt(BaseModel):
    prompt: str = ""


def build_router(store: Store, cache: Cache) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    def health() -> dict:
        return {
            "ok": True,
            "name": os.environ.get("PROTEUS_COMPONENT_NAME", ""),
            "version": os.environ.get("PROTEUS_VERSION", ""),
        }

    @router.get("/proteus/manifest")
    def manifest() -> dict:
        return {
            "name": os.environ.get("PROTEUS_COMPONENT_NAME", ""),
            "version": os.environ.get("PROTEUS_VERSION", ""),
            "route_prefix": os.environ.get("PROTEUS_ROUTE_PREFIX", ""),
            "activities": [fn.__name__ for fn in registered_activities()],
            "workflows": [cls.__name__ for cls in registered_workflows()],
        }

    @router.put("/proteus/sdk/store/{key}")
    def put_store(key: str, body: StorePut) -> dict:
        if not isinstance(body.value, dict):
            raise HTTPException(status_code=400, detail="value must be a JSON object")
        tags = [] if body.tags is None else body.tags
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise HTTPException(status_code=400, detail="tags must be a list of strings")
        try:
            return store.put(key, body.value, tags)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/proteus/sdk/store/{key}")
    def get_store(key: str) -> dict:
        found = store.get(key)
        if found is None:
            raise HTTPException(status_code=404, detail="not found")
        return found

    @router.get("/proteus/sdk/store")
    def find_store(request: Request) -> dict:
        tags = request.query_params.getlist("tag")
        return {"items": store.find(tags)}

    @router.delete("/proteus/sdk/store/{key}")
    def delete_store(key: str) -> dict:
        store.delete(key)
        return {"ok": True}

    @router.put("/proteus/sdk/cache/{key}")
    def put_cache(key: str, body: CachePut) -> dict:
        ttl = body.ttl
        if ttl is not None and (isinstance(ttl, bool) or not isinstance(ttl, (int, float))):
            raise HTTPException(status_code=400, detail="ttl must be a number")
        cache.set(key, body.value, None if ttl is None else int(ttl))
        return {"ok": True, "key": key, "value": body.value}

    @router.get("/proteus/sdk/cache/{key}")
    def get_cache(key: str) -> dict:
        value = cache.get(key)
        if value is None:
            raise HTTPException(status_code=404, detail="not found")
        return {"key": key, "value": value}

    @router.post("/proteus/sdk/bus/activity")
    async def call_activity(body: BusCall) -> dict:
        arg = _bus_arg(body.arg)
        try:
            return await bus.activity(body.component, body.name, arg)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.put("/proteus/sdk/styles")
    def put_styles(body: StylePut) -> dict:
        try:
            return styles.put(body.css)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/proteus/sdk/styles")
    def get_styles() -> dict:
        found = styles.get()
        if found is None:
            raise HTTPException(status_code=404, detail="not found")
        return found

    @router.delete("/proteus/sdk/styles")
    def delete_styles() -> dict:
        styles.clear()
        return {"ok": True}

    @router.post("/proteus/sdk/abilities/ask")
    async def ask_ability(body: AbilityPrompt) -> dict:
        if not body.prompt.strip():
            raise HTTPException(status_code=400, detail="prompt is required")
        try:
            return await abilities.ask(body.prompt)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/proteus/sdk/abilities/task")
    async def task_ability(body: AbilityPrompt) -> dict:
        if not body.prompt.strip():
            raise HTTPException(status_code=400, detail="prompt is required")
        try:
            return await abilities.task(body.prompt)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/proteus/sdk/bus/workflow")
    async def call_workflow(body: BusCall) -> dict:
        arg = _bus_arg(body.arg)
        try:
            return await bus.workflow(body.component, body.name, arg)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return router


def _bus_arg(arg: Any) -> dict:
    if arg is None:
        return {}
    if not isinstance(arg, dict):
        raise HTTPException(status_code=400, detail="arg must be a JSON object")
    return arg
