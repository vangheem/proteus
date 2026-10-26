use std::env;
use std::path::{Path, PathBuf};

use crate::error::{Error, Result};

#[derive(Clone, Debug)]
pub struct Config {
    pub http_addr: String,
    pub database_url: String,
    pub component_database_url: String,
    pub redis_url: String,
    pub component_redis_url: String,
    pub temporal_address: String,
    pub component_temporal_address: String,
    pub temporal_namespace: String,
    pub temporal_ui_url: String,
    pub runtime: String,
    pub docker_bin: String,
    pub docker_network: String,
    pub base_image: String,
    pub public_host: String,
    pub root: PathBuf,
    pub components_dir: PathBuf,
    pub python_dir: PathBuf,
    pub schema_path: PathBuf,
    pub bootstrap_component: String,
    pub agent_backend: String,
    pub agent_kind: String,
    pub model: String,
    pub herdr_bin: String,
    pub agent_name: String,
    pub agent_timeout_ms: u64,
    pub ability_executor: String,
}

impl Config {
    pub fn from_env() -> Result<Self> {
        let cwd = env::current_dir()?;
        let root = resolve(&cwd, &env_or("PROTEUS_ROOT", "."));
        Ok(Self {
            http_addr: env_or("PROTEUS_HTTP_ADDR", "0.0.0.0:8080"),
            database_url: env_or(
                "PROTEUS_DATABASE_URL",
                "postgres://proteus:proteus@127.0.0.1:5432/proteus",
            ),
            component_database_url: env_or(
                "PROTEUS_COMPONENT_DATABASE_URL",
                "postgres://proteus:proteus@postgres:5432/proteus",
            ),
            redis_url: env_or("PROTEUS_REDIS_URL", "redis://127.0.0.1:6379/0"),
            component_redis_url: env_or("PROTEUS_COMPONENT_REDIS_URL", "redis://redis:6379/0"),
            temporal_address: env_or("PROTEUS_TEMPORAL_ADDRESS", "127.0.0.1:7233"),
            component_temporal_address: env_or("PROTEUS_COMPONENT_TEMPORAL_ADDRESS", "temporal:7233"),
            temporal_namespace: env_or("PROTEUS_TEMPORAL_NAMESPACE", "default"),
            temporal_ui_url: env_or("PROTEUS_TEMPORAL_UI_URL", "http://127.0.0.1:8233"),
            runtime: env_or("PROTEUS_RUNTIME", "docker"),
            docker_bin: env_or("PROTEUS_DOCKER_BIN", "docker"),
            docker_network: env_or("PROTEUS_DOCKER_NETWORK", "proteus"),
            base_image: env_or("PROTEUS_BASE_IMAGE", "proteus-python-base:latest"),
            public_host: env_or("PROTEUS_PUBLIC_HOST", "127.0.0.1"),
            components_dir: resolve(&cwd, &env_or("PROTEUS_COMPONENTS_DIR", "components")),
            python_dir: resolve(&cwd, &env_or("PROTEUS_PYTHON_DIR", "python")),
            schema_path: resolve(&cwd, &env_or("PROTEUS_SCHEMA_PATH", "deploy/schema.sql")),
            bootstrap_component: env_or("PROTEUS_BOOTSTRAP_COMPONENT", "bootstrap"),
            agent_backend: env_or("PROTEUS_AGENT_BACKEND", "herdr"),
            agent_kind: env_or("PROTEUS_AGENT_KIND", "opencode"),
            model: env_or("PROTEUS_MODEL", "xai/grok-4.7"),
            herdr_bin: env_or("PROTEUS_HERDR_BIN", "herdr"),
            agent_name: env_or("PROTEUS_AGENT_NAME", "proteus-mutator"),
            agent_timeout_ms: env_or("PROTEUS_AGENT_TIMEOUT_MS", "600000")
                .parse()
                .map_err(|_| Error::Message("PROTEUS_AGENT_TIMEOUT_MS is not an integer".into()))?,
            ability_executor: env_or("PROTEUS_ABILITY_EXECUTOR", "herdr"),
            root,
        })
    }
}

fn env_or(key: &str, default: &str) -> String {
    env::var(key).unwrap_or_else(|_| default.to_string())
}

fn resolve(root: &Path, raw: &str) -> PathBuf {
    let path = PathBuf::from(raw);
    if path.is_absolute() {
        path
    } else if raw == "." {
        root.to_path_buf()
    } else {
        root.join(path)
    }
}
