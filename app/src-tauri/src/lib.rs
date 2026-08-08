use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::sync::Mutex;
use tauri::Manager;

mod worker;
use worker::{SupervisorState, WorkerSupervisor};

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct WorkerStatus {
    connected: bool,
    protocol_version: Option<u8>,
    active_job: Option<Value>,
    measured_profile: Option<Value>,
    events: Vec<Value>,
    error: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct GenerateRequest {
    prompt: String,
    seed: i64,
    width: i64,
    height: i64,
    frames: i64,
    fps: i64,
    steps: i64,
    guidance_scale: f64,
}

fn response_payload(reply: Value) -> Result<Value, String> {
    match reply.get("kind").and_then(Value::as_str) {
        Some("error") => Err(reply
            .get("payload")
            .and_then(|value| value.get("message"))
            .and_then(Value::as_str)
            .unwrap_or("worker rejected the request")
            .to_owned()),
        _ => reply
            .get("payload")
            .cloned()
            .ok_or("worker response had no payload".into()),
    }
}

/// This intentionally exposes no process, filesystem, or shell command to the webview.
#[tauri::command]
fn worker_status(supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>) -> WorkerStatus {
    let mut supervisor = supervisor.lock().expect("worker supervisor lock poisoned");
    let protocol_version = match supervisor.state() {
        SupervisorState::Ready { protocol_version } => protocol_version,
        _ => {
            return WorkerStatus {
                connected: false,
                protocol_version: None,
                active_job: None,
                measured_profile: None,
                events: vec![],
                error: Some("Worker unavailable".into()),
            };
        }
    };
    match supervisor
        .request("get_status", json!({}))
        .and_then(response_payload)
    {
        Ok(payload) => WorkerStatus {
            connected: true,
            protocol_version: Some(protocol_version),
            active_job: payload.get("active_job").cloned(),
            measured_profile: payload
                .get("measured_profile")
                .cloned()
                .filter(|value| !value.is_null()),
            events: supervisor.take_pending_events(),
            error: None,
        },
        Err(error) => WorkerStatus {
            connected: false,
            protocol_version: None,
            active_job: None,
            measured_profile: None,
            events: supervisor.take_pending_events(),
            error: Some(error),
        },
    }
}

#[tauri::command]
fn generate(
    request: GenerateRequest,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if request.prompt.trim().is_empty() || request.prompt.len() > 4_000 {
        return Err("Prompt must contain 1 to 4000 characters.".into());
    }
    if [
        request.width,
        request.height,
        request.frames,
        request.fps,
        request.steps,
    ]
    .iter()
    .any(|value| *value <= 0)
    {
        return Err("Generation settings must be positive.".into());
    }
    let payload = json!({
        "prompt": request.prompt,
        "seed": request.seed,
        "width": request.width,
        "height": request.height,
        "frames": request.frames,
        "fps": request.fps,
        "steps": request.steps,
        "guidance_scale": request.guidance_scale,
    });
    response_payload(
        supervisor
            .lock()
            .expect("worker supervisor lock poisoned")
            .request("generate", payload)?,
    )
}

#[tauri::command]
fn cancel(
    job_id: String,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if job_id.is_empty() || job_id.len() > 128 {
        return Err("Invalid job ID.".into());
    }
    response_payload(
        supervisor
            .lock()
            .expect("worker supervisor lock poisoned")
            .request("cancel", json!({"job_id": job_id}))?,
    )
}

#[tauri::command]
fn list_outputs(supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>) -> Result<Value, String> {
    response_payload(
        supervisor.lock().expect("worker supervisor lock poisoned").request("list_outputs", json!({}))?,
    )
}

#[tauri::command]
fn recover(supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>) -> Result<Value, String> {
    response_payload(
        supervisor.lock().expect("worker supervisor lock poisoned").request("recover", json!({}))?,
    )
}

pub fn run() {
    tauri::Builder::default()
        .manage(Mutex::new(WorkerSupervisor::new()))
        .setup(|app| {
            let worker_path = app
                .path()
                .resource_dir()?
                .join("resources/worker/synvid-worker/synvid-worker");
            if let Err(error) = app
                .state::<Mutex<WorkerSupervisor>>()
                .lock()
                .expect("worker supervisor lock poisoned")
                .start(&worker_path)
            {
                eprintln!("SynVid worker unavailable: {error}");
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![worker_status, list_outputs, recover, generate, cancel])
        .run(tauri::generate_context!())
        .expect("error while running SynVid");
}
