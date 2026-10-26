use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::Deserialize;
use serde_json::json;
use sqlx::Row;
use sha2::{Digest, Sha256};

use crate::db;
use crate::error::{Error, Result};
use crate::http::AppState;
use crate::runtime::StartSpec;

#[derive(Clone, Debug, Deserialize)]
struct ComponentToml {
    name: String,
    route_prefix: String,
    #[serde(default)]
    autostart: bool,
    #[serde(default)]
    description: String,
}

#[derive(Clone, Debug)]
pub struct Activated {
    pub name: String,
    pub version: i32,
    pub status: String,
}

pub fn source_hash(files: &BTreeMap<String, String>) -> String {
    let mut hasher = Sha256::new();
    for (path, content) in files {
        hasher.update(path.as_bytes());
        hasher.update([0]);
        hasher.update(content.as_bytes());
        hasher.update([0]);
    }
    hex::encode(hasher.finalize())
}

pub async fn sync_autostart(state: &AppState) -> Result<()> {
    let mut deployed_bootstrap = false;
    for (_dir, source) in read_component_tree(&state.config.components_dir)? {
        let toml = parse_toml(&source)?;
        if !toml.autostart {
            continue;
        }
        if toml.name == state.config.bootstrap_component {
            deployed_bootstrap = true;
        }
        if let Err(err) = activate(state, &toml.name, source, true).await {
            if toml.name == state.config.bootstrap_component {
                return Err(Error::msg(format!(
                    "bootstrap component {} failed: {err}",
                    toml.name
                )));
            }
            return Err(err);
        }
    }
    if !deployed_bootstrap {
        return Err(Error::msg(format!(
            "bootstrap component {} was not deployed",
            state.config.bootstrap_component
        )));
    }
    Ok(())
}

pub async fn sync_all(state: &AppState) -> Result<()> {
    for (_dir, source) in read_component_tree(&state.config.components_dir)? {
        let toml = parse_toml(&source)?;
        activate(state, &toml.name, source, true).await?;
    }
    Ok(())
}

pub async fn deploy_source(
    state: &AppState,
    name: &str,
    source: BTreeMap<String, String>,
) -> Result<Activated> {
    if !source.contains_key("proteus.toml") || !source.contains_key("app.py") {
        return Err(Error::BadRequest(
            "source must include proteus.toml and app.py".into(),
        ));
    }
    let toml = parse_toml(&source)?;
    if toml.name != name {
        return Err(Error::BadRequest(format!(
            "proteus.toml name {} does not match {name}",
            toml.name
        )));
    }
    write_component_dir(&state.config.components_dir.join(name), &source)?;
    activate(state, name, source, true).await
}

pub async fn rollback(state: &AppState, name: &str, version: i32) -> Result<Activated> {
    let row = sqlx::query("SELECT source FROM component_versions WHERE name = $1 AND version = $2")
        .bind(name)
        .bind(version)
        .fetch_optional(&state.db)
        .await?;
    let Some(row) = row else {
        return Err(Error::NotFound(format!("{name} version {version} not found")));
    };
    let source: sqlx::types::Json<BTreeMap<String, String>> = row.try_get("source")?;
    write_component_dir(&state.config.components_dir.join(name), &source.0)?;
    activate(state, name, source.0, true).await
}

async fn activate(
    state: &AppState,
    name: &str,
    source: BTreeMap<String, String>,
    announce_reuse: bool,
) -> Result<Activated> {
    let toml = parse_toml(&source)?;
    if !toml.route_prefix.starts_with('/') {
        return Err(Error::BadRequest("route_prefix must start with /".into()));
    }
    let hash = source_hash(&source);
    let config = json!({
        "name": toml.name,
        "route_prefix": toml.route_prefix,
        "autostart": toml.autostart,
        "description": toml.description,
    });

    let existing = sqlx::query(
        "SELECT version FROM component_versions WHERE name = $1 AND source_hash = $2",
    )
    .bind(name)
    .bind(&hash)
    .fetch_optional(&state.db)
    .await?;

    let version: i32 = if let Some(row) = existing {
        row.try_get("version")?
    } else {
        let row = sqlx::query(
            "SELECT (COALESCE(MAX(version), 0) + 1)::int AS version FROM component_versions WHERE name = $1",
        )
        .bind(name)
        .fetch_one(&state.db)
        .await?;
        let version: i32 = row.try_get("version")?;
        sqlx::query(
            "INSERT INTO component_versions (name, version, source, config, source_hash) VALUES ($1, $2, $3, $4, $5)",
        )
        .bind(name)
        .bind(version)
        .bind(sqlx::types::Json(&source))
        .bind(sqlx::types::Json(&config))
        .bind(&hash)
        .execute(&state.db)
        .await?;
        version
    };

    let image = format!("proteus/{name}:{version}");
    let context = write_build_context(
        &state.config.root,
        name,
        version,
        &source,
        &state.config.base_image,
    )?;
    state.runtime.build_component(&image, &context).await?;
    sqlx::query("UPDATE component_versions SET image_ref = $1 WHERE name = $2 AND version = $3")
        .bind(&image)
        .bind(name)
        .bind(version)
        .execute(&state.db)
        .await?;

    let running = state
        .runtime
        .list()
        .await?
        .into_iter()
        .find(|svc| svc.name == name && svc.version == version);
    if let Some(service) = running {
        let current = state
            .runtime
            .running_image_matches(&service.runtime_id, &image)
            .await
            .unwrap_or(false);
        if current && wait_healthy_for(&service.endpoint, Duration::from_secs(5)).await {
            upsert_component(
                state,
                name,
                version,
                &toml.route_prefix,
                &service.endpoint,
                &service.runtime_id,
                &image,
            )
            .await?;
            stop_other_versions(state, name, version).await?;
            if announce_reuse {
                db::insert_event(
                    &state.db,
                    "swapped",
                    &format!("{name} is now version {version}"),
                    Some(json!({"name": name, "version": version, "reused": true})),
                )
                .await?;
            }
            return Ok(Activated {
                name: name.to_string(),
                version,
                status: "running".into(),
            });
        }
        state.runtime.stop(&service.runtime_id).await?;
    }

    let started = state
        .runtime
        .start(&StartSpec {
            name: name.to_string(),
            version,
            image: image.clone(),
            network: state.config.docker_network.clone(),
            container_port: 8000,
            command: Vec::new(),
            http_health: true,
            env: component_env(state, name, version, &toml.route_prefix),
        })
        .await?;

    if !wait_healthy(&started.endpoint).await {
        let _ = state.runtime.stop(&started.runtime_id).await;
        return Err(Error::Message(format!(
            "{name} version {version} did not become healthy at {}",
            started.endpoint
        )));
    }

    upsert_component(
        state,
        name,
        version,
        &toml.route_prefix,
        &started.endpoint,
        &started.runtime_id,
        &image,
    )
    .await?;
    stop_other_versions(state, name, version).await?;
    db::insert_event(
        &state.db,
        "swapped",
        &format!("{name} is now version {version}"),
        Some(json!({"name": name, "version": version})),
    )
    .await?;

    Ok(Activated {
        name: name.to_string(),
        version,
        status: "running".into(),
    })
}

fn component_env(state: &AppState, name: &str, version: i32, prefix: &str) -> Vec<(String, String)> {
    vec![
        (
            "DATABASE_URL".into(),
            state.config.component_database_url.clone(),
        ),
        ("REDIS_URL".into(), state.config.component_redis_url.clone()),
        (
            "TEMPORAL_ADDRESS".into(),
            state.config.component_temporal_address.clone(),
        ),
        (
            "TEMPORAL_NAMESPACE".into(),
            state.config.temporal_namespace.clone(),
        ),
        ("PROTEUS_COMPONENT_NAME".into(), name.to_string()),
        ("PROTEUS_COMPONENT_DIR".into(), "/app/component".into()),
        ("PROTEUS_ROUTE_PREFIX".into(), prefix.to_string()),
        ("PROTEUS_VERSION".into(), version.to_string()),
    ]
}

async fn upsert_component(
    state: &AppState,
    name: &str,
    version: i32,
    prefix: &str,
    endpoint: &str,
    runtime_id: &str,
    image: &str,
) -> Result<()> {
    sqlx::query(
        "INSERT INTO components (name, current_version, status, route_prefix, endpoint, runtime_id, image_ref, updated_at)
         VALUES ($1, $2, 'running', $3, $4, $5, $6, now())
         ON CONFLICT (name) DO UPDATE SET
           current_version = EXCLUDED.current_version,
           status = 'running',
           route_prefix = EXCLUDED.route_prefix,
           endpoint = EXCLUDED.endpoint,
           runtime_id = EXCLUDED.runtime_id,
           image_ref = EXCLUDED.image_ref,
           updated_at = now()",
    )
    .bind(name)
    .bind(version)
    .bind(prefix)
    .bind(endpoint)
    .bind(runtime_id)
    .bind(image)
    .execute(&state.db)
    .await?;
    Ok(())
}

async fn stop_other_versions(state: &AppState, name: &str, version: i32) -> Result<()> {
    for service in state.runtime.list().await? {
        if service.name == name && service.version != version {
            if let Err(err) = state.runtime.stop(&service.runtime_id).await {
                tracing::warn!(
                    error = %err,
                    runtime_id = %service.runtime_id,
                    "failed to stop previous container"
                );
            }
        }
    }
    Ok(())
}

async fn wait_healthy(endpoint: &str) -> bool {
    wait_healthy_for(endpoint, Duration::from_secs(60)).await
}

async fn wait_healthy_for(endpoint: &str, budget: Duration) -> bool {
    let deadline = tokio::time::Instant::now() + budget;
    while tokio::time::Instant::now() < deadline {
        if health(endpoint).await {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(400)).await;
    }
    false
}

async fn health(endpoint: &str) -> bool {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(2))
        .build();
    let Ok(client) = client else {
        return false;
    };
    client
        .get(format!("{endpoint}/health"))
        .send()
        .await
        .map(|res| res.status().as_u16() == 200)
        .unwrap_or(false)
}

fn parse_toml(source: &BTreeMap<String, String>) -> Result<ComponentToml> {
    let text = source
        .get("proteus.toml")
        .ok_or_else(|| Error::BadRequest("missing proteus.toml".into()))?;
    Ok(toml::from_str(text)?)
}

fn read_component_tree(dir: &Path) -> Result<Vec<(String, BTreeMap<String, String>)>> {
    if !dir.is_dir() {
        return Ok(Vec::new());
    }
    let mut found = Vec::new();
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let path = entry.path();
        if !path.is_dir() || !path.join("proteus.toml").is_file() {
            continue;
        }
        let name = entry.file_name().to_string_lossy().to_string();
        found.push((name, read_source(&path)?));
    }
    found.sort_by(|a, b| a.0.cmp(&b.0));
    Ok(found)
}

fn read_source(dir: &Path) -> Result<BTreeMap<String, String>> {
    let mut files = BTreeMap::new();
    read_source_into(dir, dir, &mut files)?;
    Ok(files)
}

fn read_source_into(root: &Path, dir: &Path, files: &mut BTreeMap<String, String>) -> Result<()> {
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();
        if name.starts_with('.') || name == "__pycache__" || name.ends_with(".pyc") {
            continue;
        }
        if path.is_dir() {
            read_source_into(root, &path, files)?;
            continue;
        }
        if !path.is_file() {
            continue;
        }
        let rel = path
            .strip_prefix(root)
            .unwrap_or(&path)
            .components()
            .map(|c| c.as_os_str().to_string_lossy().to_string())
            .collect::<Vec<_>>()
            .join("/");
        files.insert(rel, fs::read_to_string(&path)?);
    }
    Ok(())
}

fn write_component_dir(dir: &Path, source: &BTreeMap<String, String>) -> Result<()> {
    if dir.exists() {
        fs::remove_dir_all(dir)?;
    }
    fs::create_dir_all(dir)?;
    for (path, content) in source {
        if path.contains("..") || Path::new(path).is_absolute() {
            return Err(Error::BadRequest(format!("unsafe source path {path}")));
        }
        let full = dir.join(path);
        if let Some(parent) = full.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(full, content)?;
    }
    Ok(())
}

fn write_build_context(
    root: &Path,
    name: &str,
    version: i32,
    source: &BTreeMap<String, String>,
    base_image: &str,
) -> Result<PathBuf> {
    let ctx = root
        .join("var")
        .join("builds")
        .join(name)
        .join(version.to_string());
    if ctx.exists() {
        fs::remove_dir_all(&ctx)?;
    }
    let component = ctx.join("component");
    write_component_dir(&component, source)?;
    let dockerfile = format!(
        "FROM {base_image}\nCOPY component /app/component\nENV PROTEUS_COMPONENT_DIR=/app/component\n"
    );
    fs::write(ctx.join("Dockerfile"), dockerfile)?;
    Ok(ctx)
}
