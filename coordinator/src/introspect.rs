use crate::http::FrameState;

pub struct IntrospectView<'a> {
    pub runtime: &'a str,
    pub agent_backend: &'a str,
    pub agent_kind: &'a str,
    pub model: &'a str,
    pub temporal_ui: &'a str,
    pub agent_connection: &'a str,
    pub components: &'a [ComponentRow],
    pub events: &'a [EventRow],
    pub calls: &'a [CallRow],
}

pub struct ComponentRow {
    pub name: String,
    pub version: i32,
    pub status: String,
    pub route_prefix: String,
    pub endpoint: String,
}

pub struct EventRow {
    pub id: i64,
    pub kind: String,
    pub message: String,
    pub created_at: String,
}

pub struct CallRow {
    pub id: String,
    pub kind: String,
    pub prompt: String,
    pub status: String,
    pub error: String,
    pub raw: String,
    pub created_at: String,
    pub updated_at: String,
}

pub fn page(view: &IntrospectView, frame: &FrameState) -> String {
    let mut components = String::new();
    for item in view.components {
        components.push_str(&format!(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>",
            esc(&item.name),
            item.version,
            esc(&item.status),
            esc(&item.route_prefix),
            esc(&item.endpoint)
        ));
    }
    let mut events = String::new();
    for item in view.events {
        events.push_str(&format!(
            "<li><code>{}</code> {} — {}</li>",
            item.id,
            esc(&item.kind),
            esc(&item.message)
        ));
    }
    let mut calls = String::new();
    if view.calls.is_empty() {
        calls.push_str("<tr><td colspan=\"5\">No calls yet.</td></tr>");
    }
    for item in view.calls {
        let reason = if item.error.is_empty() {
            "—".to_string()
        } else {
            let raw = if item.raw.is_empty() {
                String::new()
            } else {
                format!(
                    "<details><summary>raw</summary><pre>{}</pre></details>",
                    esc(&item.raw)
                )
            };
            format!("{}{raw}", esc(&item.error))
        };
        calls.push_str(&format!(
            "<tr data-call-id=\"{}\" data-call-status=\"{}\"><td>{}</td><td>{}</td><td class=\"{}\">{}</td><td>{}</td><td>{}</td></tr>",
            esc(&item.id),
            esc(&item.status),
            esc(&item.updated_at),
            esc(&item.kind),
            esc(&item.status),
            esc(&item.status),
            esc(&item.prompt),
            reason
        ));
    }
    let html = format!(
        r#"<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Proteus introspection</title>
<style>
  body {{ font: 15px/1.45 ui-sans-serif, system-ui; margin: 2rem; padding-bottom: 4rem; background: #101218; color: #f4f1ea; }}
  a {{ color: #f0d7a4; }}
  table {{ border-collapse: collapse; width: 100%; }}
  td, th {{ border-bottom: 1px solid #2c3140; text-align: left; padding: 0.4rem 0.6rem; }}
  .banner {{ border: 1px solid #8a6a32; background: #2a2114; padding: 0.8rem 1rem; border-radius: 8px; }}
  .failed {{ color: #f0a8a0; }}
  pre {{ white-space: pre-wrap; max-height: 16rem; overflow: auto; background: #0c0e14; padding: 0.6rem; }}
  td {{ vertical-align: top; }}
</style>
</head>
<body data-proteus-introspect="1">
<main>
  <h1>Introspection</h1>
  <p class="banner">This UI is immutable. It is compiled into the coordinator and cannot be swapped by component deploys.</p>
  <p>Runtime <strong>{runtime}</strong>. Agent backend <strong>{backend}</strong> via <strong>{kind}</strong>. Model <strong>{model}</strong>.</p>
  <p>Herdr connection: <strong>{agent}</strong></p>
  <p><a href="/">Bootstrap</a> · <a href="{temporal}">{temporal}</a></p>
  <h2>Calls</h2>
  <section data-proteus-calls="1">
  <p>Each mutation and coding-agent call. A failure includes the reason Herdr or OpenCode returned.</p>
  <table>
    <thead><tr><th>when</th><th>kind</th><th>status</th><th>prompt</th><th>reason</th></tr></thead>
    <tbody>{calls}</tbody>
  </table>
  </section>
  <h2>Components</h2>
  <table>
    <thead><tr><th>name</th><th>version</th><th>status</th><th>route prefix</th><th>endpoint</th></tr></thead>
    <tbody>{components}</tbody>
  </table>
  <h2>Events</h2>
  <ul>{events}</ul>
</main>
</body>
</html>"#,
        runtime = esc(view.runtime),
        backend = esc(view.agent_backend),
        kind = esc(view.agent_kind),
        model = esc(view.model),
        temporal = esc(view.temporal_ui),
        agent = esc(view.agent_connection),
        calls = calls,
        components = components,
        events = events,
    );
    crate::http::inject_frame(&html, frame)
}

fn esc(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}
