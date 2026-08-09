//! Worker supervision stays deliberately narrow: the webview cannot choose a
//! command, executable path, or environment. The app supplies the fixed
//! bundled resource and Rust verifies the handshake before the UI can use it.

use serde_json::{Value, json};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{
    Arc, Mutex,
    mpsc::{self, Receiver, RecvTimeoutError},
};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SupervisorState {
    Stopped,
    Starting,
    Ready { protocol_version: u8 },
    Interrupted,
}

pub struct WorkerSupervisor {
    state: SupervisorState,
    child: Option<Child>,
    stdin: Option<ChildStdin>,
    stdout_events: Option<Receiver<Result<String, String>>>,
    pending_events: Vec<Value>,
    executable: Option<PathBuf>,
    stderr_lines: Arc<Mutex<Vec<String>>>,
}

impl WorkerSupervisor {
    pub fn new() -> Self {
        Self {
            state: SupervisorState::Stopped,
            child: None,
            stdin: None,
            stdout_events: None,
            pending_events: Vec::new(),
            executable: None,
            stderr_lines: Arc::new(Mutex::new(Vec::new())),
        }
    }

    pub fn state(&self) -> SupervisorState {
        self.state
    }

    pub fn mark_interrupted(&mut self) {
        self.shutdown();
        self.state = SupervisorState::Interrupted;
    }

    pub fn start(&mut self, executable: &Path) -> Result<(), String> {
        if self.child.is_some() {
            return Ok(());
        }
        self.state = SupervisorState::Starting;
        // Retain the fixed resource location before launch so an initial
        // transient failure can be retried through the same narrow path.
        self.executable = Some(executable.to_owned());
        // Starting SynVid must not read a user's credential store. The worker
        // starts with no third-party credentials and model downloads attempt
        // anonymous access only after the user explicitly chooses one.
        let mut child = Command::new(executable)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|error| format!("could not launch bundled worker: {error}"))?;
        let mut stdin = child
            .stdin
            .take()
            .ok_or("bundled worker stdin was unavailable")?;
        let stdout = child
            .stdout
            .take()
            .ok_or("bundled worker stdout was unavailable")?;
        let stderr = child
            .stderr
            .take()
            .ok_or("bundled worker stderr was unavailable")?;
        let stderr_lines = Arc::clone(&self.stderr_lines);
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                let mut lines = stderr_lines.lock().expect("worker stderr lock poisoned");
                if lines.len() == 128 {
                    lines.remove(0);
                }
                lines.push(line);
            }
        });
        let hello = b"{\"version\":1,\"request_id\":\"app-startup\",\"kind\":\"hello\",\"payload\":{\"protocol_min\":1,\"protocol_max\":1}}\n";
        if let Err(error) = stdin.write_all(hello).and_then(|()| stdin.flush()) {
            let _ = child.kill();
            let _ = child.wait();
            self.state = SupervisorState::Interrupted;
            return Err(format!("could not handshake with bundled worker: {error}"));
        }
        let mut reader = BufReader::new(stdout);
        let mut line = String::new();
        if let Err(error) = reader.read_line(&mut line) {
            let _ = child.kill();
            let _ = child.wait();
            self.state = SupervisorState::Interrupted;
            return Err(format!(
                "bundled worker did not reply to handshake: {error}"
            ));
        }
        let version = hello_version(&line)
            .ok_or_else(|| "bundled worker returned an incompatible handshake".to_string())?;
        let (stdout_sender, stdout_events) = mpsc::channel();
        std::thread::spawn(move || {
            let mut reader = reader;
            loop {
                let mut line = String::new();
                match reader.read_line(&mut line) {
                    Ok(0) => {
                        let _ = stdout_sender.send(Err("worker stdout closed".into()));
                        return;
                    }
                    Ok(_) => {
                        if stdout_sender.send(Ok(line)).is_err() {
                            return;
                        }
                    }
                    Err(error) => {
                        let _ = stdout_sender.send(Err(error.to_string()));
                        return;
                    }
                }
            }
        });
        self.child = Some(child);
        self.stdin = Some(stdin);
        self.stdout_events = Some(stdout_events);
        self.state = SupervisorState::Ready {
            protocol_version: version,
        };
        Ok(())
    }

    pub fn shutdown(&mut self) {
        self.stdin = None;
        self.stdout_events = None;
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
        self.state = SupervisorState::Stopped;
    }

    /// Send one fixed protocol operation. The command and payload are chosen
    /// by Rust commands, never by the webview as a shell/process request.
    pub fn request(&mut self, kind: &str, payload: Value) -> Result<Value, String> {
        if !matches!(self.state, SupervisorState::Ready { .. }) {
            return Err("worker is not ready".into());
        }
        let request_id = format!(
            "desktop-{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_err(|_| "system clock is unavailable")?
                .as_nanos()
        );
        let request = json!({
            "version": 1,
            "request_id": request_id,
            "kind": kind,
            "payload": payload,
        });
        let encoded = serde_json::to_string(&request).map_err(|error| error.to_string())?;
        let stdin = self.stdin.as_mut().ok_or("worker stdin is unavailable")?;
        stdin
            .write_all(encoded.as_bytes())
            .and_then(|()| stdin.write_all(b"\n"))
            .and_then(|()| stdin.flush())
            .map_err(|error| self.interrupt_with(error.to_string()))?;

        let deadline = std::time::Instant::now() + Duration::from_secs(10);
        loop {
            if let Some(child) = self.child.as_mut() {
                if let Some(status) = child
                    .try_wait()
                    .map_err(|error| self.interrupt_with(error.to_string()))?
                {
                    return Err(self.interrupt_with(format!(
                        "worker exited while handling a request ({status})"
                    )));
                }
            }
            let remaining = deadline.saturating_duration_since(std::time::Instant::now());
            if remaining.is_zero() {
                return Err(self.interrupt_with("worker did not reply within 10 seconds".into()));
            }
            let receiver = self
                .stdout_events
                .as_ref()
                .ok_or("worker stdout is unavailable")?;
            match receiver.recv_timeout(remaining.min(Duration::from_millis(100))) {
                Ok(Ok(line)) => {
                    let message: Value = serde_json::from_str(&line).map_err(|_| {
                        self.interrupt_with("worker sent malformed protocol output".into())
                    })?;
                    if message.get("request_id").and_then(Value::as_str)
                        == Some(request_id.as_str())
                    {
                        return Ok(message);
                    }
                    // Progress and terminal messages arrive while a later status poll
                    // is waiting. Preserve them for the next UI snapshot.
                    self.pending_events.push(message);
                }
                Ok(Err(error)) => return Err(self.interrupt_with(error)),
                Err(RecvTimeoutError::Timeout) => continue,
                Err(RecvTimeoutError::Disconnected) => {
                    return Err(self.interrupt_with("worker stdout reader stopped".into()));
                }
            }
        }
    }

    /// Last worker stderr lines (bounded to 128 by the collector thread),
    /// exposed read-only for the opt-in diagnostic export.
    pub fn recent_stderr_lines(&self) -> Vec<String> {
        self.stderr_lines
            .lock()
            .expect("worker stderr lock poisoned")
            .clone()
    }

    pub fn take_pending_events(&mut self) -> Vec<Value> {
        std::mem::take(&mut self.pending_events)
    }

    pub fn restart_if_interrupted(&mut self) -> Result<(), String> {
        if !matches!(
            self.state,
            SupervisorState::Interrupted | SupervisorState::Stopped
        ) {
            return Ok(());
        }
        let executable = self
            .executable
            .clone()
            .ok_or("bundled worker location is unavailable")?;
        self.start(&executable)
    }

    fn interrupt_with(&mut self, detail: String) -> String {
        self.mark_interrupted();
        // Stderr is drained to prevent child-process backpressure, but is not
        // returned to the webview because libraries may include user inputs.
        format!("worker interrupted: {detail}")
    }
}

fn hello_version(line: &str) -> Option<u8> {
    let reply: Value = serde_json::from_str(line).ok()?;
    if reply.get("kind")?.as_str()? != "hello_ack"
        || reply.get("request_id")?.as_str()? != "app-startup"
    {
        return None;
    }
    reply
        .get("payload")?
        .get("protocol_version")?
        .as_u64()?
        .try_into()
        .ok()
}

impl Drop for WorkerSupervisor {
    fn drop(&mut self) {
        self.shutdown();
    }
}

#[cfg(test)]
mod tests {
    use super::{SupervisorState, WorkerSupervisor, hello_version};
    use serde_json::json;
    use std::fs;
    use std::os::unix::fs::PermissionsExt;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn accepts_only_compatible_handshake() {
        assert_eq!(
            hello_version(
                r#"{"kind":"hello_ack","request_id":"app-startup","payload":{"protocol_version":1}}"#
            ),
            Some(1)
        );
        assert_eq!(
            hello_version(r#"{"kind":"status","request_id":"app-startup","payload":{}}"#),
            None
        );
    }

    #[test]
    fn worker_exit_is_interrupted_and_restarts_from_the_fixed_path() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let script = std::env::temp_dir().join(format!("synvid-worker-crash-{nonce}.sh"));
        fs::write(
            &script,
            "#!/bin/sh\nread hello\nprintf '%s\\n' '{\"kind\":\"hello_ack\",\"request_id\":\"app-startup\",\"payload\":{\"protocol_version\":1}}'\nread request\nexit 0\n",
        ).unwrap();
        fs::set_permissions(&script, fs::Permissions::from_mode(0o700)).unwrap();

        let mut supervisor = WorkerSupervisor::new();
        supervisor.start(&script).unwrap();
        let error = supervisor.request("get_status", json!({})).unwrap_err();
        assert!(error.contains("worker interrupted"));
        assert_eq!(supervisor.state(), SupervisorState::Interrupted);

        supervisor.restart_if_interrupted().unwrap();
        assert!(matches!(
            supervisor.state(),
            SupervisorState::Ready {
                protocol_version: 1
            }
        ));
        supervisor.shutdown();
        fs::remove_file(script).unwrap();
    }

    #[test]
    fn worker_exit_is_detected_when_an_orphan_keeps_stdout_open() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let script = std::env::temp_dir().join(format!("synvid-worker-orphan-{nonce}.sh"));
        fs::write(
            &script,
            "#!/bin/sh\nread hello\nprintf '%s\\n' '{\"kind\":\"hello_ack\",\"request_id\":\"app-startup\",\"payload\":{\"protocol_version\":1}}'\nread request\nsleep 1 >&1 &\nexit 0\n",
        )
        .unwrap();
        fs::set_permissions(&script, fs::Permissions::from_mode(0o700)).unwrap();

        let mut supervisor = WorkerSupervisor::new();
        supervisor.start(&script).unwrap();
        let started = std::time::Instant::now();
        let error = supervisor.request("get_status", json!({})).unwrap_err();
        assert!(error.contains("worker interrupted"));
        assert!(started.elapsed() < std::time::Duration::from_millis(500));
        assert_eq!(supervisor.state(), SupervisorState::Interrupted);
        fs::remove_file(script).unwrap();
    }
}
