# Proteus specification

This file is the contract. Implement it. Do not invent a parallel design.

## What this is

Proteus is a self-mutating app. A Rust coordinator is the only process that accepts browser traffic. It deploys Python components as Docker containers, routes HTTP to them, versions every component in PostgreSQL, and uses Herdr to direct an OpenCode agent when a person asks the app to change.

The coordinator can run on the host or in a container. Component execution goes through a `Runtime` trait so a future Kubernetes implementation replaces Docker without changing routing, versioning, or the HTTP API.

## Layout

```
proteus/
  SPEC.md
  docker-compose.yml          # postgres, redis, temporal, temporal-ui
  deploy/schema.sql           # applied by the coordinator on startup
  deploy/init-db.sql
  coordinator/                # Rust binary `proteus`
  python/                     # SDK + base image
    Dockerfile
    requirements.txt
    proteus/
  components/
    bootstrap/                # root UI, route prefix /
    echo/                     # example component, route prefix /echo
  tests/e2e/run.py
```

Ownership while building:

- `coordinator/` is the Rust track.
- `python/` and `components/` are the Python track.
- Do not edit the other track, `tests/`, `deploy/schema.sql`, or this file.

## Infrastructure

`docker compose up -d` starts:

- Postgres 16 on `127.0.0.1:5432`, superuser `postgres`/`postgres`, app database `proteus` owned by `proteus`/`proteus`.
- Redis 7 on `127.0.0.1:6379`.
- Temporal auto-setup on `127.0.0.1:7233`, using the same Postgres.
- Temporal UI on `127.0.0.1:8233`.

Compose network name is `proteus`. Component containers join that network and reach infra by service DNS: `postgres`, `redis`, `temporal`. The coordinator on the host uses the published ports.

## Environment

See `.env.example`. Every variable there must be honored. Defaults in that file are the defaults in code. `PROTEUS_MODEL` defaults to `xai/grok-4.7`. `PROTEUS_AGENT_KIND` defaults to `opencode`. `PROTEUS_AGENT_BACKEND` defaults to `herdr`. `PROTEUS_RUNTIME` defaults to `docker`.

Relative paths resolve from the process working directory, which scripts set to the repo root.

## Schema

Apply `deploy/schema.sql` on startup with `sqlx::query`. Do not use `sqlx::query!`. Do not change the schema.

`kv_entries.value` is a JSON object. Tags live in `kv_tags`. Previous component sources live in `component_versions.source` as a JSON object of relative path to file text. `coordinator_events.id` is the monotonic revision shown in the status frame.

## Runtime abstraction

`coordinator/src/runtime/mod.rs` defines the only execution interface the rest of the coordinator may use:

```rust
#[async_trait]
pub trait Runtime: Send + Sync {
    fn kind(&self) -> &'static str;

    async fn ensure_base_image(&self, dockerfile_dir: &Path, tag: &str) -> Result<()>;

    async fn build_component(&self, image: &str, context_dir: &Path) -> Result<()>;

    async fn start(&self, spec: &StartSpec) -> Result<RunningService>;

    async fn stop(&self, runtime_id: &str) -> Result<()>;

    async fn list(&self) -> Result<Vec<RunningService>>;
}

pub struct StartSpec {
    pub name: String,
    pub version: i32,
    pub image: String,
    pub env: Vec<(String, String)>,
    pub network: String,
    pub container_port: u16, // 8000
}

pub struct RunningService {
    pub runtime_id: String,
    pub endpoint: String, // http://127.0.0.1:{host_port}
}
```

`main` selects the implementation from `PROTEUS_RUNTIME`:

- `docker` → `DockerRuntime`, which shells out to the `docker` CLI. No bollard.
- `kubernetes` or `k8s` → `KubernetesRuntime`, a second implementation of the same trait. Its methods return a clear not-implemented error. It exists so the seam is real.

No other module may spawn `docker` or talk about container ids except the Docker implementation. Routing, versioning, and handlers depend only on `Arc<dyn Runtime>` and `endpoint`.

Docker implementation requirements:

- `ensure_base_image` runs `docker build -t {tag} {dockerfile_dir}` when `docker image inspect {tag}` fails.
- `build_component` runs `docker build -t {image} {context_dir}`. The context contains a generated Dockerfile and the component files. Dockerfile contents:

  ```
  FROM proteus-python-base:latest
  COPY component /app/component
  ENV PROTEUS_COMPONENT_DIR=/app/component
  ```

  Use the configured base tag, not a hardcoded one, in the generated Dockerfile.
- `start` runs `docker run -d --name proteus-{name}-{version} --network {network} --label proteus.managed=true --label proteus.component={name} --label proteus.version={version} -p 127.0.0.1::{container_port} --add-host host.docker.internal:host-gateway {image}`.
- Publish a random host port. Read it back with `docker port {id} {container_port}` and set `endpoint` to `http://127.0.0.1:{port}`.
- `stop` runs `docker rm -f {runtime_id}` and ignores "no such container".
- `list` uses `docker ps -aq --filter label=proteus.managed=true` plus inspect/port.
- On deploy, if a managed container for the same name and version is already healthy, reuse it. Stop managed containers for that component at any other version after the new one is healthy.

Component env passed to `start`:

```
DATABASE_URL={PROTEUS_COMPONENT_DATABASE_URL}
REDIS_URL={PROTEUS_COMPONENT_REDIS_URL}
TEMPORAL_ADDRESS={PROTEUS_COMPONENT_TEMPORAL_ADDRESS}
TEMPORAL_NAMESPACE={PROTEUS_TEMPORAL_NAMESPACE}
PROTEUS_COMPONENT_NAME={name}
PROTEUS_COMPONENT_DIR=/app/component
PROTEUS_ROUTE_PREFIX={prefix}
PROTEUS_VERSION={version}
```

## Agent director abstraction

`coordinator/src/agent/mod.rs`:

```rust
#[async_trait]
pub trait AgentDirector: Send + Sync {
    fn kind(&self) -> &'static str;
    fn model(&self) -> &str;
    fn plan(&self, prompt: &str) -> AgentPlan;
    async fn submit(&self, prompt: &str) -> Result<String>;
    async fn probe(&self) -> Result<Value>;
}
```

`HerdrDirector` is the default. It shells out to `herdr`. `plan` does not execute anything. It returns the commands `submit` would run:

1. `herdr pane split --pane {HERDR_PANE_ID or the literal HERDR_PANE_ID} --direction right --cwd {root} --no-focus`
2. `herdr agent start proteus-mutator --kind opencode --pane {new-pane} --timeout 60000 -- --auto`
3. `herdr agent prompt proteus-mutator {task-prompt} --wait`, then keep waiting while the agent status is `working`. A long task is not failed for exceeding `PROTEUS_AGENT_TIMEOUT_MS`.

The task prompt must tell the agent:

- project root and components directory
- only create or edit files under the components directory
- do not modify the coordinator, the Python SDK, deploy, tests, or the introspection UI
- the introspection UI is immutable
- component contract: directory with `proteus.toml`, `app.py`, optional `activities.py` and `workflows.py`
- UIs use HTMX; do not add a status bar
- do not deploy; the coordinator versions and swaps after the agent finishes
- configured model is `xai/grok-4.7` unless `PROTEUS_MODEL` overrides it

`submit` holds a mutex, reuses one mutator pane/agent if already started, runs those herdr commands, and returns the agent output. `probe` runs `herdr agent list` and returns ok if the command exits 0. The HTTP server must still start if herdr is unavailable. A missing `HERDR_PANE_ID` fails `submit` with a clear error and does not crash the process.

`main` selects the director from `PROTEUS_AGENT_BACKEND`. Unknown values error at startup. Only `herdr` is required.

On startup, if `{root}/opencode.json` is missing, write `{"$schema":"https://opencode.ai/config.json","model":"<PROTEUS_MODEL>"}`. Do not overwrite an existing file.

## Versioning and deploy

Source hash is sha256 hex of each relative path and its UTF-8 contents, paths sorted, each path and content followed by a NUL byte.

Deploy path used by startup, the deploy API, and the post-agent sync:

1. Read `proteus.toml` from the source map. Required keys: `name`, `route_prefix`. Optional: `autostart` (default false), `description`.
2. If `name` + `source_hash` already exists, activate that version instead of inserting another.
3. Otherwise insert the next version number for that name and store the full source.
4. Write the source to `var/builds/{name}/{version}/component/` and a generated Dockerfile in the build context root.
5. `runtime.build_component` with image `proteus/{name}:{version}`.
6. `runtime.start`, then poll `{endpoint}/health` until HTTP 200 or 60s.
7. Upsert `components` as running, with endpoint, runtime id, image, prefix, version.
8. Insert a `coordinator_events` row whose message states the component and version. This bumps the revision the status frame watches.
9. Stop the previous container for that name.

Startup scans `{components_dir}/*/proteus.toml` and deploys every `autostart = true` component before binding the HTTP port. Bootstrap failure exits the process. Startup also adopts an already-healthy container for the same name and version instead of starting a duplicate.

Rollback loads the stored source for that version, writes it back to `components/{name}/`, and runs the same activate path. Disk and database stay aligned. Old rows in `component_versions` are never deleted.

## HTTP API

Listen on `PROTEUS_HTTP_ADDR`.

Reserved prefix `/__proteus` is never proxied.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/__proteus/health` | `{"ok":true,"service":"proteus-coordinator"}` |
| GET | `/__proteus/status` | JSON below |
| GET | `/__proteus/introspect` | immutable HTML |
| GET | `/__proteus/api/components` | JSON list |
| GET | `/__proteus/api/components/{name}/versions` | `{"versions":[{"version":1,"source_hash":"...","created_at":"..."}]}` |
| POST | `/__proteus/api/components/{name}/deploy` | body `{"source":{"proteus.toml":"...","app.py":"..."}}`, synchronous deploy, returns `{"name","version","status"}` |
| POST | `/__proteus/api/components/{name}/rollback` | body `{"version":1}`, returns the same shape |
| POST | `/__proteus/api/tasks` | JSON `{"prompt":"..."}` or form field `prompt`. Insert a queued task and spawn `submit` + disk sync. If header `HX-Request: true`, return an HTML fragment, else JSON `{"id","status","prompt"}` |
| GET | `/__proteus/api/tasks` | JSON list, newest first |
| GET | `/__proteus/api/tasks/html` | HTML fragment list for the bootstrap poller |
| GET | `/__proteus/api/agent/plan?prompt=` | director plan, no side effects |
| GET | `/__proteus/api/agent/probe` | director probe |
| GET | `/__proteus/api/events` | recent events |

`GET /__proteus/status`:

```json
{
  "ok": true,
  "revision": 1,
  "runtime": "docker",
  "agent_backend": "herdr",
  "agent_kind": "opencode",
  "model": "xai/grok-4.7",
  "busy": false,
  "latest_event": {"id": 1, "kind": "started", "message": "coordinator started", "created_at": "..."},
  "components": [
    {"name": "bootstrap", "version": 1, "route_prefix": "/", "status": "running", "endpoint": "http://127.0.0.1:18080"}
  ]
}
```

`runtime` is `Runtime::kind()`, not a hardcoded string in the handler. `model` is the configured model.

### Routing

Longest `route_prefix` wins. A prefix matches when the path equals it or starts with `prefix + "/"`. `/` is the fallback and is owned by bootstrap. Strip the matched prefix before proxying, except the prefix `/` which is not stripped. Normalize an empty stripped path to `/`.

Examples:

- `GET /echo/health` → `{echo_endpoint}/health`
- `GET /echo/proteus/sdk/store/k` → `{echo_endpoint}/proteus/sdk/store/k`
- `GET /` → `{bootstrap_endpoint}/`
- `GET /__proteus/health` is not proxied

Forward method, query, and body. Drop hop-by-hop headers. Buffer the upstream response. If `content-type` contains `text/html`, inject the status frame unless `id="proteus-status-frame"` is already present. Recalculate `content-length`.

### Status frame

Every HTML response, including introspection, gets this injected before `</body>` (or appended). `data-revision` is the current event id. The frame is fixed to the bottom, polls `GET /__proteus/status` every 2 seconds, shows `latest_event.message`, and reveals a Refresh button when `revision` differs from `data-revision`. The button calls `location.reload()`. Do not auto-reload.

Required ids, because tests look for them: `proteus-status-frame`, `proteus-frame-message`, `proteus-frame-refresh`. The script must `fetch('/__proteus/status'`.

### Introspection UI

Server-rendered by the coordinator. Not a Python component. Required:

- `data-proteus-introspect="1"`
- the word `immutable`
- a sentence that this UI cannot be swapped by component deploys
- component name, version, status, route prefix
- recent events
- runtime kind, agent backend, model

No external assets.

## Python SDK and base image

`python/Dockerfile` is the base image `proteus-python-base`. Use `python:3.12-slim-bookworm`, install `python/requirements.txt`, copy the `proteus` package to `/app/proteus`, `PYTHONPATH=/app`, expose 8000, `CMD ["python", "-m", "proteus.serve"]`.

Requirements, unpinned is fine: `fastapi`, `uvicorn`, `psycopg[binary]`, `redis`, `temporalio`.

`python -m proteus.serve`:

- Read `PROTEUS_COMPONENT_DIR`.
- Put that directory on `sys.path`.
- Load `app.py`. Use `setup()` if present, otherwise a module-level `component`.
- Load `activities.py` and `workflows.py` when present.
- Build one FastAPI app that includes the component router plus the SDK routes below.
- Start a Temporal worker in the background on task queue `proteus-{name}`. Retry the connection forever. Do not crash the HTTP server if Temporal is down.
- Serve `0.0.0.0:8000`.

### Component object

```python
class Component:
    def get(self, path: str): ...
    def post(self, path: str): ...
    def page(self, title: str, body: str) -> HTMLResponse: ...
```

`page()` returns a full HTML document that loads `https://unpkg.com/htmx.org@2.0.4`, sets `padding-bottom: 4rem` on `body`, and does not include a status bar. Escape `title`. Do not escape `body`.

### SDK routes, mounted by the server, not by component authors

- `GET /health` → `{"ok": true, "name": "...", "version": "..."}` version from `PROTEUS_VERSION`.
- `GET /proteus/manifest` → `{"name", "version", "route_prefix", "activities": [...], "workflows": [...]}`.
- `PUT /proteus/sdk/store/{key}` body `{"value": {object}, "tags": ["a"]}`. Replace tags. 400 if `value` is not an object.
- `GET /proteus/sdk/store/{key}` → `{"key", "value", "tags"}` or 404.
- `GET /proteus/sdk/store?tag=a&tag=b` → `{"items":[{"key","value","tags"}]}`. Empty tag list returns every entry. With tags, return entries that have all of the tags.
- `DELETE /proteus/sdk/store/{key}` → `{"ok": true}`.
- `PUT /proteus/sdk/cache/{key}` body `{"value": "abc", "ttl": 60}`. `ttl` optional.
- `GET /proteus/sdk/cache/{key}` → `{"key", "value"}` or 404.
- `POST /proteus/sdk/bus/activity` body `{"component", "name", "arg"}`.
- `POST /proteus/sdk/bus/workflow` body `{"component", "name", "arg"}`.

Store and cache talk to Postgres and Redis directly using `DATABASE_URL` and `REDIS_URL`. The Python interface for tags is a `list[str]`.

`Store.put/get/find/delete` and `Cache.set/get/delete` are the reusable library API. The HTTP routes are thin wrappers. `find(tags)` uses AND semantics.

### Temporal

Every worker registers the component's activities and workflows plus a built-in workflow named `ProteusCallWorkflow`. Its input is `{"activity": "...", "arg": {...}}` and it executes that activity by name on the same worker with a 30s schedule-to-close timeout.

`bus` activity calls start `ProteusCallWorkflow` on task queue `proteus-{component}`. Workflow calls start the named workflow on that queue. Both return `{"result": ...}`. Generate a unique workflow id. Activity and workflow arguments are one JSON object.

Decorators:

```python
@activity
async def echo(arg: dict) -> dict: ...

@workflow_cls
class EchoWorkflow: ...
```

`@activity` applies `temporalio.activity.defn` and marks the function for discovery. `@workflow_cls` applies `temporalio.workflow.defn` and marks the class. Manifest names are function `__name__` and class `__name__`.

## Shipped components

### `components/bootstrap`

`proteus.toml`: `name = "bootstrap"`, `route_prefix = "/"`, `autostart = true`.

`app.py` exposes `setup()`. `GET /` renders a page whose visible heading is `Proteus` and whose form posts with HTMX to `/__proteus/api/tasks`, target `#result`. The textarea has `id="proteus-prompt"` and `name="prompt"`. A div `#result` polls `/__proteus/api/tasks/html` with HTMX on load and every 3s. Short explanation that the coordinator swaps components in as they are built.

### `components/echo`

`proteus.toml`: `name = "echo"`, `route_prefix = "/echo"`, `autostart = true`.

`GET /` renders HTML containing `id="echo-body"` and the text `echo-v1`.

`activities.py` defines `@activity async def echo(arg: dict) -> dict` returning `{"text": arg.get("text", ""), "echo": True}`.

`workflows.py` defines `EchoWorkflow` which executes the `echo` activity and returns its result. Import activities inside `workflow.unsafe.imports_passed_through()`.

## Coordinator module split

Suggested, not mandatory, as long as the traits and HTTP contract hold:

```
coordinator/src/main.rs
coordinator/src/config.rs
coordinator/src/error.rs
coordinator/src/db.rs
coordinator/src/deploy.rs
coordinator/src/proxy.rs
coordinator/src/introspect.rs
coordinator/src/runtime/mod.rs
coordinator/src/runtime/docker.rs
coordinator/src/runtime/k8s.rs
coordinator/src/agent/mod.rs
coordinator/src/agent/herdr.rs
```

Log with `tracing`. Insert a `coordinator_events` row `kind=started` before listen. Print `proteus listening on {addr}`.

## Coordinator abilities

The coordinator polls Temporal task queue `proteus-coordinator` and provides coding-agent abilities. Components call them through the bus, not by importing the coordinator:

- `ask` answers a question and does not edit files.
- `task` may edit `components/`. The coordinator then versions and swaps.

`GET /__proteus/api/abilities` lists them. The worker is an implementation detail started by the coordinator. `PROTEUS_ABILITY_EXECUTOR=echo` returns the prompt without calling a model. The default executor uses OpenCode at `PROTEUS_MODEL`.

## Global styles

A component registers CSS with `styles.put` or `PUT /proteus/sdk/styles`. The coordinator injects the combined stylesheet, `id="proteus-global-styles"`, into every proxied component page. Introspection does not receive component CSS.

## Out of scope

No authentication. No extra languages. No working Kubernetes deployer. No edits to the introspection UI from the mutator. Do not add a second status bar inside components.
