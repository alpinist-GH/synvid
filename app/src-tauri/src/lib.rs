use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::fs;
use std::io::Read;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::Manager;

pub mod credentials;
mod worker;
use worker::{SupervisorState, WorkerSupervisor};

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct WorkerStatus {
    connected: bool,
    protocol_version: Option<u8>,
    active_job: Option<Value>,
    measured_recipes: Option<Value>,
    available_models: Option<Value>,
    events: Vec<Value>,
    error: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct GenerateRequest {
    model_id: String,
    prompt: String,
    seed: i64,
    recipe: String,
    source_image_id: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct EditVideoRequest {
    model_id: String,
    source_output_id: String,
    prompt: String,
    seed: i64,
    recipe: String,
    change_amount: f64,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct EditImageRequest {
    model_id: String,
    source_output_id: String,
    prompt: String,
    seed: i64,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct NarrateRequest {
    source_output_id: String,
    text: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct StoryCreateRequest {
    title: String,
    premise: String,
    style_bible: String,
    aspect_ratio: String,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct StoryUpdateRequest {
    story_id: String,
    expected_revision: i64,
    title: String,
    premise: String,
    style_bible: String,
    aspect_ratio: String,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct StorySceneRequest {
    story_id: String,
    expected_revision: i64,
    prompt: String,
    narration: String,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct StorySceneUpdateRequest {
    story_id: String,
    expected_revision: i64,
    scene_id: String,
    prompt: String,
    narration: String,
    approved: bool,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct StoryReorderRequest {
    story_id: String,
    expected_revision: i64,
    scene_ids: Vec<String>,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct StoryDraftRequest {
    story_id: String,
    expected_revision: i64,
    count: i64,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct StoryRenderRequest {
    story_id: String,
    expected_revision: i64,
    through: String,
    scene_ids: Option<Vec<String>>,
}

fn valid_story_id(value: &str) -> bool {
    value.len() == 36
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() || byte == b'-')
}
fn valid_story_revision(value: i64) -> bool {
    value > 0
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

/// Reports only whether a gated-model credential is available.  The token
/// itself remains in macOS Keychain and is never serialised into Tauri IPC.
#[tauri::command]
fn hugging_face_credential_status() -> Value {
    json!({"available": credentials::hugging_face_token().is_ok()})
}

/// This intentionally exposes no process, filesystem, or shell command to the webview.
#[tauri::command]
fn worker_status(supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>) -> WorkerStatus {
    let mut supervisor = supervisor.lock().expect("worker supervisor lock poisoned");
    if let Err(error) = supervisor.restart_if_interrupted() {
        return WorkerStatus {
            connected: false,
            protocol_version: None,
            active_job: None,
            measured_recipes: None,
            available_models: None,
            events: supervisor.take_pending_events(),
            error: Some(error),
        };
    }
    let protocol_version = match supervisor.state() {
        SupervisorState::Ready { protocol_version } => protocol_version,
        _ => {
            return WorkerStatus {
                connected: false,
                protocol_version: None,
                active_job: None,
                measured_recipes: None,
                available_models: None,
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
            measured_recipes: payload
                .get("measured_recipes")
                .cloned()
                .filter(|value| !value.is_null()),
            available_models: payload.get("available_models").cloned(),
            events: supervisor.take_pending_events(),
            error: None,
        },
        Err(error) => WorkerStatus {
            connected: false,
            protocol_version: None,
            active_job: None,
            measured_recipes: None,
            available_models: None,
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
    if !matches!(request.recipe.as_str(), "Draft" | "Balanced" | "High") {
        return Err("Generation recipe is not available.".into());
    }
    if !matches!(request.model_id.as_str(), "ltx-video" | "flux-schnell") {
        return Err("Selected model is not available.".into());
    }
    let payload = json!({
        "prompt": request.prompt,
        "model_id": request.model_id,
        "seed": request.seed,
        "recipe": request.recipe,
        "source_image_id": request.source_image_id,
    });
    response_payload(
        supervisor
            .lock()
            .expect("worker supervisor lock poisoned")
            .request("generate", payload)?,
    )
}

#[tauri::command]
fn edit_video(
    request: EditVideoRequest,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if request.prompt.trim().is_empty()
        || request.prompt.len() > 4_000
        || request.source_output_id.len() != 36
        || !matches!(request.recipe.as_str(), "Draft" | "Balanced" | "High")
        || !(0.05..=0.95).contains(&request.change_amount)
        || request.model_id != "ltx-video"
    {
        return Err("Invalid video edit request.".into());
    }
    response_payload(
        supervisor
            .lock()
            .expect("worker supervisor lock poisoned")
            .request(
                "edit_video",
                json!({"model_id": request.model_id, "source_output_id": request.source_output_id,
            "prompt": request.prompt, "seed": request.seed, "recipe": request.recipe,
            "change_amount": request.change_amount}),
            )?,
    )
}

#[tauri::command]
fn edit_image(
    request: EditImageRequest,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if request.prompt.trim().is_empty()
        || request.prompt.len() > 4_000
        || request.source_output_id.len() != 36
        || request.model_id != "qwen-image-edit"
    {
        return Err("Invalid image edit request.".into());
    }
    response_payload(
        supervisor
            .lock()
            .expect("worker supervisor lock poisoned")
            .request(
                "edit_image",
                json!({"model_id": request.model_id, "source_output_id": request.source_output_id,
                    "prompt": request.prompt, "seed": request.seed}),
            )?,
    )
}

#[tauri::command]
fn narrate(
    request: NarrateRequest,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if request.source_output_id.len() != 36
        || request.text.trim().is_empty()
        || request.text.len() > 4_000
    {
        return Err(
            "Narration text must contain 1 to 4000 characters for a selected video.".into(),
        );
    }
    response_payload(
        supervisor
            .lock()
            .expect("worker supervisor lock poisoned")
            .request(
                "narrate",
                json!({"source_output_id": request.source_output_id, "text": request.text}),
            )?,
    )
}

#[tauri::command]
fn story_create(
    request: StoryCreateRequest,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if request.title.trim().is_empty()
        || request.title.len() > 4_000
        || request.premise.len() > 4_000
        || request.style_bible.len() > 4_000
        || !matches!(request.aspect_ratio.as_str(), "16:9" | "9:16" | "1:1")
    {
        return Err("Invalid story details.".into());
    }
    response_payload(supervisor.lock().expect("worker supervisor lock poisoned").request("story_create", json!({"title": request.title, "premise": request.premise, "style_bible": request.style_bible, "aspect_ratio": request.aspect_ratio}))?)
}

#[tauri::command]
fn story_list(supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>) -> Result<Value, String> {
    response_payload(
        supervisor
            .lock()
            .expect("worker supervisor lock poisoned")
            .request("story_list", json!({}))?,
    )
}

#[tauri::command]
fn story_get(
    story_id: String,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if !valid_story_id(&story_id) {
        return Err("Invalid story ID.".into());
    }
    response_payload(
        supervisor
            .lock()
            .expect("worker supervisor lock poisoned")
            .request("story_get", json!({"story_id": story_id}))?,
    )
}

#[tauri::command]
fn story_update(
    request: StoryUpdateRequest,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if !valid_story_id(&request.story_id)
        || !valid_story_revision(request.expected_revision)
        || request.title.trim().is_empty()
        || request.title.len() > 4_000
        || request.premise.len() > 4_000
        || request.style_bible.len() > 4_000
        || !matches!(request.aspect_ratio.as_str(), "16:9" | "9:16" | "1:1")
    {
        return Err("Invalid story update.".into());
    }
    response_payload(supervisor.lock().expect("worker supervisor lock poisoned").request("story_update", json!({"story_id": request.story_id, "expected_revision": request.expected_revision, "title": request.title, "premise": request.premise, "style_bible": request.style_bible, "aspect_ratio": request.aspect_ratio}))?)
}

#[tauri::command]
fn story_add_scene(
    request: StorySceneRequest,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if !valid_story_id(&request.story_id)
        || !valid_story_revision(request.expected_revision)
        || request.prompt.len() > 4_000
        || request.narration.len() > 4_000
    {
        return Err("Invalid scene.".into());
    }
    response_payload(supervisor.lock().expect("worker supervisor lock poisoned").request("story_add_scene", json!({"story_id": request.story_id, "expected_revision": request.expected_revision, "prompt": request.prompt, "narration": request.narration}))?)
}

#[tauri::command]
fn story_update_scene(
    request: StorySceneUpdateRequest,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if !valid_story_id(&request.story_id)
        || !valid_story_id(&request.scene_id)
        || !valid_story_revision(request.expected_revision)
        || request.prompt.len() > 4_000
        || request.narration.len() > 4_000
    {
        return Err("Invalid scene update.".into());
    }
    response_payload(supervisor.lock().expect("worker supervisor lock poisoned").request("story_update_scene", json!({"story_id": request.story_id, "expected_revision": request.expected_revision, "scene_id": request.scene_id, "prompt": request.prompt, "narration": request.narration, "approved": request.approved}))?)
}

#[tauri::command]
fn story_reorder_scenes(
    request: StoryReorderRequest,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if !valid_story_id(&request.story_id)
        || !valid_story_revision(request.expected_revision)
        || request.scene_ids.len() > 64
        || !request.scene_ids.iter().all(|item| valid_story_id(item))
    {
        return Err("Invalid scene order.".into());
    }
    response_payload(supervisor.lock().expect("worker supervisor lock poisoned").request("story_reorder_scenes", json!({"story_id": request.story_id, "expected_revision": request.expected_revision, "scene_ids": request.scene_ids}))?)
}

#[tauri::command]
fn story_draft_scenes(
    request: StoryDraftRequest,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if !valid_story_id(&request.story_id)
        || !valid_story_revision(request.expected_revision)
        || !(1..=8).contains(&request.count)
    {
        return Err("Invalid story draft request.".into());
    }
    response_payload(supervisor.lock().expect("worker supervisor lock poisoned").request("story_draft_scenes", json!({"story_id": request.story_id, "expected_revision": request.expected_revision, "count": request.count}))?)
}

#[tauri::command]
fn render_story(
    request: StoryRenderRequest,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if !valid_story_id(&request.story_id)
        || !valid_story_revision(request.expected_revision)
        || !matches!(request.through.as_str(), "still" | "clip" | "narration")
        || request
            .scene_ids
            .as_ref()
            .is_some_and(|ids| ids.len() > 64 || !ids.iter().all(|id| valid_story_id(id)))
    {
        return Err("Invalid story render request.".into());
    }
    response_payload(supervisor.lock().expect("worker supervisor lock poisoned").request("render_story", json!({"story_id": request.story_id, "expected_revision": request.expected_revision, "through": request.through, "scene_ids": request.scene_ids}))?)
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
        supervisor
            .lock()
            .expect("worker supervisor lock poisoned")
            .request("list_outputs", json!({}))?,
    )
}

#[tauri::command]
fn recovery_preview(
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    response_payload(
        supervisor
            .lock()
            .expect("worker supervisor lock poisoned")
            .request("recovery_preview", json!({}))?,
    )
}

#[tauri::command]
fn recover(supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>) -> Result<Value, String> {
    response_payload(
        supervisor
            .lock()
            .expect("worker supervisor lock poisoned")
            .request("recover", json!({}))?,
    )
}

#[tauri::command]
fn export_video(
    output_id: String,
    profile: String,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if output_id.is_empty()
        || output_id.len() > 128
        || !matches!(profile.as_str(), "high" | "balanced" | "small")
    {
        return Err("Invalid export request.".into());
    }
    response_payload(
        supervisor
            .lock()
            .expect("worker supervisor lock poisoned")
            .request(
                "export_video",
                json!({"output_id": output_id, "profile": profile}),
            )?,
    )
}

fn is_supported_image(path: &std::path::Path) -> Result<bool, String> {
    let metadata = fs::symlink_metadata(path).map_err(|_| "The selected image is unavailable.")?;
    if metadata.file_type().is_symlink()
        || !metadata.is_file()
        || metadata.len() == 0
        || metadata.len() > 64 * 1024 * 1024
    {
        return Ok(false);
    }
    let mut header = [0_u8; 12];
    let mut file = fs::File::open(path).map_err(|_| "The selected image is unavailable.")?;
    let count = file
        .read(&mut header)
        .map_err(|_| "The selected image could not be read.")?;
    Ok(
        (count >= 8 && header[..8] == [137, 80, 78, 71, 13, 10, 26, 10])
            || (count >= 3 && header[..3] == [255, 216, 255])
            || (count >= 12 && header[..4] == *b"RIFF" && header[8..12] == *b"WEBP"),
    )
}

/// Opens the native picker in Rust, verifies a regular image, and copies it
/// into the app-owned root. The webview never handles media bytes or paths.
#[tauri::command]
fn choose_source_image(app: tauri::AppHandle) -> Result<Value, String> {
    let Some(source) = rfd::FileDialog::new()
        .add_filter("Image", &["png", "jpg", "jpeg", "webp"])
        .pick_file()
    else {
        return Ok(json!({"sourceImageId": null}));
    };
    if !is_supported_image(&source)? {
        return Err("Choose a PNG, JPEG, or WebP image smaller than 64 MB.".into());
    }
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "System clock is unavailable.")?
        .as_nanos();
    let id = format!("image-{nonce:x}");
    let imports = app
        .path()
        .home_dir()
        .map_err(|_| "Home directory is unavailable.")?
        .join("Library/Application Support/SynVid/temporary/imports");
    fs::create_dir_all(&imports).map_err(|_| "Could not prepare secure image storage.")?;
    let destination = imports.join(&id);
    fs::copy(&source, &destination).map_err(|_| "Could not import the selected image.")?;
    Ok(json!({"sourceImageId": id}))
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
        .invoke_handler(tauri::generate_handler![
            hugging_face_credential_status,
            worker_status,
            list_outputs,
            recovery_preview,
            recover,
            generate,
            edit_video,
            edit_image,
            narrate,
            story_create,
            story_list,
            story_get,
            story_update,
            story_add_scene,
            story_update_scene,
            story_reorder_scenes,
            story_draft_scenes,
            render_story,
            export_video,
            choose_source_image,
            cancel
        ])
        .run(tauri::generate_context!())
        .expect("error while running SynVid");
}
