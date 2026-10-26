use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::Duration;

use axum::Json;
use axum::body::Bytes;
use axum::extract::{Path, Query, Request, State};
use axum::http::{HeaderMap, HeaderValue, StatusCode, header};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Router, body::Body};
use chrono::{DateTime, Utc};
use serde::Deserialize;
use serde_json::{Value, json};
use sqlx::PgPool;
use sqlx::Row;

use crate::agent::AgentDirector;
use crate::config::Config;
use crate::db;
use crate::deploy;
use crate::error::{Error, Result};
use crate::runtime::Runtime;

#[derive(Clone)]
pub struct AppState {
    pub config: Config,
    pub db: PgPool,
    pub runtime: Arc<dyn Runtime>,
    pub director: Arc<dyn AgentDirector>,
    pub http: reqwest::Client,
    pub deploy_lock: Arc<tokio::sync::Mutex<()>>,
}

pub struct FrameState {
    pub revision: i64,
    pub message: String,
}

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/__proteus/health", get(health))
        .route("/__proteus/status", get(status))
        .route("/__proteus/introspect", get(introspect))
        .route("/__proteus/api/components", get(list_components))
        .route(
            "/__proteus/api/components/{name}/versions",
            get(list_versions),
        )
        .route(
            "/__proteus/api/components/{name}/deploy",
            axum::routing::post(deploy_component),
        )
        .route(
            "/__proteus/api/components/{name}/rollback",
            axum::routing::post(rollback_component),
        )
        .route(
            "/__proteus/api/tasks",
            get(list_tasks).post(create_task),
        )
        .route("/__proteus/api/tasks/html", get(tasks_html))
        .route("/__proteus/api/agent/plan", get(agent_plan))
        .route("/__proteus/api/agent/probe", get(agent_probe))
        .route("/__proteus/api/abilities", get(list_abilities))
        .route(
            "/__proteus/api/abilities/invoke",
            axum::routing::post(invoke_ability),
        )
        .route("/__proteus/api/styles", get(list_styles))
        .route("/__proteus/api/events", get(list_events))
        .route("/__proteus/api/calls", get(list_calls))
        .fallback(proxy)
        .layer(middleware::from_fn_with_state(state.clone(), inject_html))
        .with_state(state)
}

async fn health() -> Json<Value> {
    Json(json!({"ok": true, "service": "proteus-coordinator"}))
}

async fn status(State(state): State<AppState>) -> Result<Json<Value>> {
    Ok(Json(status_json(&state).await?))
}

async fn introspect(State(state): State<AppState>) -> Result<Response> {
    let components = component_rows(&state).await?;
    let events = event_rows(&state).await?;
    let calls = call_rows(&state).await?;
    let agent = agent_connection(&state).await;
    let view = crate::introspect::IntrospectView {
        runtime: state.runtime.kind(),
        agent_backend: &state.config.agent_backend,
        agent_kind: &state.config.agent_kind,
        model: state.director.model(),
        temporal_ui: &state.config.temporal_ui_url,
        agent_connection: &agent,
        components: &components,
        events: &events,
        calls: &calls,
    };
    let frame = frame_state(&state).await?;
    let html = crate::introspect::page(&view, &frame);
    Ok(html_response(html))
}

async fn list_components(State(state): State<AppState>) -> Result<Json<Value>> {
    let rows = component_rows(&state).await?;
    let items: Vec<Value> = rows
        .into_iter()
        .map(|row| {
            json!({
                "name": row.name,
                "version": row.version,
                "route_prefix": row.route_prefix,
                "status": row.status,
                "endpoint": row.endpoint,
            })
        })
        .collect();
    Ok(Json(Value::Array(items)))
}

async fn list_versions(
    State(state): State<AppState>,
    Path(name): Path<String>,
) -> Result<Json<Value>> {
    let rows = sqlx::query(
        "SELECT version, source_hash, created_at FROM component_versions WHERE name = $1 ORDER BY version",
    )
    .bind(&name)
    .fetch_all(&state.db)
    .await?;
    let versions: Vec<Value> = rows
        .iter()
        .map(|row| {
            let created: DateTime<Utc> = row.get("created_at");
            json!({
                "version": row.get::<i32, _>("version"),
                "source_hash": row.get::<String, _>("source_hash"),
                "created_at": created.to_rfc3339(),
            })
        })
        .collect();
    Ok(Json(json!({"versions": versions})))
}

#[derive(Deserialize)]
struct DeployBody {
    source: BTreeMap<String, String>,
}

async fn deploy_component(
    State(state): State<AppState>,
    Path(name): Path<String>,
    Json(body): Json<DeployBody>,
) -> Result<Json<Value>> {
    let _guard = state.deploy_lock.lock().await;
    let activated = deploy::deploy_source(&state, &name, body.source).await?;
    Ok(Json(json!({
        "name": activated.name,
        "version": activated.version,
        "status": activated.status,
    })))
}

#[derive(Deserialize)]
struct RollbackBody {
    version: i32,
}

async fn rollback_component(
    State(state): State<AppState>,
    Path(name): Path<String>,
    Json(body): Json<RollbackBody>,
) -> Result<Json<Value>> {
    let _guard = state.deploy_lock.lock().await;
    let activated = deploy::rollback(&state, &name, body.version).await?;
    Ok(Json(json!({
        "name": activated.name,
        "version": activated.version,
        "status": activated.status,
    })))
}

async fn create_task(
    State(state): State<AppState>,
    headers: HeaderMap,
    body: Bytes,
) -> Result<Response> {
    let prompt = parse_prompt(&headers, &body)?;
    if prompt.trim().is_empty() {
        return Err(Error::BadRequest("prompt is required".into()));
    }
    let id = uuid::Uuid::new_v4().to_string();
    sqlx::query("INSERT INTO tasks (id, prompt, status) VALUES ($1, $2, 'queued')")
        .bind(&id)
        .bind(&prompt)
        .execute(&state.db)
        .await?;

    let worker = state.clone();
    let task_id = id.clone();
    let task_prompt = prompt.clone();
    tokio::spawn(async move {
        if let Err(err) = run_task(worker, task_id, task_prompt).await {
            tracing::error!("task failed: {err}");
        }
    });

    if headers
        .get("hx-request")
        .and_then(|v| v.to_str().ok())
        .is_some_and(|v| v == "true")
    {
        return Ok(html_response(task_fragment(&id, "queued", &prompt, "")));
    }
    Ok(Json(json!({"id": id, "status": "queued", "prompt": prompt})).into_response())
}

async fn run_task(state: AppState, id: String, prompt: String) -> Result<()> {
    set_task(&state, &id, "running", None, None).await?;
    match state.director.submit(&prompt).await {
        Ok(output) => {
            let _guard = state.deploy_lock.lock().await;
            if let Err(err) = deploy::sync_all(&state).await {
                record_failure(&state, &id, "mutation", &err.to_string(), Some(output)).await?;
                return Err(err);
            }
            set_task(&state, &id, "done", None, Some(json!({"output": output}))).await?;
        }
        Err(err) => {
            record_failure(&state, &id, "mutation", &err.to_string(), None).await?;
        }
    }
    Ok(())
}

async fn record_failure(
    state: &AppState,
    id: &str,
    kind: &str,
    raw: &str,
    output: Option<String>,
) -> Result<()> {
    let failure = crate::calls::explain(raw);
    let message = if failure.message.is_empty() {
        "call failed with no error output".to_string()
    } else {
        failure.message
    };
    let detail = json!({
        "error": message,
        "code": failure.code,
        "raw": truncate(&failure.raw, 8000),
        "output": output,
    });
    set_task(state, id, "failed", Some(&message), Some(detail.clone())).await?;
    db::insert_event(
        &state.db,
        "call_failed",
        &format!("{kind} failed: {message}"),
        Some(detail),
    )
    .await?;
    tracing::error!("{kind} {id} failed: {message}");
    Ok(())
}

fn truncate(value: &str, max: usize) -> String {
    if value.chars().count() <= max {
        return value.to_string();
    }
    let cut = value.chars().take(max).collect::<String>();
    format!("{cut}…")
}

async fn set_task(
    state: &AppState,
    id: &str,
    status: &str,
    error: Option<&str>,
    detail: Option<Value>,
) -> Result<()> {
    sqlx::query(
        "UPDATE tasks SET status = $2, error = $3, detail = $4, updated_at = now() WHERE id = $1",
    )
    .bind(id)
    .bind(status)
    .bind(error)
    .bind(detail)
    .execute(&state.db)
    .await?;
    Ok(())
}

async fn list_tasks(State(state): State<AppState>) -> Result<Json<Value>> {
    Ok(Json(Value::Array(task_values(&state, None).await?)))
}

async fn tasks_html(State(state): State<AppState>) -> Result<Response> {
    let tasks = task_values(&state, Some("mutation")).await?;
    let mut html = String::from("<div class=\"tasks\">");
    for task in tasks {
        html.push_str(&task_fragment(
            task["id"].as_str().unwrap_or(""),
            task["status"].as_str().unwrap_or(""),
            task["prompt"].as_str().unwrap_or(""),
            task["error"].as_str().unwrap_or(""),
        ));
    }
    html.push_str("</div>");
    Ok(html_response(html))
}

#[derive(Deserialize)]
struct PlanQuery {
    #[serde(default)]
    prompt: String,
}

async fn agent_plan(State(state): State<AppState>, Query(query): Query<PlanQuery>) -> Json<Value> {
    let plan = state.director.plan(&query.prompt);
    Json(json!({
        "backend": plan.backend,
        "kind": plan.kind,
        "model": plan.model,
        "prompt": plan.prompt,
        "commands": plan.commands.into_iter().map(|cmd| json!({"argv": cmd.argv})).collect::<Vec<_>>(),
    }))
}

async fn agent_probe(State(state): State<AppState>) -> Json<Value> {
    match state.director.probe().await {
        Ok(value) => Json(value),
        Err(err) => Json(json!({"ok": false, "error": err.to_string()})),
    }
}

async fn list_abilities(State(state): State<AppState>) -> Json<Value> {
    let mut body = crate::abilities::catalog();
    body["model"] = json!(state.director.model());
    body["executor"] = json!(state.config.ability_executor);
    Json(body)
}

#[derive(Deserialize)]
struct InvokeBody {
    name: String,
    #[serde(default)]
    arg: Value,
}

async fn invoke_ability(
    State(state): State<AppState>,
    Json(body): Json<InvokeBody>,
) -> Result<Json<Value>> {
    let result = crate::abilities::invoke(&state, &body.name, &body.arg).await?;
    Ok(Json(json!({"result": result})))
}

async fn list_styles(State(state): State<AppState>) -> Result<Json<Value>> {
    let rows = sqlx::query("SELECT component, css FROM global_styles ORDER BY component")
        .fetch_all(&state.db)
        .await?;
    let styles: Vec<Value> = rows
        .iter()
        .map(|row| {
            json!({
                "component": row.get::<String, _>("component"),
                "css": row.get::<String, _>("css"),
            })
        })
        .collect();
    Ok(Json(json!({"styles": styles})))
}

async fn list_calls(State(state): State<AppState>) -> Result<Json<Value>> {
    let calls = call_rows(&state).await?;
    let items: Vec<Value> = calls
        .into_iter()
        .map(|call| {
            json!({
                "id": call.id,
                "kind": call.kind,
                "status": call.status,
                "prompt": call.prompt,
                "error": call.error,
                "created_at": call.created_at,
                "updated_at": call.updated_at,
            })
        })
        .collect();
    Ok(Json(json!({"calls": items})))
}

async fn agent_connection(state: &AppState) -> String {
    match tokio::time::timeout(Duration::from_secs(3), state.director.probe()).await {
        Ok(Ok(value)) => {
            if value.get("ok").and_then(Value::as_bool) == Some(true) {
                "connected".into()
            } else {
                value
                    .get("error")
                    .and_then(Value::as_str)
                    .unwrap_or("unreachable")
                    .to_string()
            }
        }
        Ok(Err(err)) => crate::calls::explain(&err.to_string()).message,
        Err(_) => "probe timed out".into(),
    }
}

async fn list_events(State(state): State<AppState>) -> Result<Json<Value>> {
    let events = event_rows(&state).await?;
    let items: Vec<Value> = events
        .into_iter()
        .map(|event| {
            json!({
                "id": event.id,
                "kind": event.kind,
                "message": event.message,
                "created_at": event.created_at,
            })
        })
        .collect();
    Ok(Json(json!({"events": items})))
}

async fn proxy(State(state): State<AppState>, req: Request) -> Response {
    match forward(&state, req).await {
        Ok(response) => response,
        Err(err) => {
            let status = match &err {
                Error::NotFound(_) => StatusCode::NOT_FOUND,
                Error::BadRequest(_) => StatusCode::BAD_REQUEST,
                _ => StatusCode::BAD_GATEWAY,
            };
            (status, Json(json!({"error": err.to_string()}))).into_response()
        }
    }
}

async fn forward(state: &AppState, req: Request) -> Result<Response> {
    let path = req.uri().path().to_string();
    if path == "/__proteus" || path.starts_with("/__proteus/") {
        return Ok((
            StatusCode::NOT_FOUND,
            Json(json!({"error": "not found"})),
        )
            .into_response());
    }
    let query = req.uri().query().map(str::to_string);
    let method = req.method().clone();
    let headers = req.headers().clone();
    let body = axum::body::to_bytes(req.into_body(), 16 * 1024 * 1024)
        .await
        .map_err(Error::msg)?;

    let components = component_rows(state).await?;
    let matched = components
        .iter()
        .filter(|item| item.status == "running" && prefix_matches(&path, &item.route_prefix))
        .max_by_key(|item| item.route_prefix.len());
    let Some(component) = matched else {
        return Err(Error::NotFound("no component is routed for this path".into()));
    };
    if component.endpoint.is_empty() {
        return Err(Error::Message(format!("{} has no endpoint", component.name)));
    }
    let stripped = strip_prefix(&path, &component.route_prefix);
    let mut url = format!("{}{stripped}", component.endpoint.trim_end_matches('/'));
    if let Some(query) = query {
        url.push('?');
        url.push_str(&query);
    }

    let mut builder = state.http.request(method, &url);
    for (key, value) in headers.iter() {
        let name = key.as_str();
        if matches!(
            name,
            "host" | "connection" | "keep-alive" | "proxy-authenticate" | "proxy-authorization"
                | "te" | "trailers" | "transfer-encoding" | "upgrade" | "content-length"
        ) {
            continue;
        }
        builder = builder.header(key, value);
    }
    builder = builder.header(header::ACCEPT_ENCODING, "identity");
    if !body.is_empty() {
        builder = builder.body(body);
    }
    let upstream = builder.send().await?;
    let status = upstream.status();
    let content_type = upstream
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("application/octet-stream")
        .to_string();
    let bytes = upstream.bytes().await?;
    let body = bytes.to_vec();
    Ok(Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, content_type)
        .header(header::CONTENT_LENGTH, body.len())
        .body(Body::from(body))
        .map_err(Error::msg)?)
}

fn prefix_matches(path: &str, prefix: &str) -> bool {
    if prefix == "/" {
        return true;
    }
    path == prefix || path.starts_with(&format!("{prefix}/"))
}

fn strip_prefix(path: &str, prefix: &str) -> String {
    if prefix == "/" {
        return if path.is_empty() {
            "/".into()
        } else {
            path.to_string()
        };
    }
    let rest = path.strip_prefix(prefix).unwrap_or(path);
    if rest.is_empty() { "/".into() } else { rest.to_string() }
}

async fn global_css(state: &AppState) -> Result<String> {
    let rows = sqlx::query("SELECT component, css FROM global_styles ORDER BY component")
        .fetch_all(&state.db)
        .await?;
    let mut css = String::new();
    for row in rows {
        let component: String = row.get("component");
        let body: String = row.get("css");
        css.push_str(&format!("/* proteus:{component} */\n{}\n", sanitize_css(&body)));
    }
    Ok(css)
}

fn sanitize_css(css: &str) -> String {
    css.replace("</", "<\\/")
}

fn inject_styles(html: &str, css: &str) -> String {
    if css.trim().is_empty() || html.contains("id=\"proteus-global-styles\"") {
        return html.to_string();
    }
    let snippet = format!(r#"<style id="proteus-global-styles">{css}</style>"#);
    if let Some(idx) = html.rfind("</head>") {
        let mut out = String::with_capacity(html.len() + snippet.len());
        out.push_str(&html[..idx]);
        out.push_str(&snippet);
        out.push_str(&html[idx..]);
        out
    } else {
        format!("{snippet}{html}")
    }
}

pub fn inject_frame(html: &str, frame: &FrameState) -> String {
    if html.contains("id=\"proteus-status-frame\"") {
        return html.to_string();
    }
    let snippet = frame_html(frame);
    if let Some(idx) = html.rfind("</body>") {
        let mut out = String::with_capacity(html.len() + snippet.len());
        out.push_str(&html[..idx]);
        out.push_str(&snippet);
        out.push_str(&html[idx..]);
        out
    } else {
        format!("{html}{snippet}")
    }
}

fn frame_html(frame: &FrameState) -> String {
    format!(
        r#"<div id="proteus-status-frame" data-revision="{revision}">
  <div class="proteus-frame-row">
    <strong>proteus</strong>
    <span id="proteus-frame-message">{message}</span>
    <button type="button" id="proteus-frame-refresh" hidden>Refresh</button>
  </div>
</div>
<script id="proteus-status-script">
(function() {{
  var frame = document.getElementById('proteus-status-frame');
  var msg = document.getElementById('proteus-frame-message');
  var btn = document.getElementById('proteus-frame-refresh');
  var seen = frame.getAttribute('data-revision');
  btn.addEventListener('click', function() {{ location.reload(); }});
  async function tick() {{
    try {{
      var res = await fetch('/__proteus/status', {{headers: {{'accept': 'application/json'}}}});
      var data = await res.json();
      var rev = String(data.revision || 0);
      var text = (data.latest_event && data.latest_event.message) || (data.busy ? 'working' : 'idle');
      if (rev !== String(seen)) {{
        btn.hidden = false;
        msg.textContent = text + ' — update published';
      }} else {{
        msg.textContent = text;
      }}
    }} catch (e) {{}}
  }}
  setInterval(tick, 2000);
  tick();
}})();
</script>
<style>
#proteus-status-frame {{ position: fixed; left: 0; right: 0; bottom: 0; z-index: 2147483647; background: #111; color: #f5f5f4; font: 13px/1.4 ui-sans-serif, system-ui; border-top: 1px solid #333; padding: 8px 12px; }}
#proteus-frame-refresh {{ margin-left: 12px; }}
.proteus-frame-row {{ display: flex; gap: 12px; align-items: center; }}
</style>"#,
        revision = frame.revision,
        message = esc(&frame.message),
    )
}

fn html_response(html: String) -> Response {
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "text/html; charset=utf-8")
        .body(Body::from(html))
        .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
}

fn parse_prompt(headers: &HeaderMap, body: &Bytes) -> Result<String> {
    let content_type = headers
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    if content_type.contains("application/json") || body.first() == Some(&b'{') {
        let value: Value = serde_json::from_slice(body)?;
        return Ok(value
            .get("prompt")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string());
    }
    let text = String::from_utf8_lossy(body);
    for pair in text.split('&') {
        let mut parts = pair.splitn(2, '=');
        let key = parts.next().unwrap_or("");
        let value = parts.next().unwrap_or("");
        if key == "prompt" {
            return Ok(urlencoding_decode(value));
        }
    }
    Err(Error::BadRequest("prompt is required".into()))
}

fn urlencoding_decode(value: &str) -> String {
    let replaced = value.replace('+', " ");
    percent_decode(&replaced)
}

fn percent_decode(value: &str) -> String {
    let bytes = value.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let Ok(hex) = u8::from_str_radix(&value[i + 1..i + 3], 16) {
                out.push(hex);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn task_fragment(id: &str, status: &str, prompt: &str, error: &str) -> String {
    let reason = if error.is_empty() {
        String::new()
    } else {
        format!("<p class=\"task-error\">{}</p>", esc(error))
    };
    format!(
        "<div class=\"task\" data-task-id=\"{}\"><strong>{}</strong> {}{reason}</div>",
        esc(id),
        esc(status),
        esc(prompt)
    )
}

fn esc(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

async fn status_json(state: &AppState) -> Result<Value> {
    let frame = frame_state(state).await?;
    let event = latest_event(state).await?;
    let components = component_rows(state).await?;
    let busy = sqlx::query(
        "SELECT EXISTS(SELECT 1 FROM tasks WHERE status IN ('queued', 'running')) AS busy",
    )
    .fetch_one(&state.db)
    .await?
    .get::<bool, _>("busy");
    Ok(json!({
        "ok": true,
        "revision": frame.revision,
        "runtime": state.runtime.kind(),
        "agent_backend": state.director.kind(),
        "agent_kind": state.config.agent_kind,
        "model": state.director.model(),
        "busy": busy,
        "latest_event": event,
        "components": components.into_iter().map(|item| json!({
            "name": item.name,
            "version": item.version,
            "route_prefix": item.route_prefix,
            "status": item.status,
            "endpoint": item.endpoint,
        })).collect::<Vec<_>>(),
    }))
}

async fn frame_state(state: &AppState) -> Result<FrameState> {
    let row = sqlx::query(
        "SELECT id, message FROM coordinator_events ORDER BY id DESC LIMIT 1",
    )
    .fetch_optional(&state.db)
    .await?;
    Ok(match row {
        Some(row) => FrameState {
            revision: row.get("id"),
            message: row.get("message"),
        },
        None => FrameState {
            revision: 0,
            message: "idle".into(),
        },
    })
}

async fn latest_event(state: &AppState) -> Result<Option<Value>> {
    let row = sqlx::query(
        "SELECT id, kind, message, created_at FROM coordinator_events ORDER BY id DESC LIMIT 1",
    )
    .fetch_optional(&state.db)
    .await?;
    Ok(row.map(|row| {
        let created: DateTime<Utc> = row.get("created_at");
        json!({
            "id": row.get::<i64, _>("id"),
            "kind": row.get::<String, _>("kind"),
            "message": row.get::<String, _>("message"),
            "created_at": created.to_rfc3339(),
        })
    }))
}

async fn component_rows(state: &AppState) -> Result<Vec<crate::introspect::ComponentRow>> {
    let rows = sqlx::query(
        "SELECT name, current_version, status, route_prefix, endpoint FROM components ORDER BY name",
    )
    .fetch_all(&state.db)
    .await?;
    Ok(rows
        .into_iter()
        .map(|row| crate::introspect::ComponentRow {
            name: row.get("name"),
            version: row.get::<Option<i32>, _>("current_version").unwrap_or(0),
            status: row.get("status"),
            route_prefix: row.get("route_prefix"),
            endpoint: row.get::<Option<String>, _>("endpoint").unwrap_or_default(),
        })
        .collect())
}

async fn event_rows(state: &AppState) -> Result<Vec<crate::introspect::EventRow>> {
    let rows = sqlx::query(
        "SELECT id, kind, message, created_at FROM coordinator_events ORDER BY id DESC LIMIT 20",
    )
    .fetch_all(&state.db)
    .await?;
    Ok(rows
        .into_iter()
        .map(|row| {
            let created: DateTime<Utc> = row.get("created_at");
            crate::introspect::EventRow {
                id: row.get("id"),
                kind: row.get("kind"),
                message: row.get("message"),
                created_at: created.to_rfc3339(),
            }
        })
        .collect())
}

async fn task_values(state: &AppState, kind: Option<&str>) -> Result<Vec<Value>> {
    let rows = sqlx::query(
        "SELECT id, kind, prompt, status, error, detail, created_at, updated_at
         FROM tasks
         WHERE $1::text IS NULL OR kind = $1
         ORDER BY created_at DESC
         LIMIT 40",
    )
    .bind(kind)
    .fetch_all(&state.db)
    .await?;
    Ok(rows
        .into_iter()
        .map(|row| {
            let created: DateTime<Utc> = row.get("created_at");
            let updated: DateTime<Utc> = row.get("updated_at");
            let detail: Option<Value> = row.get("detail");
            let stored: Option<String> = row.get("error");
            json!({
                "id": row.get::<String, _>("id"),
                "kind": row.get::<String, _>("kind"),
                "prompt": row.get::<String, _>("prompt"),
                "status": row.get::<String, _>("status"),
                "error": crate::calls::reason_from(stored.as_deref(), detail.as_ref()),
                "created_at": created.to_rfc3339(),
                "updated_at": updated.to_rfc3339(),
            })
        })
        .collect())
}

async fn call_rows(state: &AppState) -> Result<Vec<crate::introspect::CallRow>> {
    let rows = sqlx::query(
        "SELECT id, kind, prompt, status, error, detail, created_at, updated_at
         FROM tasks
         ORDER BY created_at DESC
         LIMIT 40",
    )
    .fetch_all(&state.db)
    .await?;
    Ok(rows
        .into_iter()
        .map(|row| {
            let created: DateTime<Utc> = row.get("created_at");
            let updated: DateTime<Utc> = row.get("updated_at");
            let detail: Option<Value> = row.get("detail");
            let stored: Option<String> = row.get("error");
            let raw = detail
                .as_ref()
                .and_then(|value| value.get("raw").or_else(|| value.get("error")))
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            crate::introspect::CallRow {
                id: row.get("id"),
                kind: row.get("kind"),
                prompt: row.get("prompt"),
                status: row.get("status"),
                error: crate::calls::reason_from(stored.as_deref(), detail.as_ref()),
                raw: truncate(&raw, 4000),
                created_at: created.to_rfc3339(),
                updated_at: updated.to_rfc3339(),
            }
        })
        .collect())
}

pub fn http_client() -> Result<reqwest::Client> {
    Ok(reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(180))
        .build()?)
}

async fn inject_html(State(state): State<AppState>, request: Request, next: Next) -> Response {
    let path = request.uri().path().to_string();
    let response = next.run(request).await;
    let content_type = response
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("")
        .to_ascii_lowercase();
    if !content_type.contains("text/html") {
        return response;
    }
    let (parts, body) = response.into_parts();
    let Ok(bytes) = axum::body::to_bytes(body, 16 * 1024 * 1024).await else {
        return StatusCode::BAD_GATEWAY.into_response();
    };
    let html = String::from_utf8_lossy(&bytes);
    if html.contains("id=\"proteus-status-frame\"") {
        return Response::from_parts(parts, Body::from(bytes));
    }
    let Ok(frame) = frame_state(&state).await else {
        return Response::from_parts(parts, Body::from(bytes));
    };
    let mut html = html.to_string();
    if !path.starts_with("/__proteus") {
        if let Ok(css) = global_css(&state).await {
            html = inject_styles(&html, &css);
        }
    }
    let injected = inject_frame(&html, &frame);
    let mut response = Response::from_parts(parts, Body::from(injected.clone()));
    if let Ok(len) = HeaderValue::from_str(&injected.len().to_string()) {
        response.headers_mut().insert(header::CONTENT_LENGTH, len);
    }
    response.headers_mut().remove(header::TRANSFER_ENCODING);
    response
}
