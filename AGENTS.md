# Proteus

Polymorphic app. A Rust coordinator deploys interchangeable Python components and wires them with Temporal.

When changing the running app through the prompt UI:

- Only create or edit files under `components/`.
- Do not modify `coordinator/`, `python/proteus`, `deploy/`, `tests/`, or the introspection UI.
- The introspection UI is compiled into the coordinator and cannot be swapped out.
- Components are Python + `proteus.toml`. UIs use HTMX. Do not add a bottom status bar; the coordinator injects one.
- Follow `SPEC.md` for the component contract.
