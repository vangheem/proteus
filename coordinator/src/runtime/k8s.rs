use std::path::Path;

use async_trait::async_trait;

use super::{RunningService, Runtime, StartSpec};
use crate::error::{Error, Result};

/// Seam for a future Kubernetes deployer. The trait is the same one Docker uses.
/// Callers do not change when this implementation starts creating Deployments
/// and Services instead of containers.
pub struct KubernetesRuntime;

fn unsupported() -> Error {
    Error::Message(
        "kubernetes runtime is not implemented; implement Runtime in runtime/k8s.rs".into(),
    )
}

#[async_trait]
impl Runtime for KubernetesRuntime {
    fn kind(&self) -> &'static str {
        "kubernetes"
    }

    async fn ensure_base_image(&self, _dockerfile_dir: &Path, _tag: &str) -> Result<()> {
        Err(unsupported())
    }

    async fn build_component(&self, _image: &str, _context_dir: &Path) -> Result<()> {
        Err(unsupported())
    }

    async fn start(&self, _spec: &StartSpec) -> Result<RunningService> {
        Err(unsupported())
    }

    async fn stop(&self, _runtime_id: &str) -> Result<()> {
        Err(unsupported())
    }

    async fn list(&self) -> Result<Vec<RunningService>> {
        Err(unsupported())
    }

    async fn running_image_matches(&self, _runtime_id: &str, _image: &str) -> Result<bool> {
        Ok(false)
    }
}
