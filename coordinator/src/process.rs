use std::process::Command;

use crate::error::{Error, Result};

pub struct CmdOutput {
    pub status_ok: bool,
    pub stdout: String,
    pub stderr: String,
}

pub async fn run_raw(bin: &str, args: &[String]) -> Result<CmdOutput> {
    let bin = bin.to_string();
    let args = args.to_vec();
    let joined = format!("{bin} {}", args.join(" "));
    let output = tokio::task::spawn_blocking(move || Command::new(bin).args(args).output())
        .await
        .map_err(Error::msg)?
        .map_err(Error::from)?;
    Ok(CmdOutput {
        status_ok: output.status.success(),
        stdout: String::from_utf8_lossy(&output.stdout).to_string(),
        stderr: format!("{joined}\n{}", String::from_utf8_lossy(&output.stderr)),
    })
}

pub async fn run(bin: &str, args: &[String]) -> Result<CmdOutput> {
    let output = run_raw(bin, args).await?;
    if !output.status_ok {
        return Err(Error::Message(format!(
            "{}{}",
            output.stderr, output.stdout
        )));
    }
    Ok(output)
}
