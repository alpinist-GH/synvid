// This frontend is deliberately vanilla static assets, not an npm bundle.
// Tauri exposes this narrow API only because `withGlobalTauri` is enabled in
// the checked-in desktop configuration.
const invoke = window.__TAURI__.core.invoke;
const convertFileSrc = window.__TAURI__.core.convertFileSrc;

const DRAFT_KEY = "synvid.stage2.draft.v1";
const ONBOARDING_KEY = "synvid.stage2.onboarding.v1";
const MAX_HISTORY = 20;
// The evaluated compact planner failed the Stage 7 adversarial JSON gate.
// Do not expose an optional model feature merely because its files are present.
const STORY_PLANNER_AVAILABLE = false;
const state = { recipes: null, models: null, modelId: "ltx-video", activeJob: null, connected: false, recipe: "Balanced", mode: "text", sourceImageId: null, variants: [], selectedVariant: null, history: [], historyIndex: -1 };
let activeStory = null; let activeSceneId = null; let draftProposals = [];
const $ = (selector) => document.querySelector(selector);
const connection = $("#connection");
const jobStatus = $("#job-status");
const generateButton = $("#generate");
const cancelButton = $("#cancel");
const error = $("#form-error");
const generationProgress = $("#generation-progress");

function setError(message = "") { error.textContent = message; error.hidden = !message; }
function renderGenerationProgress(job) {
  generationProgress.hidden = !job;
  generationProgress.value = job ? Math.round(Math.max(0, Math.min(1, Number(job.progress) || 0)) * 100) : 0;
}
function selectedModel() { return state.models?.[state.modelId] || null; }
function isImageModel() { return selectedModel()?.capabilities?.includes("image_generation") || false; }
function profileLabel(profile) { return `${profile.width} × ${profile.height} · ${profile.frames ? `${profile.frames} frames · ` : ""}${profile.steps} steps`; }
function draft() { return { prompt: $("#prompt").value, seed: $("#seed").value, recipe: state.recipe }; }
function saveDraft() { localStorage.setItem(DRAFT_KEY, JSON.stringify(draft())); }
function renderHistory() { $("#undo").disabled = state.historyIndex <= 0; $("#redo").disabled = state.historyIndex >= state.history.length - 1; }
function saveHistory() {
  const next = JSON.stringify(draft());
  if (state.history[state.historyIndex] === next) return;
  state.history = state.history.slice(0, state.historyIndex + 1);
  state.history.push(next);
  if (state.history.length > MAX_HISTORY) state.history.shift();
  state.historyIndex = state.history.length - 1;
  renderHistory(); saveDraft();
}
function applyDraft(raw) {
  const value = JSON.parse(raw);
  $("#prompt").value = typeof value.prompt === "string" ? value.prompt : "";
  $("#seed").value = /^\d+$/.test(String(value.seed)) ? value.seed : "42";
  setRecipe(["Draft", "Balanced", "High"].includes(value.recipe) ? value.recipe : "Balanced");
}
function restoreDraft() {
  try { const value = localStorage.getItem(DRAFT_KEY); if (value) applyDraft(value); } catch { localStorage.removeItem(DRAFT_KEY); }
  state.history = [JSON.stringify(draft())]; state.historyIndex = 0; renderHistory();
}
function setRecipe(recipe) {
  state.recipe = recipe;
  for (const button of document.querySelectorAll("[data-recipe]")) button.setAttribute("aria-checked", String(button.dataset.recipe === recipe));
  const profile = activeProfile();
  $("#recipe-note").textContent = profile ? `${recipe}: ${profile.steps} steps at ${profile.width} × ${profile.height}; measured on this Mac.` : `${recipe} has not been measured on this Mac.`;
  $("#advanced-note").textContent = "Custom overrides are unavailable: only the measured recipe map may be submitted.";
}
function activeProfile() { const model = selectedModel(); return isImageModel() ? model?.measured_image_profile : model?.measured_recipes?.[state.recipe] || null; }
function updateControls() {
  const profile = activeProfile(); const available = state.connected && profile && !state.activeJob;
  generateButton.disabled = !available; cancelButton.hidden = !state.activeJob;
  renderGenerationProgress(state.activeJob);
  $("#profile").textContent = profile ? profileLabel(profile) : "Not available";
  $("#fps").textContent = profile && !isImageModel() ? `${profile.fps} FPS (Native)` : "—";
  generateButton.textContent = isImageModel() ? "Generate image" : "Generate video";
  const imageMode = document.querySelector('[data-mode="image"]'); imageMode.disabled = isImageModel();
  if (isImageModel() && state.mode === "image") { state.mode = "text"; state.sourceImageId = null; }
  $("#choose-image").hidden = state.mode !== "image" || isImageModel(); $("#source-image-status").hidden = state.mode !== "image" || isImageModel();
}
function renderVariants() {
  const list = $("#variant-list"); list.replaceChildren();
  $("#variant-note").textContent = state.variants.length ? `${state.variants.length} local variant${state.variants.length === 1 ? "" : "s"}. Promotion does not alter source media.` : "No generated variants yet.";
  for (const variant of state.variants) {
    const item = document.createElement("li"); const button = document.createElement("button");
    button.type = "button"; button.className = "variant"; button.setAttribute("aria-pressed", String(variant.outputId === state.selectedVariant));
    button.textContent = `${variant.outputId} · seed ${variant.seed}`;
    button.addEventListener("click", () => promoteVariant(variant)); item.append(button); list.append(item);
  }
}
function promoteVariant(variant) {
  state.selectedVariant = variant.outputId;
  const isVideo = variant.mediaFile === "video.mp4";
  $("#result-message").textContent = `Selected ${variant.outputId}. The canonical output remains immutable.`;
  $("#export-controls").hidden = !isVideo;
  $("#image-edit-controls").hidden = variant.mediaFile !== "image.png";
  $("#video-edit-controls").hidden = true;
  $("#voice-controls").hidden = true;
  void previewVariant(variant);
  renderVariants();
}
async function previewVariant(variant) {
  const preview = $("#media-preview"); const video = $("#video-preview"); const image = $("#image-preview"); const status = $("#preview-status");
  preview.hidden = false; video.hidden = true; image.hidden = true; video.pause(); video.removeAttribute("src"); image.removeAttribute("src");
  status.textContent = "Loading local preview…";
  try {
    const path = await invoke("output_media_path", { outputId: variant.outputId, mediaFile: variant.mediaFile });
    const source = convertFileSrc(path);
    if (variant.mediaFile === "video.mp4") {
      video.src = source; video.hidden = false; video.load();
      status.textContent = "Preview ready. Use the video controls to play, pause, seek, or adjust volume.";
    } else if (variant.mediaFile === "image.png") {
      image.src = source; image.hidden = false;
      status.textContent = "Preview ready.";
    } else {
      preview.hidden = true;
    }
  } catch (reason) {
    status.textContent = "Preview unavailable. The completed output remains in your local Library.";
    setError(String(reason));
  }
}
function recordTerminal(events) {
  for (const event of events) {
    if (event.kind !== "terminal") continue;
    const payload = event.payload ?? {};
    if (payload.state === "succeeded" && payload.story_draft?.scenes) {
      draftProposals = payload.story_draft.scenes; renderDraftProposals(); $("#story-draft-note").textContent = "Choose a proposal to copy it into editable scene fields; it is not saved yet."; $("#draft-story-scenes").disabled = false;
    } else if (payload.state && payload.story_draft) {
      $("#story-draft-note").textContent = payload.error || `Story drafting ${payload.state}.`; $("#draft-story-scenes").disabled = false;
    } else if (payload.state === "succeeded" && payload.output_id && !state.variants.some((item) => item.outputId === payload.output_id)) {
      const variant = { outputId: payload.output_id, seed: $("#seed").value, mediaFile: isImageModel() ? "image.png" : "video.mp4" };
      state.variants.unshift(variant); promoteVariant(variant); jobStatus.textContent = "Generation completed and saved atomically.";
    } else if (payload.state) jobStatus.textContent = payload.error || `Generation ${payload.state}.`;
  }
}
async function refresh() {
  try {
    const status = await invoke("worker_status"); state.connected = status.connected; state.models = status.availableModels; state.recipes = status.measuredRecipes; state.activeJob = status.activeJob;
    connection.textContent = status.connected ? `Ready · worker protocol v${status.protocolVersion}` : status.error || "Worker unavailable";
    if (state.activeJob) jobStatus.textContent = `${state.activeJob.status_text || state.activeJob.statusText || "Generating"} · ${Math.round((state.activeJob.progress || 0) * 100)}%`;
    recordTerminal(status.events || []); updateControls();
  } catch { state.connected = false; connection.textContent = "Worker unavailable"; updateControls(); }
}
async function showLibrary() {
  const dialog = $("#library-dialog"); const list = $("#library-list"); list.replaceChildren();
  try {
    const { outputs = [] } = await invoke("list_outputs");
    for (const output of outputs) { const item = document.createElement("li"); const button = document.createElement("button"); button.type = "button"; button.className = "variant"; button.textContent = `${output.output_id} · ${output.prompt || "Untitled"}`; button.addEventListener("click", () => { promoteVariant({ outputId: output.output_id, seed: output.seed ?? "unknown", mediaFile: output.media_file }); dialog.close(); }); item.append(button); list.append(item); }
    if (!outputs.length) list.textContent = "No completed local outputs yet.";
  } catch { list.textContent = "The local library is unavailable while the worker is disconnected."; }
  dialog.showModal();
}
async function showRecovery() {
  const dialog = $("#recovery-dialog"); $("#recovery-preview").textContent = "Checking recoverable state…"; dialog.showModal();
  try {
    const preview = await invoke("recovery_preview");
    const count = preview.partialOutputCount ?? preview.partial_output_count ?? 0;
    const reserved = preview.reservedBytes ?? preview.reserved_bytes ?? 0;
    $("#recovery-preview").textContent = `${count} incomplete output${count === 1 ? "" : "s"} and ${reserved} reserved byte${reserved === 1 ? "" : "s"} can be safely recovered. Completed output will not be deleted.`;
  } catch { $("#recovery-preview").textContent = "Worker unavailable. Reopen SynVid to inspect recovery state."; }
}
function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"]; const power = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / (1024 ** power)).toFixed(power < 3 ? 0 : 1)} ${units[power]}`;
}
function renderModelCatalog(models) {
  const list = $("#model-list"); list.replaceChildren();
  for (const model of models) {
    const item = document.createElement("article"); item.className = "model-card";
    const title = document.createElement("h4"); title.textContent = model.display_name;
    const reason = document.createElement("p"); reason.textContent = model.reason;
    const facts = document.createElement("p"); facts.className = "field-help";
    facts.textContent = `${model.license} · ${model.expected_size_gib} GB expected · ${model.profile}${model.requires_access_confirmation ? " · access confirmation required" : ""}`;
    const status = document.createElement("p"); status.className = "model-status";
    status.textContent = model.installed
      ? `Installed · ${formatBytes(model.installed_bytes)} on disk`
      : "Not downloaded · shown for planning only until its explicit download approval.";
    item.append(title, reason, facts, status);
    if (model.installed) {
      const remove = document.createElement("button"); remove.type = "button"; remove.className = "danger"; remove.textContent = "Remove model";
      remove.addEventListener("click", async () => {
        if (!window.confirm(`Remove ${model.display_name}? This deletes only its SynVid model files and cannot be undone.`)) return;
        remove.disabled = true;
        try { const result = await invoke("remove_model", { modelId: model.model_id }); $("#cleanup-status").textContent = result.removed ? `${model.display_name} removed; freed ${formatBytes(result.freed_bytes)}.` : `${model.display_name} was already absent.`; await showSettings(); }
        catch (reason) { setError(String(reason)); remove.disabled = false; }
      });
      item.append(remove);
    } else {
      const download = document.createElement("button"); download.type = "button"; download.textContent = "Download model…";
      download.addEventListener("click", () => {
        const access = model.requires_access_confirmation ? " Access approval is required." : "";
        const approved = window.confirm(`Download ${model.display_name}?\n\nRevision: ${model.revision}\nExpected size: ${model.expected_size_gib} GB\nLicense: ${model.license}.${access}\n\nSynVid will request a final authorization before any network transfer.`);
        if (!approved) return;
        download.disabled = true;
        try {
          const accepted = await invoke("download_model", { modelId: model.model_id });
          state.activeJob = { job_id: accepted.job_id || accepted.jobId, status_text: `Downloading ${model.display_name}`, progress: 0 };
          jobStatus.textContent = `Downloading ${model.display_name}…`;
          updateControls();
        } catch (reason) { setError(String(reason)); download.disabled = false; }
      });
      item.append(download);
    }
    list.append(item);
  }
}
async function showSettings() {
  const dialog = $("#settings-dialog"); $("#model-list").textContent = "Loading model catalog…"; if (!dialog.open) dialog.showModal();
  try { const { models = [] } = await invoke("model_catalog"); renderModelCatalog(models); }
  catch (reason) { $("#model-list").textContent = "Model catalog unavailable while the worker is disconnected."; setError(String(reason)); }
}
async function showAbout() {
  const dialog = $("#about-dialog"); $("#about-version").textContent = "Version…"; dialog.showModal();
  try { $("#about-version").textContent = `Version ${await window.__TAURI__.app.getVersion()}`; }
  catch { $("#about-version").textContent = "Version unavailable"; }
}
function renderStory(story) {
  activeStory = story; if (!story) activeSceneId = null; $("#story-name").value = story?.title || ""; $("#story-premise").value = story?.premise || ""; $("#story-style").value = story?.style_bible || ""; $("#story-aspect").value = story?.aspect_ratio || "16:9";
  $("#story-revision").textContent = story ? `Revision ${story.revision} · ${story.scenes.length} scene${story.scenes.length === 1 ? "" : "s"}` : "No story selected";
  const list = $("#story-scenes"); list.replaceChildren();
  for (const [index, scene] of (story?.scenes || []).entries()) { const item = document.createElement("li"); const button = document.createElement("button"); button.type = "button"; button.className = "variant"; button.setAttribute("aria-pressed", String(scene.scene_id === activeSceneId)); button.textContent = `${index + 1}. ${scene.prompt || "Untitled scene"}${scene.approved ? " · approved" : " · draft"}`; button.addEventListener("click", () => selectStoryScene(scene)); item.append(button); list.append(item); }
  $("#add-story-scene").disabled = !story;
  $("#export-story-project").disabled = !story;
  $("#export-story-package").disabled = !story;
  $("#draft-story-scenes").disabled = true;
  $("#story-draft-note").textContent = STORY_PLANNER_AVAILABLE
    ? "Drafting is optional and never changes the project until you add or edit a scene."
    : "Local scene drafting is unavailable while its structured-output quality gate is unresolved. You can add scenes manually.";
  $("#save-story-scene").disabled = !story || !activeSceneId; $("#move-scene-earlier").disabled = !story || !activeSceneId; $("#move-scene-later").disabled = !story || !activeSceneId;
  $("#replace-story-still").disabled = !story || !activeSceneId;
  $("#replace-story-clip").disabled = !story || !activeSceneId || !story.scenes.find((scene) => scene.scene_id === activeSceneId)?.artifacts?.clip;
  $("#replace-story-subtitles").disabled = !story || !activeSceneId;
  $("#replace-story-narration").disabled = !story || !activeSceneId || !story.scenes.find((scene) => scene.scene_id === activeSceneId)?.artifacts?.clip;
  $("#render-storyboard").disabled = !story || !story.scenes.some((scene) => scene.approved); $("#render-story-clips").disabled = !story || !story.scenes.some((scene) => scene.approved);
  $("#render-story-narration").disabled = !story || !story.scenes.some((scene) => scene.approved && scene.narration);
  $("#render-story-subtitles").disabled = !story || !story.scenes.some((scene) => scene.approved && scene.narration);
  $("#compose-story").disabled = !story || !story.scenes.length || !story.scenes.every((scene) => scene.approved && (scene.artifacts?.narration || scene.artifacts?.clip));
}
function selectStoryScene(scene) { const shot = scene.shot || {}; activeSceneId = scene.scene_id; $("#scene-prompt").value = scene.prompt || ""; $("#scene-narration").value = scene.narration || ""; $("#scene-approved").checked = Boolean(scene.approved); $("#scene-trim-start").value = String(shot.trim_start_seconds || 0); $("#scene-trim-end").value = String(shot.trim_end_seconds || 0); $("#scene-narration-muted").checked = Boolean(shot.narration_muted); renderStory(activeStory); }
function renderDraftProposals() { const list = $("#story-draft-list"); list.replaceChildren(); for (const [index, proposal] of draftProposals.entries()) { const item = document.createElement("li"); const button = document.createElement("button"); button.type = "button"; button.className = "variant"; button.textContent = `Use draft ${index + 1}: ${proposal.prompt}`; button.addEventListener("click", () => { activeSceneId = null; $("#scene-prompt").value = proposal.prompt; $("#scene-narration").value = proposal.narration; $("#scene-approved").checked = false; $("#story-draft-note").textContent = "Draft copied into the editable scene fields. Add it to save."; }); item.append(button); list.append(item); } }
async function showStory() {
  const dialog = $("#story-dialog"); const select = $("#story-list"); select.replaceChildren(new Option("Create a new story", ""));
  try { const { stories = [] } = await invoke("story_list"); for (const story of stories) select.add(new Option(story.title, story.story_id)); renderStory(null); dialog.showModal(); }
  catch (reason) { setError(String(reason)); }
}

restoreDraft();
if (!localStorage.getItem(ONBOARDING_KEY)) $("#onboarding").showModal();
$("#complete-onboarding").addEventListener("click", () => localStorage.setItem(ONBOARDING_KEY, "complete"));
$("#prompt").addEventListener("input", saveHistory); $("#seed").addEventListener("change", saveHistory);
$("#random-seed").addEventListener("click", () => { $("#seed").value = String(Math.floor(Math.random() * 2_147_483_647)); saveHistory(); });
for (const button of document.querySelectorAll("[data-recipe]")) button.addEventListener("click", () => { setRecipe(button.dataset.recipe); saveHistory(); });
for (const button of document.querySelectorAll("[data-mode]")) button.addEventListener("click", () => {
  if (isImageModel()) return;
  state.mode = button.dataset.mode;
  for (const item of document.querySelectorAll("[data-mode]")) { const selected = item === button; item.setAttribute("aria-checked", String(selected)); item.classList.toggle("selected", selected); }
  $("#choose-image").hidden = state.mode !== "image"; $("#source-image-status").hidden = state.mode !== "image";
});
$("#choose-image").addEventListener("click", async () => {
  try { const selected = await invoke("choose_source_image"); state.sourceImageId = selected.sourceImageId; $("#source-image-status").textContent = state.sourceImageId ? "Source image selected and copied into SynVid storage." : "No source image selected."; }
  catch (reason) { setError(String(reason)); }
});
$("#model").addEventListener("change", () => { state.modelId = $("#model").value; setRecipe("Balanced"); updateControls(); });
for (const button of document.querySelectorAll("[data-export]")) button.addEventListener("click", async () => {
  if (!state.selectedVariant) return;
  button.disabled = true;
  try {
    const result = await invoke("export_video", { outputId: state.selectedVariant, profile: button.dataset.export });
    $("#result-message").textContent = result.saved
      ? `${result.profile} export saved as ${result.fileName} without regenerating the canonical video.`
      : "Export cancelled; the canonical video remains unchanged.";
  }
  catch (reason) { setError(String(reason)); }
  finally { button.disabled = false; }
});
$("#edit-video").addEventListener("click", () => { $("#video-edit-controls").hidden = !$("#video-edit-controls").hidden; });
$("#add-voice").addEventListener("click", () => { $("#voice-controls").hidden = !$("#voice-controls").hidden; });
$("#change-amount").addEventListener("input", () => { $("#change-amount-value").textContent = $("#change-amount").value; });
$("#apply-video-edit").addEventListener("click", async () => {
  const prompt = $("#edit-prompt").value.trim(); const changeAmount = Number($("#change-amount").value);
  if (!state.selectedVariant || !prompt) return setError("Describe the video change before applying the edit.");
  if (state.modelId !== "ltx-video") return setError("Video editing is currently validated only for LTX Video.");
  try {
    const accepted = await invoke("edit_video", { request: { modelId: state.modelId, sourceOutputId: state.selectedVariant, prompt, seed: Number($("#seed").value), recipe: state.recipe, changeAmount } });
    state.activeJob = { job_id: accepted.job_id || accepted.jobId, status_text: "Loading edit model", progress: 0 };
    jobStatus.textContent = "Applying video edit…"; updateControls();
  } catch (reason) { setError(String(reason)); }
});
$("#apply-image-edit").addEventListener("click", async () => {
  const prompt = $("#image-edit-prompt").value.trim(); const modelId = $("#image-edit-model").value;
  const model = state.models?.[modelId];
  if (!state.selectedVariant || !prompt) return setError("Describe the image change before applying the edit.");
  if (!model?.capabilities?.includes("image_editing") || !model?.measured_image_profile) return setError("Qwen Image Edit has not passed its measured local MPS gate.");
  try {
    const accepted = await invoke("edit_image", { request: { modelId, sourceOutputId: state.selectedVariant, prompt, seed: Number($("#seed").value) } });
    state.activeJob = { job_id: accepted.job_id || accepted.jobId, status_text: "Loading image edit model", progress: 0 };
    jobStatus.textContent = "Applying image edit…"; updateControls();
  } catch (reason) { setError(String(reason)); }
});
$("#generate-voice").addEventListener("click", async () => {
  const text = $("#narration-text").value.trim();
  if (!state.selectedVariant || !text) return setError("Add narration text before generating the voice.");
  try {
    const accepted = await invoke("narrate", { request: { sourceOutputId: state.selectedVariant, text } });
    state.activeJob = { job_id: accepted.job_id || accepted.jobId, status_text: "Loading narration model", progress: 0 };
    jobStatus.textContent = "Generating narration…"; updateControls();
  } catch (reason) { setError(String(reason)); }
});
$("#reset-preset").addEventListener("click", () => { setRecipe("Balanced"); saveHistory(); });
$("#undo").addEventListener("click", () => { if (state.historyIndex > 0) { state.historyIndex--; applyDraft(state.history[state.historyIndex]); renderHistory(); saveDraft(); } });
$("#redo").addEventListener("click", () => { if (state.historyIndex < state.history.length - 1) { state.historyIndex++; applyDraft(state.history[state.historyIndex]); renderHistory(); saveDraft(); } });
$("#library-button").addEventListener("click", showLibrary); $("#settings-button").addEventListener("click", showSettings); $("#about-button").addEventListener("click", showAbout); $("#recovery-button").addEventListener("click", showRecovery);
$("#story-button").addEventListener("click", showStory);
$("#story-list").addEventListener("change", async () => { const id = $("#story-list").value; if (!id) return renderStory(null); try { renderStory(await invoke("story_get", { storyId: id })); } catch (reason) { setError(String(reason)); } });
$("#save-story").addEventListener("click", async () => {
  const request = { title: $("#story-name").value.trim(), premise: $("#story-premise").value.trim(), styleBible: $("#story-style").value.trim(), aspectRatio: $("#story-aspect").value };
  if (!request.title) return setError("Add a story title before saving.");
  try { const saved = activeStory ? await invoke("story_update", { request: { ...request, storyId: activeStory.story_id, expectedRevision: activeStory.revision } }) : await invoke("story_create", { request }); renderStory(saved); $("#story-list").value = saved.story_id; } catch (reason) { setError(String(reason)); }
});
async function exportStoryProject(selfContained) { if (!activeStory) return; try { const result = await invoke("story_export_project", { request: { storyId: activeStory.story_id, selfContained } }); if (result.saved) $("#story-draft-note").textContent = selfContained ? "Self-contained story package saved." : "Story project package saved."; } catch (reason) { setError(String(reason)); } }
$("#export-story-project").addEventListener("click", () => void exportStoryProject(false));
$("#export-story-package").addEventListener("click", () => void exportStoryProject(true));
$("#import-story-project").addEventListener("click", async () => { try { const picked = await invoke("choose_story_project"); if (!picked.sourceProjectId) return; const story = await invoke("story_import_project", { sourceProjectId: picked.sourceProjectId }); const select = $("#story-list"); select.add(new Option(story.title, story.story_id)); select.value = story.story_id; renderStory(story); } catch (reason) { setError(String(reason)); } });
$("#add-story-scene").addEventListener("click", async () => { if (!activeStory) return; try { const saved = await invoke("story_add_scene", { request: { storyId: activeStory.story_id, expectedRevision: activeStory.revision, prompt: $("#scene-prompt").value.trim(), narration: $("#scene-narration").value.trim() } }); $("#scene-prompt").value = ""; $("#scene-narration").value = ""; renderStory(saved); } catch (reason) { setError(String(reason)); } });
$("#draft-story-scenes").addEventListener("click", async () => { if (!activeStory) return; const button = $("#draft-story-scenes"); button.disabled = true; $("#story-draft-note").textContent = "Drafting scenes locally…"; try { const accepted = await invoke("story_draft_scenes", { request: { storyId: activeStory.story_id, expectedRevision: activeStory.revision, count: 3 } }); state.activeJob = { job_id: accepted.job_id || accepted.jobId, status_text: "Drafting scenes locally", progress: 0 }; } catch (reason) { $("#story-draft-note").textContent = String(reason); button.disabled = false; } });
$("#save-story-scene").addEventListener("click", async () => { if (!activeStory || !activeSceneId) return; const trimStart = Number($("#scene-trim-start").value); const trimEnd = Number($("#scene-trim-end").value); if (!Number.isFinite(trimStart) || !Number.isFinite(trimEnd) || trimStart < 0 || trimEnd < 0 || (trimEnd && trimEnd <= trimStart)) return setError("Shot trim times must be non-negative, with the end after the start."); try { const saved = await invoke("story_update_scene", { request: { storyId: activeStory.story_id, expectedRevision: activeStory.revision, sceneId: activeSceneId, prompt: $("#scene-prompt").value.trim(), narration: $("#scene-narration").value.trim(), approved: $("#scene-approved").checked, trimStartSeconds: trimStart, trimEndSeconds: trimEnd, narrationMuted: $("#scene-narration-muted").checked } }); renderStory(saved); } catch (reason) { setError(String(reason)); } });
async function moveStoryScene(direction) { if (!activeStory || !activeSceneId) return; const order = activeStory.scenes.map((scene) => scene.scene_id); const index = order.indexOf(activeSceneId); const target = index + direction; if (target < 0 || target >= order.length) return; [order[index], order[target]] = [order[target], order[index]]; try { renderStory(await invoke("story_reorder_scenes", { request: { storyId: activeStory.story_id, expectedRevision: activeStory.revision, sceneIds: order } })); } catch (reason) { setError(String(reason)); } }
$("#move-scene-earlier").addEventListener("click", () => void moveStoryScene(-1)); $("#move-scene-later").addEventListener("click", () => void moveStoryScene(1));
async function renderStoryPhase(through) { if (!activeStory) return; try { const accepted = await invoke("render_story", { request: { storyId: activeStory.story_id, expectedRevision: activeStory.revision, through } }); state.activeJob = accepted.job_id; jobStatus.textContent = through === "still" ? "Rendering storyboard…" : "Rendering story motion clips…"; } catch (reason) { setError(String(reason)); } }
$("#render-storyboard").addEventListener("click", () => void renderStoryPhase("still")); $("#render-story-clips").addEventListener("click", () => void renderStoryPhase("clip"));
$("#render-story-narration").addEventListener("click", () => void renderStoryPhase("narration"));
$("#render-story-subtitles").addEventListener("click", () => void renderStoryPhase("subtitles"));
$("#replace-story-still").addEventListener("click", async () => { if (!activeStory || !activeSceneId) return; try { const picked = await invoke("choose_source_image"); if (!picked.sourceImageId) return; renderStory(await invoke("story_import_still", { request: { storyId: activeStory.story_id, expectedRevision: activeStory.revision, sceneId: activeSceneId, sourceImageId: picked.sourceImageId } })); } catch (reason) { setError(String(reason)); } });
$("#replace-story-clip").addEventListener("click", async () => { if (!activeStory || !activeSceneId) return; try { const picked = await invoke("choose_story_clip"); if (!picked.sourceClipId) return; renderStory(await invoke("story_import_clip", { request: { storyId: activeStory.story_id, expectedRevision: activeStory.revision, sceneId: activeSceneId, sourceClipId: picked.sourceClipId } })); } catch (reason) { setError(String(reason)); } });
$("#replace-story-subtitles").addEventListener("click", async () => { if (!activeStory || !activeSceneId) return; try { const picked = await invoke("choose_story_subtitles"); if (!picked.sourceSubtitleId) return; renderStory(await invoke("story_import_subtitles", { request: { storyId: activeStory.story_id, expectedRevision: activeStory.revision, sceneId: activeSceneId, sourceSubtitleId: picked.sourceSubtitleId } })); } catch (reason) { setError(String(reason)); } });
$("#replace-story-narration").addEventListener("click", async () => { if (!activeStory || !activeSceneId) return; try { const picked = await invoke("choose_story_narration"); if (!picked.sourceAudioId) return; renderStory(await invoke("story_import_narration", { request: { storyId: activeStory.story_id, expectedRevision: activeStory.revision, sceneId: activeSceneId, sourceAudioId: picked.sourceAudioId } })); } catch (reason) { setError(String(reason)); } });
$("#compose-story").addEventListener("click", async () => { if (!activeStory) return; try { const accepted = await invoke("compose_story", { request: { storyId: activeStory.story_id, expectedRevision: activeStory.revision } }); state.activeJob = accepted.job_id; jobStatus.textContent = "Composing approved scenes…"; } catch (reason) { setError(String(reason)); } });
for (const button of document.querySelectorAll(".close-dialog")) button.addEventListener("click", () => button.closest("dialog").close());
$("#run-recovery").addEventListener("click", async () => { const button = $("#run-recovery"); button.disabled = true; try { const recovered = await invoke("recover"); $("#recovery-preview").textContent = `Recovered ${recovered.partialOutputCount ?? recovered.partial_output_count ?? 0} incomplete output(s). Completed media was not changed.`; } catch (reason) { $("#recovery-preview").textContent = `Recovery could not run: ${String(reason)}`; } finally { button.disabled = false; } });
$("#clean-temporary").addEventListener("click", async () => {
  if (!window.confirm("Clean SynVid temporary files? Completed outputs, stories, and models will not be deleted.")) return;
  const button = $("#clean-temporary"); button.disabled = true;
  try { const result = await invoke("clean_temporary"); $("#cleanup-status").textContent = `Temporary files cleaned; freed ${formatBytes(result.freed_bytes)}.`; }
  catch (reason) { $("#cleanup-status").textContent = `Cleanup could not run: ${String(reason)}`; }
  finally { button.disabled = false; }
});
generateButton.addEventListener("click", async () => {
  const prompt = $("#prompt").value.trim(); const seed = Number($("#seed").value);
  if (!prompt) return setError(`Add an ${isImageModel() ? "image" : "video"} description before generating.`);
  if (!Number.isInteger(seed) || seed < 0 || seed > 2_147_483_647) return setError("Seed must be a whole number from 0 to 2147483647.");
  if (!activeProfile()) return setError("The selected model recipe is not measured on this Mac.");
  if (state.mode === "image" && !state.sourceImageId) {
    setError("Choose a source image before starting image-to-video.");
    return;
  }
  setError(); generateButton.disabled = true; jobStatus.textContent = "Submitting generation…";
  try { const accepted = await invoke("generate", { request: { modelId: state.modelId, prompt, seed, recipe: state.recipe, sourceImageId: state.mode === "image" && !isImageModel() ? state.sourceImageId : null } }); state.activeJob = { job_id: accepted.job_id || accepted.jobId, status_text: "Loading model", progress: 0 }; jobStatus.textContent = "Loading model…"; }
  catch (reason) { setError(String(reason)); jobStatus.textContent = "Generation was not started."; }
  updateControls();
});
cancelButton.addEventListener("click", async () => { if (!state.activeJob) return; cancelButton.disabled = true; jobStatus.textContent = "Cancelling generation…"; try { await invoke("cancel", { jobId: state.activeJob.job_id || state.activeJob.jobId }); } catch (reason) { setError(String(reason)); } finally { cancelButton.disabled = false; } });
void refresh(); window.setInterval(() => void refresh(), 750);
