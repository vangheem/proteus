"""Component server. ``python -m proteus.serve``."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType

import uvicorn
from fastapi import FastAPI

from proteus.activity_def import registered_activities
from proteus.builtin_workflow import ProteusCallWorkflow
from proteus.cache import Cache
from proteus.component import Component
from proteus.routes import build_router
from proteus.store import Store
from proteus.workflow_def import registered_workflows

logger = logging.getLogger("proteus")


def create_app(component_dir: str | None = None) -> FastAPI:
    directory = Path(component_dir or os.environ.get("PROTEUS_COMPONENT_DIR", "")).resolve()
    if not directory.is_dir():
        raise RuntimeError(f"PROTEUS_COMPONENT_DIR is not a directory: {directory}")
    _install_path(directory)
    component = _load_component(directory)
    _load_optional(directory, "activities")
    _load_optional(directory, "workflows")

    name = os.environ.get("PROTEUS_COMPONENT_NAME", "")
    activities = registered_activities()
    workflows = _worker_workflows()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Own thread and loop so a down Temporal server cannot stall HTTP.
        thread = threading.Thread(
            target=_worker_thread,
            args=(name, activities, workflows),
            name=f"proteus-temporal-{name or 'component'}",
            daemon=True,
        )
        thread.start()
        app.state.temporal_thread = thread
        yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(build_router(Store(), Cache()))
    app.include_router(component.router)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host="0.0.0.0", port=8000)


def _install_path(directory: Path) -> None:
    path = str(directory)
    if path not in sys.path:
        sys.path.insert(0, path)


def _load_component(directory: Path) -> Component:
    app_mod = _load_file_module(directory, "app")
    if app_mod is None:
        raise RuntimeError(f"missing {directory / 'app.py'}")
    if hasattr(app_mod, "setup") and callable(app_mod.setup):
        component = app_mod.setup()
    elif hasattr(app_mod, "component"):
        component = app_mod.component
    else:
        raise RuntimeError("app.py must define setup() or component")
    if not isinstance(component, Component):
        raise RuntimeError("setup() must return a Component")
    return component


def _load_optional(directory: Path, module_name: str) -> None:
    _load_file_module(directory, module_name)


def _load_file_module(directory: Path, module_name: str) -> ModuleType | None:
    path = directory / f"{module_name}.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _worker_workflows() -> list[type]:
    workflows: list[type] = [ProteusCallWorkflow]
    seen = {ProteusCallWorkflow.__name__}
    for cls in registered_workflows():
        if cls.__name__ in seen:
            continue
        seen.add(cls.__name__)
        workflows.append(cls)
    return workflows


def _worker_thread(name: str, activities: list, workflows: list[type]) -> None:
    while True:
        try:
            asyncio.run(_worker_loop(name, activities, workflows))
        except Exception:
            logger.warning("temporal worker thread stopped; retrying", exc_info=True)
            # asyncio.run already exited. Sleep here so a failing import cannot spin.
            try:
                import time

                time.sleep(2)
            except Exception:
                return


async def _worker_loop(name: str, activities: list, workflows: list[type]) -> None:
    address = os.environ.get("TEMPORAL_ADDRESS", "127.0.0.1:7233")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")
    queue = f"proteus-{name}"
    delay = 1.0
    while True:
        try:
            await _run_worker(address, namespace, queue, activities, workflows)
            logger.warning("temporal worker exited; reconnecting")
            delay = 1.0
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "temporal worker unavailable on %s; retrying in %.0fs",
                queue,
                delay,
                exc_info=True,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10)


async def _run_worker(
    address: str,
    namespace: str,
    queue: str,
    activities: list,
    workflows: list[type],
) -> None:
    from temporalio.client import Client
    from temporalio.worker import UnsandboxedWorkflowRunner, Worker

    client = await Client.connect(address, namespace=namespace)
    # Component workflows import the SDK and user activities. The sandbox reloads
    # those modules and rejects them. HTTP must stay up either way; unsandboxed
    # execution keeps the bus working for interchangeable components.
    worker = Worker(
        client,
        task_queue=queue,
        workflows=workflows,
        activities=activities,
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    logger.info("temporal worker listening on %s", queue)
    await worker.run()


if __name__ == "__main__":
    main()
