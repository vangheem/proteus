use std::time::Duration;

use serde_json::{Value, json};

use crate::deploy;
use crate::error::{Error, Result};
use crate::http::AppState;
use crate::runtime::StartSpec;

pub fn catalog() -> Value {
    json!({
        "task_queue": "proteus-coordinator",
        "model": "configured",
        "abilities": [
            {
                "name": "ask",
                "description": "Ask the coding agent a question. Does not edit files."
            },
            {
                "name": "task",
                "description": "Give the coding agent a bounded task. May update components; the coordinator then versions and swaps them."
            }
        ]
    })
}

pub async fn invoke(state: &AppState, name: &str, arg: &Value) -> Result<Value> {
    let prompt = arg
        .get("prompt")
        .or_else(|| arg.get("question"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    if prompt.is_empty() {
        return Err(Error::BadRequest("prompt is required".into()));
    }
    let id = uuid::Uuid::new_v4().to_string();
    sqlx::query("INSERT INTO tasks (id, prompt, status, kind) VALUES ($1, $2, 'running', $3)")
        .bind(&id)
        .bind(&prompt)
        .bind(name)
        .execute(&state.db)
        .await?;
    let model = state.director.model().to_string();
    let executor = state.config.ability_executor.clone();
    let outcome = match name {
        "ask" => {
            let answer = if executor == "echo" {
                format!("echo: {prompt}")
            } else {
                state.director.ask(&prompt).await?
            };
            Ok(json!({
                "answer": answer,
                "model": model,
                "executor": executor,
            }))
        }
        "task" => {
            let output = if executor == "echo" {
                format!("echo: {prompt}")
            } else {
                let output = state.director.task(&prompt).await?;
                let _guard = state.deploy_lock.lock().await;
                deploy::sync_all(state).await?;
                output
            };
            Ok(json!({
                "output": output,
                "model": model,
                "executor": executor,
            }))
        }
        other => Err(Error::NotFound(format!("unknown ability {other}"))),
    };
    match &outcome {
        Ok(result) => {
            sqlx::query(
                "UPDATE tasks SET status = 'done', detail = $2, updated_at = now() WHERE id = $1",
            )
            .bind(&id)
            .bind(json!({"result": result}))
            .execute(&state.db)
            .await?;
        }
        Err(err) => {
            let failure = crate::calls::explain(&err.to_string());
            let message = failure.message;
            sqlx::query(
                "UPDATE tasks SET status = 'failed', error = $2, detail = $3, updated_at = now() WHERE id = $1",
            )
            .bind(&id)
            .bind(&message)
            .bind(json!({"error": message, "code": failure.code, "raw": failure.raw}))
            .execute(&state.db)
            .await?;
            crate::db::insert_event(
                &state.db,
                "call_failed",
                &format!("{name} failed: {message}"),
                Some(json!({"id": id, "error": message})),
            )
            .await?;
            tracing::error!("ability {name} {id} failed: {message}");
        }
    }
    outcome
}

pub async fn supervise(state: AppState) {
    if state.runtime.kind() != "docker" {
        tracing::warn!("ability worker is started by the docker runtime only");
        return;
    }
    loop {
        if let Err(err) = start_worker(&state).await {
            tracing::warn!("ability worker start failed: {err}");
        }
        tokio::time::sleep(Duration::from_secs(15)).await;
    }
}

async fn start_worker(state: &AppState) -> Result<()> {
    let port = state
        .config
        .http_addr
        .rsplit(':')
        .next()
        .unwrap_or("8080");
    let url = format!("http://host.docker.internal:{port}/__proteus/api/abilities/invoke");
    state
        .runtime
        .start(&StartSpec {
            name: "ability-worker".into(),
            version: 1,
            image: state.config.base_image.clone(),
            env: vec![
                (
                    "TEMPORAL_ADDRESS".into(),
                    "temporal:7233".into(),
                ),
                (
                    "TEMPORAL_NAMESPACE".into(),
                    state.config.temporal_namespace.clone(),
                ),
                ("PROTEUS_ABILITY_URL".into(), url),
                (
                    "PROTEUS_AGENT_TIMEOUT_MS".into(),
                    state.config.agent_timeout_ms.to_string(),
                ),
            ],
            network: state.config.docker_network.clone(),
            container_port: 8000,
            command: vec![
                "python".into(),
                "-m".into(),
                "proteus.coordinator_worker".into(),
            ],
            http_health: false,
        })
        .await?;
    tracing::info!("ability worker polling proteus-coordinator");
    Ok(())
}
