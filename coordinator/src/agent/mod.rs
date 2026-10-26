use std::sync::Arc;

use async_trait::async_trait;
use serde_json::Value;

use crate::config::Config;
use crate::error::Result;

mod herdr;

#[derive(Clone, Debug)]
pub struct PlannedCommand {
    pub argv: Vec<String>,
}

#[derive(Clone, Debug)]
pub struct AgentPlan {
    pub backend: String,
    pub kind: String,
    pub model: String,
    pub prompt: String,
    pub commands: Vec<PlannedCommand>,
}

#[async_trait]
pub trait AgentDirector: Send + Sync {
    fn kind(&self) -> &'static str;
    fn model(&self) -> &str;
    fn plan(&self, prompt: &str) -> AgentPlan;
    async fn submit(&self, prompt: &str) -> Result<String>;
    async fn ask(&self, prompt: &str) -> Result<String>;
    async fn task(&self, prompt: &str) -> Result<String>;
    async fn probe(&self) -> Result<Value>;
}

pub fn from_config(cfg: &Config) -> Result<Arc<dyn AgentDirector>> {
    match cfg.agent_backend.as_str() {
        "herdr" => Ok(Arc::new(herdr::HerdrDirector::new(cfg))),
        other => Err(crate::error::Error::Message(format!(
            "unknown agent backend {other}"
        ))),
    }
}
