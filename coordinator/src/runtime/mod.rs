use std::path::Path;
use std::sync::Arc;

use async_trait::async_trait;

use crate::config::Config;
use crate::error::Result;

mod docker;
mod k8s;

/// Execution backend. Routing, versioning, and HTTP handlers depend only on this
/// trait and the published `endpoint`. A Kubernetes implementation replaces
/// `DockerRuntime` without changing those callers.
#[async_trait]
pub trait Runtime: Send + Sync {
    fn kind(&self) -> &'static str;

    async fn ensure_base_image(&self, dockerfile_dir: &Path, tag: &str) -> Result<()>;

    async fn build_component(&self, image: &str, context_dir: &Path) -> Result<()>;

    async fn start(&self, spec: &StartSpec) -> Result<RunningService>;

    async fn stop(&self, runtime_id: &str) -> Result<()>;

    async fn list(&self) -> Result<Vec<RunningService>>;

    /// True when the running workload was started from the current image id.
    async fn running_image_matches(&self, runtime_id: &str, image: &str) -> Result<bool>;
}

#[derive(Clone, Debug)]
pub struct StartSpec {
    pub name: String,
    pub version: i32,
    pub image: String,
    pub env: Vec<(String, String)>,
    pub network: String,
    pub container_port: u16,
    /// Overrides the image command. Empty uses the image default.
    pub command: Vec<String>,
    /// When true, an existing container is reused only if HTTP `/health` succeeds.
    pub http_health: bool,
}

#[derive(Clone, Debug)]
pub struct RunningService {
    pub runtime_id: String,
    pub endpoint: String,
    pub name: String,
    pub version: i32,
}

pub fn from_config(cfg: &Config) -> Result<Arc<dyn Runtime>> {
    match cfg.runtime.as_str() {
        "docker" => Ok(Arc::new(docker::DockerRuntime::new(cfg))),
        "kubernetes" | "k8s" => Ok(Arc::new(k8s::KubernetesRuntime)),
        other => Err(crate::error::Error::Message(format!(
            "unknown runtime {other}"
        ))),
    }
}
