use std::path::Path;
use std::time::Duration;

use async_trait::async_trait;
use serde_json::Value;

use super::{RunningService, Runtime, StartSpec};
use crate::config::Config;
use crate::error::{Error, Result};
use crate::process::{self, CmdOutput};

pub struct DockerRuntime {
    docker: String,
    public_host: String,
}

impl DockerRuntime {
    pub fn new(cfg: &Config) -> Self {
        Self {
            docker: cfg.docker_bin.clone(),
            public_host: cfg.public_host.clone(),
        }
    }

    async fn docker(&self, args: &[String]) -> Result<CmdOutput> {
        process::run(&self.docker, args).await
    }
}

#[async_trait]
impl Runtime for DockerRuntime {
    fn kind(&self) -> &'static str {
        "docker"
    }

    async fn ensure_base_image(&self, dockerfile_dir: &Path, tag: &str) -> Result<()> {
        tracing::info!("building base image {tag}");
        self.docker(&[
            "build".into(),
            "-t".into(),
            tag.to_string(),
            dockerfile_dir.display().to_string(),
        ])
        .await?;
        Ok(())
    }

    async fn build_component(&self, image: &str, context_dir: &Path) -> Result<()> {
        tracing::info!("building {image}");
        self.docker(&[
            "build".into(),
            "-t".into(),
            image.to_string(),
            context_dir.display().to_string(),
        ])
        .await?;
        Ok(())
    }

    async fn start(&self, spec: &StartSpec) -> Result<RunningService> {
        if let Some(existing) = self.find(spec.name.as_str(), spec.version).await? {
            let image_ok = self
                .running_image_matches(&existing.runtime_id, &spec.image)
                .await
                .unwrap_or(false);
            let healthy = if !image_ok {
                false
            } else if spec.http_health {
                health_ok(&existing.endpoint).await
            } else {
                self.is_running(&existing.runtime_id).await
            };
            if healthy {
                return Ok(existing);
            }
            self.stop(&existing.runtime_id).await?;
        }

        let container_name = container_name(&spec.name, spec.version);
        let _ = self
            .docker(&["rm".into(), "-f".into(), container_name.clone()])
            .await;

        let mut args = vec![
            "run".into(),
            "-d".into(),
            "--name".into(),
            container_name,
            "--network".into(),
            spec.network.clone(),
            "--label".into(),
            "proteus.managed=true".into(),
            "--label".into(),
            format!("proteus.component={}", spec.name),
            "--label".into(),
            format!("proteus.version={}", spec.version),
            "-p".into(),
            format!("127.0.0.1::{}", spec.container_port),
            "--add-host".into(),
            "host.docker.internal:host-gateway".into(),
        ];
        for (key, value) in &spec.env {
            args.push("-e".into());
            args.push(format!("{key}={value}"));
        }
        args.push(spec.image.clone());
        args.extend(spec.command.iter().cloned());

        let output = self.docker(&args).await?;
        let runtime_id = output.stdout.lines().next().unwrap_or("").trim().to_string();
        if runtime_id.is_empty() {
            return Err(Error::Message("docker run returned no container id".into()));
        }
        let endpoint = self.endpoint_for(&runtime_id, spec.container_port).await?;
        Ok(RunningService {
            runtime_id,
            endpoint,
            name: spec.name.clone(),
            version: spec.version,
        })
    }

    async fn stop(&self, runtime_id: &str) -> Result<()> {
        let output = process::run_raw(
            &self.docker,
            &["rm".into(), "-f".into(), runtime_id.to_string()],
        )
        .await?;
        let err = output.stderr.to_ascii_lowercase();
        let out = output.stdout.to_ascii_lowercase();
        if output.status_ok || err.contains("no such container") || out.contains("no such container")
        {
            return Ok(());
        }
        Err(Error::Message(output.stderr))
    }

    async fn running_image_matches(&self, runtime_id: &str, image: &str) -> Result<bool> {
        let container = self
            .docker(&[
                "inspect".into(),
                "-f".into(),
                "{{.Image}}".into(),
                runtime_id.to_string(),
            ])
            .await?;
        let wanted = self
            .docker(&[
                "image".into(),
                "inspect".into(),
                "-f".into(),
                "{{.Id}}".into(),
                image.to_string(),
            ])
            .await?;
        Ok(container.stdout.trim() == wanted.stdout.trim() && !wanted.stdout.trim().is_empty())
    }

    async fn list(&self) -> Result<Vec<RunningService>> {
        let ids = process::run_raw(
            &self.docker,
            &[
                "ps".into(),
                "-aq".into(),
                "--filter".into(),
                "label=proteus.managed=true".into(),
            ],
        )
        .await?;
        if !ids.status_ok {
            return Err(Error::Message(ids.stderr));
        }
        let mut id_list: Vec<String> = ids
            .stdout
            .lines()
            .map(str::trim)
            .filter(|line| !line.is_empty())
            .map(str::to_string)
            .collect();
        if id_list.is_empty() {
            return Ok(Vec::new());
        }
        let mut args = vec!["inspect".into()];
        args.append(&mut id_list);
        let inspected = self.docker(&args).await?;
        let values: Vec<Value> = serde_json::from_str(&inspected.stdout)?;
        let mut services = Vec::new();
        for value in values {
            if !value["State"]["Running"].as_bool().unwrap_or(false) {
                continue;
            }
            let runtime_id = value["Id"].as_str().unwrap_or("").to_string();
            let labels = &value["Config"]["Labels"];
            let name = labels["proteus.component"].as_str().unwrap_or("").to_string();
            let version = labels["proteus.version"]
                .as_str()
                .unwrap_or("0")
                .parse()
                .unwrap_or(0);
            let endpoint = endpoint_from_inspect(&value, &self.public_host).unwrap_or_default();
            if runtime_id.is_empty() {
                continue;
            }
            services.push(RunningService {
                runtime_id,
                endpoint,
                name,
                version,
            });
        }
        Ok(services)
    }
}

impl DockerRuntime {
    async fn find(&self, name: &str, version: i32) -> Result<Option<RunningService>> {
        Ok(self
            .list()
            .await?
            .into_iter()
            .find(|svc| svc.name == name && svc.version == version))
    }

    async fn endpoint_for(&self, runtime_id: &str, port: u16) -> Result<String> {
        let mut last = String::new();
        for _ in 0..25 {
            let output = process::run_raw(
                &self.docker,
                &["port".into(), runtime_id.to_string(), port.to_string()],
            )
            .await?;
            last = output.stdout.clone();
            if let Some(host_port) = parse_port(&output.stdout) {
                return Ok(format!("http://{}:{host_port}", self.public_host));
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
        Err(Error::Message(format!(
            "cannot parse docker port for {runtime_id}: {last}"
        )))
    }

    async fn is_running(&self, runtime_id: &str) -> bool {
        let Ok(output) = self
            .docker(&[
                "inspect".into(),
                "-f".into(),
                "{{.State.Running}}".into(),
                runtime_id.to_string(),
            ])
            .await
        else {
            return false;
        };
        output.stdout.trim() == "true"
    }
}

fn container_name(name: &str, version: i32) -> String {
    let safe: String = name
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || ch == '-' || ch == '_' {
                ch.to_ascii_lowercase()
            } else {
                '-'
            }
        })
        .collect();
    format!("proteus-{safe}-{version}")
}

fn parse_port(text: &str) -> Option<u16> {
    let preferred = text
        .lines()
        .find(|line| line.contains("127.0.0.1"))
        .or_else(|| text.lines().find(|line| !line.trim().is_empty()))?;
    preferred
        .rsplit(':')
        .next()?
        .trim()
        .trim_end_matches(']')
        .parse()
        .ok()
}

fn endpoint_from_inspect(value: &Value, public_host: &str) -> Option<String> {
    let bindings = value["NetworkSettings"]["Ports"]["8000/tcp"].as_array()?;
    let host_port = bindings.iter().find_map(|binding| {
        let port = binding["HostPort"].as_str()?;
        let ip = binding["HostIp"].as_str().unwrap_or("");
        if ip == "127.0.0.1" || ip.is_empty() || ip == public_host {
            Some(port)
        } else {
            None
        }
    })?;
    let _ = public_host;
    Some(format!("http://127.0.0.1:{host_port}"))
}

async fn health_ok(endpoint: &str) -> bool {
    let url = format!("{endpoint}/health");
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(2))
        .build();
    let Ok(client) = client else {
        return false;
    };
    client
        .get(url)
        .send()
        .await
        .map(|res| res.status().as_u16() == 200)
        .unwrap_or(false)
}
