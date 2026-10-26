use std::env;

use async_trait::async_trait;
use serde_json::{Value, json};
use tokio::sync::Mutex;

use super::{AgentDirector, AgentPlan, PlannedCommand};
use crate::calls::{self, Failure};
use crate::config::Config;
use crate::error::{Error, Result};
use crate::process;

pub struct HerdrDirector {
    herdr: String,
    kind: String,
    model: String,
    agent_name: String,
    root: std::path::PathBuf,
    components_dir: std::path::PathBuf,
    timeout_ms: u64,
    pane: Mutex<Option<String>>,
    submit_lock: Mutex<()>,
}

impl HerdrDirector {
    pub fn new(cfg: &Config) -> Self {
        Self {
            herdr: cfg.herdr_bin.clone(),
            kind: cfg.agent_kind.clone(),
            model: cfg.model.clone(),
            agent_name: cfg.agent_name.clone(),
            root: cfg.root.clone(),
            components_dir: cfg.components_dir.clone(),
            timeout_ms: cfg.agent_timeout_ms,
            pane: Mutex::new(None),
            submit_lock: Mutex::new(()),
        }
    }

    fn task_prompt(&self, user: &str) -> String {
        format!(
            "You are the Proteus mutator, an OpenCode agent named {}.\n\
             Project root: {}.\n\
             Components directory: {}.\n\
             Only create or edit files under the components directory.\n\
             Do not modify the coordinator, the Python SDK, deploy, tests, or the introspection UI.\n\
             The introspection UI is immutable and is not a component.\n\
             Component contract: a directory with proteus.toml, app.py, and optional activities.py and workflows.py.\n\
             UIs use HTMX. Do not add a status bar; the coordinator injects one.\n\
             Do not deploy; the coordinator versions and swaps containers after you finish.\n\
             Configured model is {}.\n\
             \n\
             User request:\n{}\n",
            self.agent_name,
            self.root.display(),
            self.components_dir.display(),
            self.model,
            user
        )
    }

    fn caller_pane() -> String {
        env::var("HERDR_PANE_ID").unwrap_or_else(|_| "HERDR_PANE_ID".into())
    }

    async fn ensure_pane(&self) -> Result<String> {
        let mut pane = self.pane.lock().await;
        if let Some(id) = pane.clone() {
            if self.pane_alive(&id).await {
                return Ok(id);
            }
            tracing::warn!("cached herdr pane {id} is gone; opening a new one");
            *pane = None;
        }
        let caller = Self::caller_pane();
        if caller == "HERDR_PANE_ID" {
            return Err(Error::Message(
                "HERDR_PANE_ID is not set; start the coordinator from a Herdr pane".into(),
            ));
        }
        let output = process::run(
            &self.herdr,
            &[
                "pane".into(),
                "split".into(),
                "--pane".into(),
                caller.clone(),
                "--direction".into(),
                "right".into(),
                "--cwd".into(),
                self.root.display().to_string(),
                "--no-focus".into(),
            ],
        )
        .await?;
        let id = pane_id_from_split(&output.stdout, &caller)?;
        *pane = Some(id.clone());
        Ok(id)
    }

    async fn existing_agent_pane(&self) -> Result<Option<String>> {
        let output = process::run(&self.herdr, &["agent".into(), "list".into()]).await?;
        let parsed: Value = serde_json::from_str(output.stdout.trim()).unwrap_or(Value::Null);
        let Some(items) = parsed.pointer("/result/agents").and_then(Value::as_array) else {
            return Ok(None);
        };
        for item in items {
            if item["name"].as_str() == Some(self.agent_name.as_str()) {
                if let Some(id) = item["pane_id"].as_str() {
                    if self.pane_alive(id).await {
                        return Ok(Some(id.to_string()));
                    }
                    tracing::warn!("agent {} is attached to dead pane {id}", self.agent_name);
                }
            }
        }
        Ok(None)
    }

    async fn pane_alive(&self, id: &str) -> bool {
        let mut args = vec!["pane".into(), "list".into()];
        if let Ok(workspace) = env::var("HERDR_WORKSPACE_ID") {
            args.push("--workspace".into());
            args.push(workspace);
        }
        let Ok(output) = process::run(&self.herdr, &args).await else {
            return false;
        };
        output.stdout.contains(&format!("\"pane_id\":\"{id}\""))
            || output.stdout.contains(&format!("\"pane_id\": \"{id}\""))
    }

    async fn agent_status(&self) -> String {
        match process::run(
            &self.herdr,
            &["agent".into(), "get".into(), self.agent_name.clone()],
        )
        .await
        {
            Ok(output) => serde_json::from_str::<Value>(output.stdout.trim())
                .ok()
                .and_then(|value| {
                    value
                        .pointer("/result/agent/agent_status")
                        .and_then(Value::as_str)
                        .map(str::to_string)
                })
                .unwrap_or_else(|| "unknown".into()),
            Err(err) => calls::explain(&err.to_string()).message,
        }
    }

    async fn reset(&self) {
        *self.pane.lock().await = None;
    }

    async fn ensure_agent(&self, pane: &str) -> Result<()> {
        if self.agent_running().await? {
            return Ok(());
        }
        if let Err(err) = process::run(
            &self.herdr,
            &[
                "agent".into(),
                "start".into(),
                self.agent_name.clone(),
                "--kind".into(),
                self.kind.clone(),
                "--pane".into(),
                pane.to_string(),
                "--timeout".into(),
                "60000".into(),
                "--".into(),
                "--auto".into(),
            ],
        )
        .await
        {
            if self.agent_running().await.unwrap_or(false) {
                return Ok(());
            }
            return Err(err);
        }
        Ok(())
    }

    async fn agent_running(&self) -> Result<bool> {
        let output = process::run(&self.herdr, &["agent".into(), "list".into()]).await?;
        let parsed: Value = serde_json::from_str(output.stdout.trim()).unwrap_or(Value::Null);
        let agents = parsed["result"]["agents"].as_array();
        Ok(agents.is_some_and(|items| {
            items.iter().any(|item| item["name"].as_str() == Some(self.agent_name.as_str()))
        }))
    }

    async fn submit_inner(&self, prompt: &str, retried: bool) -> Result<String> {
        let mut retried = retried;
        loop {
            if let Err(err) = self.prepare_agent().await {
                let failure = calls::explain(&err.to_string());
                if !retried && calls::recoverable(&failure) {
                    tracing::warn!(
                        "herdr connection failed ({}); resetting the session and retrying once",
                        failure.message
                    );
                    self.reset().await;
                    retried = true;
                    continue;
                }
                return Err(self.failure_error(failure).await);
            }
            match self.prompt_agent(prompt).await {
                Ok(text) => return Ok(text),
                Err(err) => {
                    let failure = calls::explain(&err.to_string());
                    self.drop_dead_pane().await;
                    if !retried && calls::recoverable(&failure) && failure.code != "timeout" {
                        tracing::warn!(
                            "herdr prompt was not accepted ({}); resetting the session and retrying once",
                            failure.message
                        );
                        self.reset().await;
                        retried = true;
                        continue;
                    }
                    return Err(self.failure_error(failure).await);
                }
            }
        }
    }

    async fn prepare_agent(&self) -> Result<()> {
        if env::var("HERDR_PANE_ID").is_err() && env::var("HERDR_ENV").ok().as_deref() != Some("1")
        {
            return Err(Error::Message(
                "HERDR_PANE_ID is not set; start the coordinator from a Herdr pane".into(),
            ));
        }
        if let Some(existing) = self.existing_agent_pane().await? {
            *self.pane.lock().await = Some(existing);
            return Ok(());
        }
        let pane = self.ensure_pane().await?;
        self.ensure_agent(&pane).await
    }

    fn poll_ms(&self) -> u64 {
        self.timeout_ms.clamp(15_000, 60_000)
    }

    async fn prompt_agent(&self, prompt: &str) -> Result<String> {
        let task = self.task_prompt(prompt);
        let output = process::run_raw(
            &self.herdr,
            &[
                "agent".into(),
                "prompt".into(),
                self.agent_name.clone(),
                task,
                "--wait".into(),
                "--timeout".into(),
                self.poll_ms().to_string(),
            ],
        )
        .await?;
        if output.status_ok {
            return self.collect_output(output.stdout).await;
        }
        let failure = calls::explain(&format!("{}{}", output.stderr, output.stdout));
        if self.still_working(&failure).await {
            return self.wait_until_settled().await;
        }
        Err(Error::Message(format!("{}{}", output.stderr, output.stdout)))
    }

    async fn still_working(&self, failure: &Failure) -> bool {
        let timed_out =
            failure.code == "timeout" || failure.message.contains("timed out waiting for agent status");
        timed_out && self.agent_status().await == "working"
    }

    async fn wait_until_settled(&self) -> Result<String> {
        let poll = self.poll_ms().to_string();
        loop {
            match self.agent_status().await.as_str() {
                "working" => {
                    tracing::info!(
                        "agent {} is still working; continuing to wait",
                        self.agent_name
                    );
                }
                "idle" | "done" => return self.collect_output(String::new()).await,
                "blocked" => {
                    return Err(Error::Message(
                        "agent is blocked waiting for approval; agent status: blocked".into(),
                    ));
                }
                other => {
                    return Err(Error::Message(format!(
                        "agent stopped while the task was still running; agent status: {other}"
                    )));
                }
            }
            let wait = process::run_raw(
                &self.herdr,
                &[
                    "agent".into(),
                    "wait".into(),
                    self.agent_name.clone(),
                    "--until".into(),
                    "idle".into(),
                    "--until".into(),
                    "done".into(),
                    "--until".into(),
                    "blocked".into(),
                    "--timeout".into(),
                    poll.clone(),
                ],
            )
            .await?;
            if wait.status_ok {
                if self.agent_status().await == "blocked" {
                    return Err(Error::Message(
                        "agent is blocked waiting for approval; agent status: blocked".into(),
                    ));
                }
                return self.collect_output(wait.stdout).await;
            }
            let failure = calls::explain(&format!("{}{}", wait.stderr, wait.stdout));
            if self.still_working(&failure).await {
                continue;
            }
            let status = self.agent_status().await;
            if status == "idle" || status == "done" {
                return self.collect_output(String::new()).await;
            }
            if status == "working" {
                tracing::warn!(
                    "wait returned an error but the agent is still working: {}",
                    failure.message
                );
                continue;
            }
            return Err(Error::Message(format!(
                "{}; agent status: {status}",
                failure.message
            )));
        }
    }

    async fn collect_output(&self, mut text: String) -> Result<String> {
        if let Ok(read) = process::run(
            &self.herdr,
            &[
                "agent".into(),
                "read".into(),
                self.agent_name.clone(),
                "--source".into(),
                "recent-unwrapped".into(),
                "--lines".into(),
                "80".into(),
            ],
        )
        .await
        {
            if !read.stdout.trim().is_empty() {
                text.push('\n');
                text.push_str(&read.stdout);
            }
        }
        Ok(text)
    }

    async fn drop_dead_pane(&self) {
        let cached = self.pane.lock().await.clone();
        if let Some(id) = cached {
            if !self.pane_alive(&id).await {
                self.reset().await;
            }
        }
    }

    async fn failure_error(&self, failure: Failure) -> Error {
        let status = self.agent_status().await;
        Error::Message(format!("{}; agent status: {status}", failure.message))
    }
}

#[async_trait]
impl AgentDirector for HerdrDirector {
    fn kind(&self) -> &'static str {
        "herdr"
    }

    fn model(&self) -> &str {
        &self.model
    }

    fn plan(&self, prompt: &str) -> AgentPlan {
        let task = self.task_prompt(prompt);
        let commands = vec![
            PlannedCommand {
                argv: vec![
                    self.herdr.clone(),
                    "pane".into(),
                    "split".into(),
                    "--pane".into(),
                    Self::caller_pane(),
                    "--direction".into(),
                    "right".into(),
                    "--cwd".into(),
                    self.root.display().to_string(),
                    "--no-focus".into(),
                ],
            },
            PlannedCommand {
                argv: vec![
                    self.herdr.clone(),
                    "agent".into(),
                    "start".into(),
                    self.agent_name.clone(),
                    "--kind".into(),
                    self.kind.clone(),
                    "--pane".into(),
                    "{new-pane}".into(),
                    "--timeout".into(),
                    "60000".into(),
                    "--".into(),
                    "--auto".into(),
                ],
            },
            PlannedCommand {
                argv: vec![
                    self.herdr.clone(),
                    "agent".into(),
                    "prompt".into(),
                    self.agent_name.clone(),
                    task.clone(),
                    "--wait".into(),
                ],
            },
        ];
        AgentPlan {
            backend: "herdr".into(),
            kind: self.kind.clone(),
            model: self.model.clone(),
            prompt: task,
            commands,
        }
    }

    async fn submit(&self, prompt: &str) -> Result<String> {
        let _guard = self.submit_lock.lock().await;
        self.submit_inner(prompt, false).await
    }

    async fn ask(&self, prompt: &str) -> Result<String> {
        let question = format!(
            "You are the Proteus coding assistant. Answer the question. Do not edit files. Do not deploy.\n\
             Configured model is {}.\n\nQuestion:\n{prompt}\n",
            self.model
        );
        let output = process::run(
            "opencode",
            &[
                "run".into(),
                "--model".into(),
                self.model.clone(),
                "--auto".into(),
                question,
            ],
        )
        .await?;
        let answer = output.stdout.trim();
        if answer.is_empty() {
            return Err(Error::Message(format!(
                "coding agent returned no answer: {}",
                output.stderr
            )));
        }
        Ok(answer.to_string())
    }

    async fn task(&self, prompt: &str) -> Result<String> {
        self.submit(prompt).await
    }

    async fn probe(&self) -> Result<Value> {
        let output = process::run(&self.herdr, &["agent".into(), "list".into()]).await?;
        let raw = serde_json::from_str::<Value>(output.stdout.trim()).unwrap_or(Value::Null);
        Ok(json!({
            "ok": true,
            "backend": "herdr",
            "agents": raw
        }))
    }
}

fn pane_id_from_split(stdout: &str, source: &str) -> Result<String> {
    let parsed: Value = serde_json::from_str(stdout.trim()).map_err(|err| {
        Error::Message(format!("herdr split did not return json: {err}: {stdout}"))
    })?;
    if let Some(id) = parsed
        .pointer("/result/pane/pane_id")
        .and_then(Value::as_str)
    {
        if id != source {
            return Ok(id.to_string());
        }
    }
    let mut ids = Vec::new();
    collect_pane_ids(&parsed, &mut ids);
    ids.into_iter()
        .find(|id| id != source)
        .ok_or_else(|| Error::Message(format!("herdr split returned no pane id: {stdout}")))
}

fn collect_pane_ids(value: &Value, out: &mut Vec<String>) {
    match value {
        Value::Object(map) => {
            for (key, child) in map {
                if key == "pane_id" {
                    if let Some(id) = child.as_str() {
                        out.push(id.to_string());
                    }
                }
                collect_pane_ids(child, out);
            }
        }
        Value::Array(items) => {
            for child in items {
                collect_pane_ids(child, out);
            }
        }
        _ => {}
    }
}
