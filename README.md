# Proteus

A self-mutating app. You talk to a prompt. A Rust coordinator versions Python components, builds them into containers, and swaps them into the live route table. Temporal carries calls between components. An introspection UI, compiled into the coordinator, cannot be swapped away.

## Architecture

```
browser
  │
  ▼
Rust coordinator  ── Runtime trait ── DockerRuntime (KubernetesRuntime is a seam)
  │                 ── AgentDirector ── HerdrDirector → OpenCode (default xai/grok-4.7)
  │
  ├─ /                 bootstrap component (Python + HTMX)
  ├─ /echo             echo component (activity + workflow)
  └─ /__proteus/*      coordinator itself (status, deploy, introspection)
          │
          ├── postgres   versions, events, schemaless JSONB store
          ├── redis      cache
          └── temporal   system-to-system activities and workflows
```

The coordinator is the only public HTTP process. It injects a fixed status frame into every HTML page. When a deploy publishes a new revision, the frame shows it and offers Refresh. It does not reload on its own.

Component execution is behind `Runtime` in `coordinator/src/runtime`. Moving to Kubernetes means a new implementation of that trait, not a change to routing or versioning. The coordinator itself can run on the host (default, so it can see Docker and Herdr) or from `deploy/coordinator.Dockerfile`.

## Run

```bash
docker compose up -d
cargo run --manifest-path coordinator/Cargo.toml
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080). Introspection is [http://127.0.0.1:8080/__proteus/introspect](http://127.0.0.1:8080/__proteus/introspect). Temporal UI is [http://127.0.0.1:8233](http://127.0.0.1:8233).

Start the coordinator from a Herdr pane if you want the prompt UI to direct OpenCode. Direct deploys (`POST /__proteus/api/components/{name}/deploy`) do not need an agent.

## Configuration

See `.env.example`. The mutator model defaults to `xai/grok-4.7`. Override with `PROTEUS_MODEL`. Runtime defaults to `docker`.

## Components

A component is a directory of Python plus `proteus.toml`:

```toml
name = "notes"
route_prefix = "/notes"
autostart = true
```

`app.py` exposes `setup()` returning a `proteus.Component`. Optional `activities.py` and `workflows.py` are registered on task queue `proteus-{name}`. Other components call them through the Temporal bus.

The coordinator also polls `proteus-coordinator` and exposes two coding-agent abilities, `ask` and `task`. Components reach them only through Temporal:

```python
from proteus import abilities, styles

await abilities.ask("What does the echo component return?")
await abilities.task("Add a notes list to the bootstrap page")
styles.put("body { font-family: Georgia, serif; }")
```

`ask` is a question. It does not edit files. `task` may update `components/`; the coordinator then versions and swaps. Both default to OpenCode with `xai/grok-4.7`. Set `PROTEUS_ABILITY_EXECUTOR=echo` to skip the model and return the prompt. Registered CSS is injected into every component page, not into the immutable introspection UI.

If Herdr or OpenCode drops the session, the coordinator drops the dead pane and retries the connection once. A mutation stays running while the agent status is `working`; it is not failed for taking a long time. Every mutation, `ask`, and `task` is listed on the introspection page with its status and, on failure, the reason.

Every deployed source snapshot is kept in `component_versions`. Rollback restores an old snapshot and swaps the container.

## Tests

```bash
./scripts/e2e.sh
```

Scenarios live in `tests/e2e/run.py`.
