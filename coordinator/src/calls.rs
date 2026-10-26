use serde_json::Value;

/// A Herdr or agent failure, reduced to the reason a person can act on.
#[derive(Clone, Debug)]
pub struct Failure {
    pub code: String,
    pub message: String,
    pub raw: String,
}

pub fn explain(raw: &str) -> Failure {
    if let Some(value) = error_json(raw) {
        let code = value
            .pointer("/error/code")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let message = value
            .pointer("/error/message")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_string();
        if !message.is_empty() {
            return Failure {
                code,
                message,
                raw: raw.to_string(),
            };
        }
    }
    let message = raw
        .lines()
        .rev()
        .map(str::trim)
        .find(|line| !line.is_empty() && !line.starts_with("herdr "))
        .unwrap_or("call failed with no error output")
        .to_string();
    Failure {
        code: String::new(),
        message,
        raw: raw.to_string(),
    }
}

/// Failures where the prompt was not accepted and a fresh pane can be tried once.
pub fn recoverable(failure: &Failure) -> bool {
    matches!(
        failure.code.as_str(),
        "agent_pane_not_found"
            | "agent_not_found"
            | "pane_not_found"
            | "agent_not_ready"
            | "agent_prompt_stalled"
    ) || failure.message.contains("not found")
        || failure.message.contains("connection refused")
        || failure.message.contains("broken pipe")
        || failure.message.contains("socket")
}

pub fn reason_from(error: Option<&str>, detail: Option<&Value>) -> String {
    if let Some(text) = error.map(str::trim).filter(|text| !text.is_empty()) {
        return text.to_string();
    }
    let raw = detail
        .and_then(|value| value.get("error"))
        .and_then(Value::as_str)
        .unwrap_or("");
    if raw.is_empty() {
        return String::new();
    }
    explain(raw).message
}

fn error_json(raw: &str) -> Option<Value> {
    let idx = raw.rfind("{\"error\"")?;
    let line = raw[idx..].lines().next().unwrap_or("");
    serde_json::from_str(line).ok()
}
