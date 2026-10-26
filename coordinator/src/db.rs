use sqlx::postgres::PgPoolOptions;
use sqlx::{PgPool, Row};

use crate::error::{Error, Result};

pub async fn connect(url: &str) -> Result<PgPool> {
    let mut last = None;
    for attempt in 1..=30 {
        match PgPoolOptions::new().max_connections(8).connect(url).await {
            Ok(pool) => return Ok(pool),
            Err(err) => {
                tracing::warn!("database not ready (attempt {attempt}): {err}");
                last = Some(err);
                tokio::time::sleep(std::time::Duration::from_secs(1)).await;
            }
        }
    }
    Err(Error::Message(format!(
        "database: {}",
        last.map(|err| err.to_string()).unwrap_or_else(|| "unavailable".into())
    )))
}

pub async fn migrate(pool: &PgPool, schema_sql: &str) -> Result<()> {
    for statement in schema_sql.split(';') {
        let statement = statement.trim();
        if statement.is_empty() {
            continue;
        }
        sqlx::query(statement).execute(pool).await?;
    }
    Ok(())
}

pub async fn insert_event(
    pool: &PgPool,
    kind: &str,
    message: &str,
    detail: Option<serde_json::Value>,
) -> Result<i64> {
    let row = sqlx::query(
        "INSERT INTO coordinator_events (kind, message, detail) VALUES ($1, $2, $3) RETURNING id",
    )
    .bind(kind)
    .bind(message)
    .bind(detail)
    .fetch_one(pool)
    .await?;
    Ok(row.try_get("id")?)
}

pub async fn fail_interrupted(pool: &PgPool) -> Result<u64> {
    let reason = "coordinator restarted before this call finished";
    let result = sqlx::query(
        "UPDATE tasks
         SET status = 'failed',
             error = $1,
             detail = COALESCE(detail, '{}'::jsonb) || jsonb_build_object('error', $1::text),
             updated_at = now()
         WHERE status IN ('queued', 'running')",
    )
    .bind(reason)
    .execute(pool)
    .await?;
    Ok(result.rows_affected())
}
