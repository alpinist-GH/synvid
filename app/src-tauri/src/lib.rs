use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::fs;
use std::io::Read;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::Manager;

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
    trim_start_seconds: f64,
    trim_end_seconds: f64,
    narration_muted: bool,
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
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct StoryImportStillRequest {
    story_id: String,
    expected_revision: i64,
    scene_id: String,
    source_image_id: String,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct StoryImportSubtitlesRequest {
    story_id: String,
    expected_revision: i64,
    scene_id: String,
    source_subtitle_id: String,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct StoryImportNarrationRequest {
    story_id: String,
    expected_revision: i64,
    scene_id: String,
    source_audio_id: String,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct StoryImportClipRequest {
    story_id: String,
    expected_revision: i64,
    scene_id: String,
    source_clip_id: String,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct StoryProjectExportRequest {
    story_id: String,
    self_contained: bool,
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
        || !request.trim_start_seconds.is_finite()
        || !request.trim_end_seconds.is_finite()
        || request.trim_start_seconds < 0.0
        || request.trim_end_seconds < 0.0
        || (request.trim_end_seconds > 0.0
            && request.trim_end_seconds <= request.trim_start_seconds)
    {
        return Err("Invalid scene update.".into());
    }
    response_payload(supervisor.lock().expect("worker supervisor lock poisoned").request("story_update_scene", json!({"story_id": request.story_id, "expected_revision": request.expected_revision, "scene_id": request.scene_id, "prompt": request.prompt, "narration": request.narration, "approved": request.approved, "trim_start_seconds": request.trim_start_seconds, "trim_end_seconds": request.trim_end_seconds, "narration_muted": request.narration_muted}))?)
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
        || !matches!(
            request.through.as_str(),
            "still" | "clip" | "narration" | "subtitles"
        )
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
fn compose_story(
    request: StoryDraftRequest,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if !valid_story_id(&request.story_id) || !valid_story_revision(request.expected_revision) {
        return Err("Invalid story composition request.".into());
    }
    response_payload(supervisor.lock().expect("worker supervisor lock poisoned").request("compose_story", json!({"story_id": request.story_id, "expected_revision": request.expected_revision}))?)
}

#[tauri::command]
fn story_import_still(
    request: StoryImportStillRequest,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if !valid_story_id(&request.story_id)
        || !valid_story_id(&request.scene_id)
        || !valid_story_revision(request.expected_revision)
        || !request.source_image_id.starts_with("image-")
        || request.source_image_id.len() > 128
    {
        return Err("Invalid story image import request.".into());
    }
    response_payload(supervisor.lock().expect("worker supervisor lock poisoned").request("story_import_still", json!({"story_id": request.story_id, "expected_revision": request.expected_revision, "scene_id": request.scene_id, "source_image_id": request.source_image_id}))?)
}

#[tauri::command]
fn story_import_subtitles(
    request: StoryImportSubtitlesRequest,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if !valid_story_id(&request.story_id)
        || !valid_story_id(&request.scene_id)
        || !valid_story_revision(request.expected_revision)
        || !request.source_subtitle_id.starts_with("subtitle-")
        || request.source_subtitle_id.len() > 128
    {
        return Err("Invalid story subtitle import request.".into());
    }
    response_payload(supervisor.lock().expect("worker supervisor lock poisoned").request("story_import_subtitles", json!({"story_id": request.story_id, "expected_revision": request.expected_revision, "scene_id": request.scene_id, "source_subtitle_id": request.source_subtitle_id}))?)
}

#[tauri::command]
fn story_import_narration(
    request: StoryImportNarrationRequest,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if !valid_story_id(&request.story_id)
        || !valid_story_id(&request.scene_id)
        || !valid_story_revision(request.expected_revision)
        || !request.source_audio_id.starts_with("audio-")
        || request.source_audio_id.len() > 128
    {
        return Err("Invalid story narration import request.".into());
    }
    response_payload(supervisor.lock().expect("worker supervisor lock poisoned").request("story_import_narration", json!({"story_id": request.story_id, "expected_revision": request.expected_revision, "scene_id": request.scene_id, "source_audio_id": request.source_audio_id}))?)
}

#[tauri::command]
fn story_import_clip(
    request: StoryImportClipRequest,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if !valid_story_id(&request.story_id)
        || !valid_story_id(&request.scene_id)
        || !valid_story_revision(request.expected_revision)
        || !request.source_clip_id.starts_with("clip-")
    {
        return Err("Invalid story clip import request.".into());
    }
    response_payload(supervisor.lock().expect("worker supervisor lock poisoned").request("story_import_clip", json!({"story_id": request.story_id, "expected_revision": request.expected_revision, "scene_id": request.scene_id, "source_clip_id": request.source_clip_id}))?)
}

#[tauri::command]
fn story_export_project(
    request: StoryProjectExportRequest,
    app: tauri::AppHandle,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if !valid_story_id(&request.story_id) {
        return Err("Invalid story project export request.".into());
    }
    let response = response_payload(
        supervisor
            .lock()
            .expect("worker supervisor lock poisoned")
            .request(
                "story_export_project",
                json!({"story_id": request.story_id, "self_contained": request.self_contained}),
            )?,
    )?;
    let archive_name = response
        .get("archive_name")
        .and_then(Value::as_str)
        .ok_or("Worker returned an invalid project archive.")?;
    if archive_name != format!("{}.synvidstory", request.story_id) {
        return Err("Worker returned an invalid project archive.".into());
    }
    let Some(destination) = rfd::FileDialog::new()
        .set_file_name(archive_name)
        .save_file()
    else {
        return Ok(json!({"saved": false}));
    };
    let source = app
        .path()
        .home_dir()
        .map_err(|_| "Home directory is unavailable.")?
        .join("Library/Application Support/SynVid/stories/exports")
        .join(archive_name);
    if !source.is_file() || source.is_symlink() {
        return Err("The exported project archive is unavailable.".into());
    }
    fs::copy(source, destination).map_err(|_| "Could not save the project archive.")?;
    Ok(json!({"saved": true, "self_contained": request.self_contained}))
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
fn delete_output(
    output_id: String,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if output_id.len() != 36
        || !output_id
            .chars()
            .all(|character| character.is_ascii_hexdigit() || character == '-')
    {
        return Err("Invalid output deletion request.".into());
    }
    response_payload(
        supervisor
            .lock()
            .expect("worker supervisor lock poisoned")
            .request("delete_output", json!({"output_id": output_id}))?,
    )
}

#[tauri::command]
fn model_catalog(supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>) -> Result<Value, String> {
    response_payload(
        supervisor
            .lock()
            .expect("worker supervisor lock poisoned")
            .request("model_catalog", json!({}))?,
    )
}

#[tauri::command]
fn download_model(model_id: String, supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>) -> Result<Value, String> {
    if model_id.len() > 64 || !model_id.bytes().all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-') {
        return Err("Invalid model download request.".into());
    }
    response_payload(supervisor.lock().expect("worker supervisor lock poisoned").request("download_model", json!({"model_id": model_id}))?)
}

#[tauri::command]
fn remove_model(
    model_id: String,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if model_id.len() > 64
        || !model_id.chars().all(|character| {
            character.is_ascii_lowercase() || character.is_ascii_digit() || character == '-'
        })
    {
        return Err("Invalid model removal request.".into());
    }
    response_payload(
        supervisor
            .lock()
            .expect("worker supervisor lock poisoned")
            .request("remove_model", json!({"model_id": model_id}))?,
    )
}

#[tauri::command]
fn clean_temporary(supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>) -> Result<Value, String> {
    response_payload(
        supervisor
            .lock()
            .expect("worker supervisor lock poisoned")
            .request("clean_temporary", json!({}))?,
    )
}

#[tauri::command]
fn output_media_path(
    app: tauri::AppHandle,
    output_id: String,
    media_file: String,
) -> Result<String, String> {
    if output_id.len() != 36
        || !output_id
            .chars()
            .all(|character| character.is_ascii_hexdigit() || character == '-')
        || !matches!(media_file.as_str(), "video.mp4" | "image.png")
    {
        return Err("Invalid output media request.".into());
    }
    let path = app
        .path()
        .home_dir()
        .map_err(|_| "Home directory is unavailable.")?
        .join("Library/Application Support/SynVid/outputs")
        .join(output_id)
        .join(media_file);
    let path = match path {
        path if path.is_file() && !path.is_symlink() => path,
        _ => return Err("The selected output media is unavailable.".into()),
    };
    path.to_str()
        .map(str::to_owned)
        .ok_or_else(|| "The selected output media has an unsupported path.".into())
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
    app: tauri::AppHandle,
    output_id: String,
    profile: String,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if output_id.len() != 36
        || !output_id
            .chars()
            .all(|character| character.is_ascii_hexdigit() || character == '-')
        || !matches!(profile.as_str(), "high" | "balanced" | "small")
    {
        return Err("Invalid export request.".into());
    }
    let exported = response_payload(
        supervisor
            .lock()
            .expect("worker supervisor lock poisoned")
            .request(
                "export_video",
                json!({"output_id": output_id, "profile": profile}),
            )?,
    )?;
    let Some(destination) = rfd::FileDialog::new()
        .add_filter("MPEG-4 Video", &["mp4"])
        .set_file_name(format!("SynVid-{}-{}.mp4", &output_id[..8], profile))
        .save_file()
    else {
        return Ok(json!({"saved": false}));
    };
    let destination = if destination.extension().is_none() {
        destination.with_extension("mp4")
    } else {
        destination
    };
    if destination
        .extension()
        .and_then(|extension| extension.to_str())
        != Some("mp4")
    {
        return Err("Export filenames must use the .mp4 extension.".into());
    }
    if destination.exists() {
        return Err("That file already exists. Choose a different final filename.".into());
    }
    let source = app
        .path()
        .home_dir()
        .map_err(|_| "Home directory is unavailable.")?
        .join("Library/Application Support/SynVid/outputs")
        .join(&output_id)
        .join("exports")
        .join(&profile)
        .with_extension("mp4");
    if !source.is_file() || source.is_symlink() {
        return Err("The rendered export is unavailable.".into());
    }
    let parent = destination
        .parent()
        .filter(|path| path.is_dir())
        .ok_or("The selected export folder is unavailable.")?;
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "System clock is unavailable.")?
        .as_nanos();
    let temporary = parent.join(format!(".synvid-export-{nonce}.partial"));
    fs::copy(&source, &temporary).map_err(|_| "Could not save the final export.")?;
    if fs::hard_link(&temporary, &destination).is_err() {
        let _ = fs::remove_file(&temporary);
        return Err("Could not save the final export; choose a different filename.".into());
    }
    fs::remove_file(&temporary).map_err(|_| "Could not finalize the saved export.")?;
    Ok(
        json!({"saved": true, "profile": exported["profile"], "file_name": destination.file_name().and_then(|name| name.to_str())}),
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

#[tauri::command]
fn choose_story_subtitles(app: tauri::AppHandle) -> Result<Value, String> {
    let Some(source) = rfd::FileDialog::new()
        .add_filter("SubRip subtitles", &["srt"])
        .pick_file()
    else {
        return Ok(json!({"sourceSubtitleId": null}));
    };
    let metadata =
        fs::symlink_metadata(&source).map_err(|_| "The selected subtitle file is unavailable.")?;
    if metadata.file_type().is_symlink()
        || !metadata.is_file()
        || metadata.len() == 0
        || metadata.len() > 4 * 1024 * 1024
    {
        return Err("Choose a regular SRT file smaller than 4 MB.".into());
    }
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "System clock is unavailable.")?
        .as_nanos();
    let id = format!("subtitle-{nonce:x}");
    let imports = app
        .path()
        .home_dir()
        .map_err(|_| "Home directory is unavailable.")?
        .join("Library/Application Support/SynVid/temporary/imports");
    fs::create_dir_all(&imports).map_err(|_| "Could not prepare secure subtitle storage.")?;
    fs::copy(&source, imports.join(&id))
        .map_err(|_| "Could not import the selected subtitle file.")?;
    Ok(json!({"sourceSubtitleId": id}))
}

#[tauri::command]
fn choose_story_narration(app: tauri::AppHandle) -> Result<Value, String> {
    let Some(source) = rfd::FileDialog::new()
        .add_filter("WAV audio", &["wav"])
        .pick_file()
    else {
        return Ok(json!({"sourceAudioId": null}));
    };
    let metadata =
        fs::symlink_metadata(&source).map_err(|_| "The selected narration file is unavailable.")?;
    if metadata.file_type().is_symlink()
        || !metadata.is_file()
        || metadata.len() == 0
        || metadata.len() > 64 * 1024 * 1024
    {
        return Err("Choose a regular WAV file smaller than 64 MB.".into());
    }
    let mut header = [0_u8; 12];
    fs::File::open(&source)
        .map_err(|_| "The selected narration file is unavailable.")?
        .read_exact(&mut header)
        .map_err(|_| "The selected narration file is invalid.")?;
    if header[..4] != *b"RIFF" || header[8..12] != *b"WAVE" {
        return Err("Choose a valid WAV file.".into());
    }
    let id = format!(
        "audio-{:x}",
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| "System clock is unavailable.")?
            .as_nanos()
    );
    let imports = app
        .path()
        .home_dir()
        .map_err(|_| "Home directory is unavailable.")?
        .join("Library/Application Support/SynVid/temporary/imports");
    fs::create_dir_all(&imports).map_err(|_| "Could not prepare secure narration storage.")?;
    fs::copy(&source, imports.join(&id))
        .map_err(|_| "Could not import the selected narration file.")?;
    Ok(json!({"sourceAudioId": id}))
}

#[tauri::command]
fn choose_story_clip(app: tauri::AppHandle) -> Result<Value, String> {
    let Some(source) = rfd::FileDialog::new()
        .add_filter("Video", &["mp4", "mov"])
        .pick_file()
    else {
        return Ok(json!({"sourceClipId": null}));
    };
    let metadata =
        fs::symlink_metadata(&source).map_err(|_| "The selected motion clip is unavailable.")?;
    if metadata.file_type().is_symlink()
        || !metadata.is_file()
        || metadata.len() == 0
        || metadata.len() > 512 * 1024 * 1024
    {
        return Err("Choose a regular MP4 or MOV clip smaller than 512 MB.".into());
    }
    let mut header = [0_u8; 12];
    fs::File::open(&source)
        .map_err(|_| "The selected motion clip is unavailable.")?
        .read_exact(&mut header)
        .map_err(|_| "The selected motion clip is invalid.")?;
    if &header[4..8] != b"ftyp" {
        return Err("Choose a valid MP4 or MOV clip.".into());
    }
    let id = format!(
        "clip-{:x}",
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| "System clock is unavailable.")?
            .as_nanos()
    );
    let imports = app
        .path()
        .home_dir()
        .map_err(|_| "Home directory is unavailable.")?
        .join("Library/Application Support/SynVid/temporary/imports");
    fs::create_dir_all(&imports).map_err(|_| "Could not prepare secure clip storage.")?;
    fs::copy(&source, imports.join(&id))
        .map_err(|_| "Could not import the selected motion clip.")?;
    Ok(json!({"sourceClipId": id}))
}

#[tauri::command]
fn choose_story_project(app: tauri::AppHandle) -> Result<Value, String> {
    let Some(source) = rfd::FileDialog::new()
        .add_filter("SynVid Story Project", &["synvidstory"])
        .pick_file()
    else {
        return Ok(json!({"sourceProjectId": null}));
    };
    let metadata =
        fs::symlink_metadata(&source).map_err(|_| "The selected story project is unavailable.")?;
    if metadata.file_type().is_symlink()
        || !metadata.is_file()
        || metadata.len() == 0
        || metadata.len() > 2 * 1024 * 1024 * 1024
    {
        return Err("Choose a regular SynVid story project smaller than 2 GB.".into());
    }
    let id = format!(
        "storyproj-{:x}",
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| "System clock is unavailable.")?
            .as_nanos()
    );
    let imports = app
        .path()
        .home_dir()
        .map_err(|_| "Home directory is unavailable.")?
        .join("Library/Application Support/SynVid/temporary/imports");
    fs::create_dir_all(&imports).map_err(|_| "Could not prepare secure story project storage.")?;
    fs::copy(&source, imports.join(&id))
        .map_err(|_| "Could not import the selected story project.")?;
    Ok(json!({"sourceProjectId": id}))
}

#[tauri::command]
fn story_import_project(
    source_project_id: String,
    supervisor: tauri::State<'_, Mutex<WorkerSupervisor>>,
) -> Result<Value, String> {
    if !source_project_id.starts_with("storyproj-") || source_project_id.len() > 128 {
        return Err("Invalid story project import request.".into());
    }
    response_payload(
        supervisor
            .lock()
            .expect("worker supervisor lock poisoned")
            .request(
                "story_import_project",
                json!({"source_project_id": source_project_id}),
            )?,
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
        .invoke_handler(tauri::generate_handler![
            worker_status,
            list_outputs,
            delete_output,
            model_catalog,
            download_model,
            remove_model,
            clean_temporary,
            output_media_path,
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
            compose_story,
            story_import_still,
            story_import_subtitles,
            story_import_narration,
            story_import_clip,
            story_export_project,
            story_import_project,
            export_video,
            choose_source_image,
            choose_story_subtitles,
            choose_story_narration,
            choose_story_clip,
            choose_story_project,
            cancel
        ])
        .run(tauri::generate_context!())
        .expect("error while running SynVid");
}
