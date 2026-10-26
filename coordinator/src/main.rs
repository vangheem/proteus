mod abilities;
mod agent;
mod calls;
mod config;
mod db;
mod deploy;
mod error;
mod http;
mod introspect;
mod process;
mod runtime;

use std::fs;
use std::sync::Arc;

use error::Result;
use serde_json::json;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "proteus_coordinator=info,info".into()),
        )
        .init();
    if let Err(err) = run().await {
        eprintln!("proteus: {err}");
        std::process::exit(1);
    }
}

async fn run() -> Result<()> {
    let config = config::Config::from_env()?;
    ensure_opencode_config(&config)?;
    tracing::info!(
        redis = %config.redis_url,
        temporal = %config.temporal_address,
        public_host = %config.public_host,
        "coordinator endpoints"
    );
    let db = db::connect(&config.database_url).await?;
    let schema = fs::read_to_string(&config.schema_path)?;
    db::migrate(&db, &schema).await?;
    let interrupted = db::fail_interrupted(&db).await?;
    if interrupted > 0 {
        db::insert_event(
            &db,
            "recovered",
            &format!("{interrupted} in-flight calls were marked failed after restart"),
            None,
        )
        .await?;
    }

    let runtime = runtime::from_config(&config)?;
    tracing::info!(runtime = runtime.kind(), "selected runtime");
    runtime
        .ensure_base_image(&config.python_dir, &config.base_image)
        .await?;

    let director = agent::from_config(&config)?;
    let state = http::AppState {
        config: config.clone(),
        db,
        runtime,
        director,
        http: http::http_client()?,
        deploy_lock: Arc::new(tokio::sync::Mutex::new(())),
    };

    tracing::info!("deploying autostart components");
    deploy::sync_autostart(&state).await?;
    db::insert_event(&state.db, "started", "coordinator started", None).await?;

    let listener = tokio::net::TcpListener::bind(&config.http_addr).await?;
    let addr = listener.local_addr()?;
    println!("proteus listening on {addr}");
    let worker_state = state.clone();
    tokio::spawn(async move {
        abilities::supervise(worker_state).await;
    });
    axum::serve(listener, http::router(state)).await?;
    Ok(())
}

fn ensure_opencode_config(config: &config::Config) -> Result<()> {
    let path = config.root.join("opencode.json");
    if path.exists() {
        return Ok(());
    }
    let body = json!({
        "$schema": "https://opencode.ai/config.json",
        "model": config.model,
    });
    fs::write(path, serde_json::to_string_pretty(&body)?)?;
    Ok(())
}
