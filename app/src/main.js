// This frontend is deliberately vanilla static assets, not an npm bundle.
// Tauri exposes this narrow API only because `withGlobalTauri` is enabled in
// the checked-in desktop configuration.
const invoke = window.__TAURI__.core.invoke;
const convertFileSrc = window.__TAURI__.core.convertFileSrc;

const DRAFT_KEY = "synvid.stage2.draft.v1";
const ONBOARDING_KEY = "synvid.stage2.onboarding.v1";
const MAX_HISTORY = 20;
const DEFAULT_MODEL_ID = "wan2.2-ti2v-5b-mlx";
const WAN_MODEL_ID = "wan2.2-ti2v-5b-mlx";
const SETTINGS_HIDDEN_MODEL_IDS = new Set(["flux-dev", "flux-kontext-dev", "hunyuan15-480p-t2v", "hunyuan15-480p-i2v"]);
const WALKTHROUGH_STEPS = [
  { target: "#preparation-tab", title: "1. Prepare local models", copy: "Start here to see the models available for local use. Download a model only after reviewing its size, license, and fixed revision, then generate a measured profile before composing." },
  { target: "#prompt", title: "2. Describe the result", copy: "Write the subject, movement, setting, and visual style. This text is kept on this Mac and becomes the instruction for your local model." },
  { target: "[data-mode=\"text\"]", title: "3. Choose how to begin", copy: "Use Text to video to create from your description. Choose Image to video when you want to animate a source image; the Choose source image button then opens the native file picker." },
  { target: "#model", title: "4. Pick a model", copy: "Choose the prepared model for the result you need. LTX Video makes video; FLUX.1-schnell makes a still image. Downloads and profile generation stay in Preparation." },
  { target: "#model-settings", title: "5. Review model settings", copy: "Each model shows only settings that have been measured for it. Wan 2.2 uses one fixed validated shape, while LTX exposes its measured recipe, aspect, and duration choices." },
  { target: "#seed", title: "6. Control variations", copy: "Keep a seed to make a comparable variation, or use Randomize for a new one. Undo and Redo restore recent prompt, seed, and recipe changes." },
  { target: "#generate", title: "7. Generate locally", copy: "Generate starts the selected measured model. Progress and Cancel stay here while it runs. Model download and profile generation happen separately in Preparation." },
  { target: "#variant-list", title: "8. Review and refine", copy: "Each finished output appears in Variants. Select one to preview it, export it, or use Edit video, Add Voice, or image editing when those tools are available." },
  { target: "#library-button", title: "9. Keep or clean up", copy: "Library reopens or deletes completed local work. Preparation manages downloaded models and profiles. Recovery Center only removes incomplete temporary work, never finished outputs." },
];
// The evaluated compact planner failed the Stage 7 adversarial JSON gate.
// Do not expose an optional model feature merely because its files are present.
const STORY_PLANNER_AVAILABLE = false;
const state = { recipes: null, models: null, modelId: DEFAULT_MODEL_ID, activeJob: null, lastJobMessage: "", downloadModelJobId: null, calibrationJobId: null, connected: false, quality: "Balanced", aspect: "Landscape", frames: 41, recipe: "Balanced", mode: "text", sourceImageId: null, variants: [], selectedVariant: null, history: [], historyIndex: -1 };
let activeStory = null; let activeSceneId = null; let draftProposals = [];
let walkthroughIndex = 0;
let requiredModelSetupChecked = false;
let requiredModel = null;
const $ = (selector) => document.querySelector(selector);
const connection = $("#connection");
const jobStatus = $("#job-status");
const generateButton = $("#generate");
const cancelButton = $("#cancel");
const preparationCancelButton = $("#preparation-cancel");
const error = $("#form-error");
const generationProgress = $("#generation-progress");
const modelDownloadProgress = $("#model-download-progress");
const modelDownloadStatus = $("#model-download-status");
const modelCalibrationProgress = $("#model-calibration-progress");
const modelCalibrationStatus = $("#model-calibration-status");

function setError(message = "") { error.textContent = message; error.hidden = !message; }
function appConfirm(message) {
  // WKWebView on macOS has no UIDelegate wired for window.confirm(), so it
  // resolves falsy without ever showing anything; this dialog replaces it.
  const dialog = $("#confirm-dialog");
  $("#confirm-message").textContent = message;
  return new Promise((resolve) => {
    const finish = (confirmed) => {
      $("#confirm-ok").removeEventListener("click", onConfirm);
      $("#confirm-cancel").removeEventListener("click", onCancel);
      dialog.removeEventListener("cancel", onDialogCancel);
      dialog.close();
      resolve(confirmed);
    };
    const onConfirm = () => finish(true);
    const onCancel = () => finish(false);
    const onDialogCancel = (event) => { event.preventDefault(); finish(false); };
    $("#confirm-ok").addEventListener("click", onConfirm);
    $("#confirm-cancel").addEventListener("click", onCancel);
    dialog.addEventListener("cancel", onDialogCancel);
    dialog.showModal();
  });
}
function setWorkspaceTab(name) {
  for (const button of document.querySelectorAll("[data-workspace-tab]")) {
    const selected = button.dataset.workspaceTab === name;
    button.setAttribute("aria-selected", String(selected));
    $("#" + button.getAttribute("aria-controls")).hidden = !selected;
  }
  if (name === "preparation") void loadPreparationCatalog();
}
function stopWalkthrough() {
  document.querySelectorAll(".walkthrough-target").forEach((element) => element.classList.remove("walkthrough-target"));
  $("#walkthrough").hidden = true;
}
function positionWalkthrough(target) {
  const walkthrough = $("#walkthrough");
  if (walkthrough.hidden || !target) return;
  const gap = 16;
  const targetBounds = target.getBoundingClientRect();
  const popupBounds = walkthrough.getBoundingClientRect();
  const canPlaceRight = window.innerWidth - targetBounds.right >= popupBounds.width + gap;
  const canPlaceLeft = targetBounds.left >= popupBounds.width + gap;
  let left = canPlaceRight ? targetBounds.right + gap : canPlaceLeft ? targetBounds.left - popupBounds.width - gap : Math.max(gap, Math.min(targetBounds.left, window.innerWidth - popupBounds.width - gap));
  let top = targetBounds.top + (targetBounds.height - popupBounds.height) / 2;
  if (!canPlaceRight && !canPlaceLeft) top = targetBounds.bottom + gap;
  top = Math.max(gap, Math.min(top, window.innerHeight - popupBounds.height - gap));
  left = Math.max(gap, Math.min(left, window.innerWidth - popupBounds.width - gap));
  walkthrough.style.left = `${Math.round(left)}px`;
  walkthrough.style.top = `${Math.round(top)}px`;
}
function repositionWalkthrough() {
  if (!$("#walkthrough").hidden) positionWalkthrough(document.querySelector(WALKTHROUGH_STEPS[walkthroughIndex].target));
}
function renderWalkthrough() {
  const step = WALKTHROUGH_STEPS[walkthroughIndex];
  const target = document.querySelector(step.target);
  document.querySelectorAll(".walkthrough-target").forEach((element) => element.classList.remove("walkthrough-target"));
  if (target) {
    const panel = target.closest(".workspace-panel");
    if (panel) setWorkspaceTab(panel.id.replace("-panel", ""));
    target.classList.add("walkthrough-target");
    target.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
  }
  $("#walkthrough").hidden = false;
  $("#walkthrough-progress").textContent = "WALKTHROUGH · " + (walkthroughIndex + 1) + " OF " + WALKTHROUGH_STEPS.length;
  $("#walkthrough-title").textContent = step.title;
  $("#walkthrough-copy").textContent = step.copy;
  $("#walkthrough-back").disabled = walkthroughIndex === 0;
  $("#walkthrough-next").textContent = walkthroughIndex === WALKTHROUGH_STEPS.length - 1 ? "Finish" : "Next";
  requestAnimationFrame(() => requestAnimationFrame(() => positionWalkthrough(target)));
}
function startWalkthrough() { walkthroughIndex = 0; renderWalkthrough(); }
function renderGenerationProgress(job) {
  generationProgress.hidden = !job;
  generationProgress.value = job ? Math.round(Math.max(0, Math.min(1, Number(job.progress) || 0)) * 100) : 0;
}
function renderModelDownloadProgress(job) {
  const downloading = job?.operation === "model_download" || (job && state.downloadModelJobId === (job.job_id || job.jobId));
  modelDownloadStatus.hidden = !downloading;
  if (!downloading) {
    modelDownloadProgress.value = 0;
    return;
  }
  const percent = Math.round(Math.max(0, Math.min(1, Number(job.progress) || 0)) * 100);
  modelDownloadProgress.value = percent;
  modelDownloadProgress.setAttribute("aria-valuetext", `${percent}%`);
  $("#model-download-status-text").textContent = `${job.status_text || job.statusText || "Downloading model"} · ${percent}%`;
  $("#preparation-status").textContent = `${job.status_text || job.statusText || "Downloading model"} · ${percent}%`;
}
function renderCalibrationProgress(job) {
  const calibrating = job?.operation === "calibrate" || (job && state.calibrationJobId === (job.job_id || job.jobId));
  modelCalibrationStatus.hidden = !calibrating;
  if (!calibrating) {
    modelCalibrationProgress.value = 0;
    return;
  }
  const percent = Math.round(Math.max(0, Math.min(1, Number(job.progress) || 0)) * 100);
  modelCalibrationProgress.value = percent;
  modelCalibrationProgress.setAttribute("aria-valuetext", `${percent}%`);
  $("#model-calibration-status-text").textContent = `${job.status_text || job.statusText || "Calibrating model"} · ${percent}%`;
  $("#preparation-status").textContent = `${job.status_text || job.statusText || "Generating profile"} · ${percent}%`;
}
function selectedModel() { return state.models?.[state.modelId] || null; }
function isImageModel() { return selectedModel()?.capabilities?.includes("image_generation") || false; }
function isWanModel() { return state.modelId === WAN_MODEL_ID; }
function profileLabel(profile) { return `${profile.width} × ${profile.height} · ${profile.frames ? `${profile.frames} frames · ` : ""}${profile.steps} steps`; }
const QUALITY_TIERS = ["Draft", "Balanced", "High"];
const ASPECT_RATIOS = ["Square", "Landscape", "Portrait"];
// Square recipes form a duration ladder per quality tier ("Balanced",
// "BalancedD17", "BalancedD25", …); Landscape/Portrait stay single-duration.
function aspectForReference(reference) {
  if (!reference?.width || !reference?.height) return null;
  const ratio = reference.width / reference.height;
  return ratio > 1.2 ? "Landscape" : ratio < 0.833 ? "Portrait" : "Square";
}
function recipeAspect(name, quality, info) {
  const suffix = name.slice(quality.length);
  if (suffix === "Landscape") return "Landscape";
  if (suffix === "Portrait") return "Portrait";
  if (suffix === "" || /^D\d+$/.test(suffix)) return aspectForReference(info.reference);
  return null;
}
function durationOptionsFor(model, quality, aspect = state.aspect) {
  if (!model?.calibration) return [];
  const pattern = new RegExp(`^${quality}(D(\\d+)|Landscape|Portrait)?$`);
  return Object.entries(model.calibration)
    .filter(([name, info]) => pattern.test(name) && recipeAspect(name, quality, info) === aspect)
    .map(([name, info]) => ({ name, frames: info.reference?.frames || 0, fps: info.reference?.fps || 8, measured: Boolean(info.measured) }))
    .filter((option) => option.frames > 0)
    .sort((a, b) => a.frames - b.frames);
}
function availableAspectsFor(model, quality = state.quality) {
  if (!model?.calibration) return ASPECT_RATIOS;
  return ASPECT_RATIOS.filter((aspect) => durationOptionsFor(model, quality, aspect).length > 0);
}
function availableQualitiesFor(model, aspect = state.aspect) {
  if (!model?.calibration) return QUALITY_TIERS;
  return QUALITY_TIERS.filter((quality) => durationOptionsFor(model, quality, aspect).length > 0);
}
function composeRecipeName(quality, aspect, frames) {
  const options = durationOptionsFor(selectedModel(), quality, aspect);
  if (!options.length) return quality;
  const match = options.find((option) => option.frames === frames) || options.find((option) => option.name === quality) || options[0];
  return match.name;
}
function normalizeSelection() {
  const model = selectedModel();
  let aspects = availableAspectsFor(model, state.quality);
  if (model?.calibration && !aspects.length) {
    aspects = ASPECT_RATIOS.filter((aspect) => QUALITY_TIERS.some((quality) => durationOptionsFor(model, quality, aspect).length > 0));
  }
  if (aspects.length && !aspects.includes(state.aspect)) state.aspect = aspects[0];
  let qualities = availableQualitiesFor(model, state.aspect);
  if (model?.calibration && !qualities.length) {
    qualities = QUALITY_TIERS.filter((quality) => ASPECT_RATIOS.some((aspect) => durationOptionsFor(model, quality, aspect).length > 0));
  }
  if (qualities.length && !qualities.includes(state.quality)) state.quality = qualities.includes("Balanced") ? "Balanced" : qualities[0];
  const options = durationOptionsFor(model, state.quality, state.aspect);
  if (options.length && !options.some((option) => option.frames === state.frames)) {
    state.frames = (options.find((option) => option.name === state.quality) || options[0]).frames;
  }
  state.recipe = composeRecipeName(state.quality, state.aspect, state.frames);
}
function draft() { return { prompt: $("#prompt").value, seed: $("#seed").value, recipe: state.quality, aspect: state.aspect, frames: state.frames }; }
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
  state.aspect = ASPECT_RATIOS.includes(value.aspect) ? value.aspect : "Square";
  state.frames = Number.isInteger(value.frames) && value.frames > 0 ? value.frames : 49;
  for (const button of document.querySelectorAll("[data-aspect]")) button.setAttribute("aria-checked", String(button.dataset.aspect === state.aspect));
  setRecipe(QUALITY_TIERS.includes(value.recipe) ? value.recipe : "Balanced");
}
function restoreDraft() {
  try { const value = localStorage.getItem(DRAFT_KEY); if (value) applyDraft(value); } catch { localStorage.removeItem(DRAFT_KEY); }
  state.history = [JSON.stringify(draft())]; state.historyIndex = 0; renderHistory();
}
function setRecipe(quality) {
  state.quality = quality;
  normalizeSelection();
  state.recipe = composeRecipeName(state.quality, state.aspect, state.frames);
  for (const button of document.querySelectorAll("[data-recipe]")) button.setAttribute("aria-checked", String(button.dataset.recipe === quality));
  renderRecipeButtons();
  renderRecipeNote();
  renderDurationControl();
}
function setAspect(aspect) {
  state.aspect = aspect;
  normalizeSelection();
  state.recipe = composeRecipeName(state.quality, state.aspect, state.frames);
  for (const button of document.querySelectorAll("[data-aspect]")) button.setAttribute("aria-checked", String(button.dataset.aspect === aspect));
  renderAspectButtons();
  renderRecipeNote();
  renderDurationControl();
}
function setDuration(frames) {
  if (!durationOptionsFor(selectedModel(), state.quality, state.aspect).some((option) => option.frames === frames)) return;
  state.frames = frames;
  state.recipe = composeRecipeName(state.quality, state.aspect, state.frames);
  renderRecipeNote();
  renderDurationControl();
}
function renderRecipeButtons() {
  const model = selectedModel();
  const available = availableQualitiesFor(model, state.aspect);
  for (const button of document.querySelectorAll("[data-recipe]")) {
    const enabled = available.includes(button.dataset.recipe);
    button.disabled = !enabled;
    button.setAttribute("aria-disabled", String(!enabled));
    button.setAttribute("aria-checked", String(button.dataset.recipe === state.quality));
  }
}
function renderAspectButtons() {
  const available = availableAspectsFor(selectedModel(), state.quality);
  for (const button of document.querySelectorAll("[data-aspect]")) {
    const enabled = available.includes(button.dataset.aspect);
    button.disabled = !enabled;
    button.setAttribute("aria-disabled", String(!enabled));
    button.setAttribute("aria-checked", String(button.dataset.aspect === state.aspect));
  }
}
function renderRecipeNote() {
  const profile = activeProfile();
  $("#recipe-note").textContent = profile
    ? `${state.recipe}: ${profile.steps} steps at ${profile.width} × ${profile.height}; measured on this Mac.`
    : `${state.recipe} has not been measured on this Mac.`;
  $("#advanced-note").textContent = "Custom overrides are unavailable: only the measured recipe map may be submitted.";
}
function renderDurationControl() {
  const field = $("#duration-field"); const slider = $("#duration-slider"); const valueOut = $("#duration-value"); const note = $("#duration-note");
  if (isImageModel()) { field.hidden = true; return; }
  field.hidden = false;
  const options = durationOptionsFor(selectedModel(), state.quality, state.aspect);
  if (!options.length) {
    slider.disabled = true; slider.min = "0"; slider.max = "0"; slider.value = "0";
    valueOut.textContent = "—";
    note.textContent = "Duration options appear once the model catalog loads.";
    return;
  }
  if (options.length === 1) {
    const option = options[0];
    slider.disabled = true; slider.min = "0"; slider.max = "0"; slider.value = "0";
    valueOut.textContent = `${(option.frames / option.fps).toFixed(1)}s`;
    note.textContent = option.measured
      ? `${option.frames} frames at ${option.fps} fps, measured on this Mac; this model has one validated duration for ${state.aspect}.`
      : `${option.frames} frames at ${option.fps} fps — not yet measured on this Mac. Generate a profile before generating.`;
    return;
  }
  let index = options.findIndex((option) => option.frames === state.frames);
  if (index === -1) index = options.findIndex((option) => option.name === state.quality);
  if (index === -1) index = 0;
  slider.disabled = false; slider.min = "0"; slider.max = String(options.length - 1); slider.step = "1"; slider.value = String(index);
  const option = options[index];
  valueOut.textContent = `${(option.frames / option.fps).toFixed(1)}s`;
  note.textContent = option.measured
    ? `${option.frames} frames at ${option.fps} fps, measured on this Mac.`
    : `${option.frames} frames at ${option.fps} fps — not yet measured on this Mac. Generate a profile before generating.`;
}
function renderModelSettings() {
  const wan = isWanModel();
  $("#generic-generation-controls").hidden = wan;
  $("#wan-settings").hidden = !wan;
}
function activeProfile() { const model = selectedModel(); return isImageModel() ? model?.measured_image_profile : model?.measured_recipes?.[state.recipe] || null; }
function hasUncalibratedRecipe(model) { return Object.values(model?.calibration || {}).some((info) => !info.measured); }
function updateControls() {
  normalizeSelection();
  const model = selectedModel(); const profile = activeProfile();
  const installed = Boolean(model?.installed);
  const available = state.connected && profile && installed && !state.activeJob;
  const preparationJob = state.activeJob?.operation === "model_download" || state.activeJob?.operation === "calibrate";
  generateButton.disabled = !available; cancelButton.hidden = !state.activeJob || preparationJob;
  preparationCancelButton.hidden = !preparationJob;
  cancelButton.textContent = state.activeJob?.operation === "calibrate" ? "Cancel profile generation" : "Cancel generation";
  if (!state.activeJob) {
    if (state.lastJobMessage) jobStatus.textContent = state.lastJobMessage;
    else if (!state.connected) jobStatus.textContent = "The local worker is unavailable. Reopen SynVid, then try again.";
    else if (!installed) jobStatus.textContent = "Install the selected local model before generating.";
    else if (!profile) jobStatus.textContent = hasUncalibratedRecipe(model)
      ? "This model has no measured profile for the selected recipe. Generate one on this Mac before generating."
      : (selectedModel()?.reason || "Set up a measured local model before generating. SynVid will show its size, license, and revision first.");
    else jobStatus.textContent = "Ready to generate locally.";
  }
  renderGenerationProgress(state.activeJob);
  renderCalibrationProgress(state.activeJob);
  renderModelSettings();
  renderRecipeButtons();
  renderAspectButtons();
  $("#profile").textContent = profile ? profileLabel(profile) : "Not available";
  $("#fps").textContent = profile && !isImageModel() ? `${profile.fps} FPS (Native)` : "—";
  generateButton.textContent = isImageModel() ? "Generate image" : "Generate video";
  const modes = selectedModel()?.modes || ["text", "image"];
  const textMode = document.querySelector('[data-mode="text"]');
  const imageMode = document.querySelector('[data-mode="image"]');
  textMode.disabled = !modes.includes("text") || isImageModel();
  imageMode.disabled = !modes.includes("image") || isImageModel();
  if (!modes.includes(state.mode) || isImageModel()) {
    state.mode = isImageModel() || modes.includes("text") ? "text" : "image";
    state.sourceImageId = state.mode === "image" ? state.sourceImageId : null;
    for (const item of document.querySelectorAll("[data-mode]")) {
      const selected = item.dataset.mode === state.mode;
      item.setAttribute("aria-checked", String(selected));
      item.classList.toggle("selected", selected);
    }
  }
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
  setWorkspaceTab("review");
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
    const failure = payload.error || (payload.state === "failed" ? "The operation failed." : "");
    const preparationOperation = payload.operation === "model_download" || payload.operation === "calibrate";
    if (payload.state && !payload.story_draft && !preparationOperation) {
      state.lastJobMessage = failure || `${payload.operation === "model_download" ? "Model download" : payload.operation === "calibrate" ? "Calibration" : "Generation"} ${payload.state}.`;
      if (failure) setError(failure);
      jobStatus.textContent = state.lastJobMessage;
    }
    if (payload.state === "succeeded" && payload.story_draft?.scenes) {
      draftProposals = payload.story_draft.scenes; renderDraftProposals(); $("#story-draft-note").textContent = "Choose a proposal to copy it into editable scene fields; it is not saved yet."; $("#draft-story-scenes").disabled = false;
    } else if (payload.state && payload.story_draft) {
      $("#story-draft-note").textContent = payload.error || `Story drafting ${payload.state}.`; $("#draft-story-scenes").disabled = false;
    } else if (payload.state === "succeeded" && payload.output_id && !state.variants.some((item) => item.outputId === payload.output_id)) {
      const variant = { outputId: payload.output_id, seed: $("#seed").value, mediaFile: isImageModel() ? "image.png" : "video.mp4" };
      state.variants.unshift(variant); promoteVariant(variant); state.lastJobMessage = "Generation completed and saved atomically."; jobStatus.textContent = state.lastJobMessage;
    } else if (payload.operation === "model_download") {
      state.downloadModelJobId = null;
      $("#preparation-status").textContent = failure || (payload.state === "succeeded" ? "Model download complete. Generate a profile before composing." : `Model download ${payload.state}.`);
      if (failure) setError(failure);
      void loadPreparationCatalog();
    } else if (payload.operation === "calibrate") {
      state.calibrationJobId = null;
      const message = failure || (payload.state === "succeeded" ? "Profile generation complete. This recipe is now measured and ready to compose." : `Profile generation ${payload.state}.`);
      $("#preparation-status").textContent = message;
      if (failure) setError(failure);
      void loadPreparationCatalog();
    } else if (payload.state) jobStatus.textContent = state.lastJobMessage;
  }
}
async function refresh() {
  try {
    const status = await invoke("worker_status"); state.connected = status.connected; state.models = status.availableModels; state.recipes = status.measuredRecipes; state.activeJob = status.activeJob;
    state.recipe = composeRecipeName(state.quality, state.aspect, state.frames);
    if (state.activeJob?.operation === "model_download") state.downloadModelJobId = state.activeJob.job_id || state.activeJob.jobId;
    if (state.activeJob?.operation === "calibrate") state.calibrationJobId = state.activeJob.job_id || state.activeJob.jobId;
    connection.textContent = status.connected ? `Ready · worker protocol v${status.protocolVersion}` : status.error || "Worker unavailable";
    if (state.activeJob) {
      const activeText = `${state.activeJob.status_text || state.activeJob.statusText || "Generating"} · ${Math.round((state.activeJob.progress || 0) * 100)}%`;
      if (state.activeJob.operation === "model_download" || state.activeJob.operation === "calibrate") $("#preparation-status").textContent = activeText;
      else jobStatus.textContent = activeText;
    }
    recordTerminal(status.events || []); updateControls(); renderModelDownloadProgress(state.activeJob); renderCalibrationProgress(state.activeJob); renderRecipeNote(); renderDurationControl(); void maybeShowRequiredModelSetup();
  } catch { state.connected = false; connection.textContent = "Worker unavailable"; updateControls(); }
}
async function maybeShowRequiredModelSetup() {
  if (requiredModelSetupChecked || !state.connected || activeProfile() || $("#onboarding").open) return;
  requiredModelSetupChecked = true;
  try {
    const response = await invoke("model_catalog");
    requiredModel = (response.models || []).find((model) => model.model_id === DEFAULT_MODEL_ID) || null;
    if (!requiredModel) return;
    $("#required-model-copy").textContent = requiredModel.installed
      ? `${requiredModel.display_name} is installed, but this Mac has no valid measured profile yet. Generate one on this Mac before generating.`
      : `${requiredModel.display_name} is the default video model and is required before SynVid can create video on this Mac.`;
    $("#required-model-facts").textContent = "Model: " + requiredModel.display_name + " · " + requiredModel.expected_size_gib + " GB expected · " + requiredModel.license + " · revision " + requiredModel.revision;
    $("#download-required-model").hidden = Boolean(requiredModel.installed);
    const calibration = requiredModel.calibration?.[state.recipe] || requiredModel.calibration?.Balanced;
    $("#generate-required-profile").hidden = !requiredModel.installed || !calibration?.reference;
    if (!$("#required-model-dialog").open) $("#required-model-dialog").showModal();
  } catch {
    requiredModelSetupChecked = false;
  }
}
async function downloadRequiredModel() {
  if (!requiredModel) return;
  const button = $("#download-required-model");
  const access = requiredModel.requires_access_confirmation ? " The publisher may require separate access approval." : "";
  const approved = await appConfirm("Download " + requiredModel.display_name + "?\n\nRevision: " + requiredModel.revision + "\nExpected size: " + requiredModel.expected_size_gib + " GB\nLicense: " + requiredModel.license + "." + access + "\n\nSynVid will begin the network transfer only after you confirm.");
  if (!approved) return;
  button.disabled = true;
  try {
    const accepted = await invoke("download_model", { modelId: requiredModel.model_id });
    state.activeJob = { job_id: accepted.job_id || accepted.jobId, operation: "model_download", status_text: "Downloading " + requiredModel.display_name, progress: 0 };
    state.downloadModelJobId = state.activeJob.job_id;
    $("#preparation-status").textContent = "Downloading " + requiredModel.display_name + "…";
    $("#required-model-dialog").close();
    updateControls();
  } catch (reason) {
    setError(String(reason));
    button.disabled = false;
  }
}
async function showLibrary(message = "") {
  const dialog = $("#library-dialog"); const list = $("#library-list"); const status = $("#library-status"); list.replaceChildren(); status.textContent = "";
  try {
    const { outputs = [] } = await invoke("list_outputs");
    for (const output of outputs) {
      const item = document.createElement("li"); item.className = "library-item";
      const select = document.createElement("button"); select.type = "button"; select.className = "variant"; select.textContent = `${output.output_id} · ${output.prompt || "Untitled"}`;
      select.addEventListener("click", () => { promoteVariant({ outputId: output.output_id, seed: output.seed ?? "unknown", mediaFile: output.media_file }); dialog.close(); });
      const remove = document.createElement("button"); remove.type = "button"; remove.className = "danger"; remove.textContent = "Delete";
      const deleteOutput = async () => {
        if (!(await appConfirm("Delete this completed generation from SynVid? Its media and local Library record will be permanently removed."))) return;
        remove.disabled = true;
        try {
          const result = await invoke("delete_output", { request: { outputId: output.output_id, cascade: false } });
          const deletedIds = new Set(result.deleted_output_ids ?? result.deletedOutputIds ?? [output.output_id]);
          state.variants = state.variants.filter((variant) => !deletedIds.has(variant.outputId));
          if (deletedIds.has(state.selectedVariant)) { state.selectedVariant = null; $("#media-preview").hidden = true; $("#export-controls").hidden = true; $("#image-edit-controls").hidden = true; $("#result-message").textContent = "The selected generation was deleted."; }
          renderVariants();
          const count = deletedIds.size;
          const success = `Deleted ${count} local generation${count === 1 ? "" : "s"} and freed ${formatBytes(result.freed_bytes ?? result.freedBytes)}.`;
          $("#result-message").textContent = success;
          await showLibrary(success);
        } catch (reason) { status.textContent = `Could not delete this generation: ${String(reason)}.`; remove.disabled = false; }
      };
      remove.addEventListener("click", () => { void deleteOutput(); });
      item.append(select, remove); list.append(item);
    }
    if (!outputs.length) list.textContent = "No completed local outputs yet.";
  } catch (reason) { list.textContent = "The local library is unavailable while the worker is disconnected."; status.textContent = `Could not refresh the Library: ${String(reason)}.`; }
  if (message) status.textContent = message;
  if (!dialog.open) dialog.showModal();
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
  const list = $("#preparation-model-list"); list.replaceChildren();
  for (const model of models.filter((candidate) => !SETTINGS_HIDDEN_MODEL_IDS.has(candidate.model_id))) {
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
        if (!(await appConfirm(`Remove ${model.display_name}? This deletes only its SynVid model files and cannot be undone.`))) return;
        remove.disabled = true;
        try {
          const result = await invoke("remove_model", { modelId: model.model_id });
          $("#preparation-status").textContent = result.removed ? `${model.display_name} removed; freed ${formatBytes(result.freed_bytes ?? result.freedBytes)}.` : `${model.display_name} was already absent.`;
          await loadPreparationCatalog();
        } catch (reason) {
          $("#preparation-status").textContent = `Could not remove ${model.display_name}: ${String(reason)}.`;
          remove.disabled = false;
        }
      });
      item.append(remove);
      if (model.retired) {
        const retired = document.createElement("p"); retired.className = "field-help"; retired.textContent = "Retired; no new downloads are available."; item.append(retired);
      }
    } else {
      const download = document.createElement("button"); download.type = "button"; download.textContent = "Download model…";
      download.addEventListener("click", async () => {
        const access = model.requires_access_confirmation ? " Access approval is required." : "";
        const approved = await appConfirm(`Download ${model.display_name}?\n\nRevision: ${model.revision}\nExpected size: ${model.expected_size_gib} GB\nLicense: ${model.license}.${access}\n\nSynVid will request a final authorization before any network transfer.`);
        if (!approved) return;
        download.disabled = true;
        try {
          const accepted = await invoke("download_model", { modelId: model.model_id });
          state.activeJob = { job_id: accepted.job_id || accepted.jobId, operation: "model_download", status_text: `Downloading ${model.display_name}`, progress: 0 };
          state.downloadModelJobId = state.activeJob.job_id;
          $("#preparation-status").textContent = `Downloading ${model.display_name}…`;
          updateControls();
        } catch (reason) { setError(String(reason)); download.disabled = false; }
      });
      item.append(download);
    }
    if (model.installed && model.calibration) {
      for (const [recipeName, info] of Object.entries(model.calibration)) {
        if (info.measured || !info.reference) continue;
        const calibrate = document.createElement("button"); calibrate.type = "button"; calibrate.textContent = `Generate ${recipeName} profile…`;
        calibrate.addEventListener("click", () => void runCalibration(model, recipeName, info.reference, calibrate));
        item.append(calibrate);
      }
    }
    list.append(item);
  }
}
async function runCalibration(model, recipeName, reference, button) {
  const frames = reference.frames ? `${reference.frames} frames at ${reference.fps} fps, ` : "";
  const minGiB = Math.ceil((reference.min_system_memory_bytes || 0) / 1024 ** 3);
  const message = `Generate a profile for ${model.display_name} (${recipeName})?\n\n`
    + `This runs a real ${reference.width} × ${reference.height}, ${frames}${reference.steps}-step generation on this Mac to measure `
    + `its own memory use and speed. It can take many minutes, and SynVid will be unusable for anything else while it runs.\n\n`
    + `This Mac needs at least ${minGiB} GB of memory for this recipe. These numbers were measured on the developer's own Mac; `
    + `yours may take more or less time.`;
  const approved = await appConfirm(message);
  if (!approved) return;
  button.disabled = true;
  try {
    const accepted = await invoke("calibrate_model", { modelId: model.model_id, recipe: recipeName });
    state.activeJob = { job_id: accepted.job_id || accepted.jobId, operation: "calibrate", status_text: `Calibrating ${model.display_name}`, progress: 0 };
    state.calibrationJobId = state.activeJob.job_id;
    $("#preparation-status").textContent = `Generating a profile for ${model.display_name}…`;
    updateControls();
  } catch (reason) { setError(String(reason)); button.disabled = false; }
}
async function loadPreparationCatalog() {
  const list = $("#preparation-model-list");
  if (!list || list.dataset.loading === "true") return;
  list.dataset.loading = "true";
  list.textContent = "Loading model catalog…";
  try {
    const { models = [] } = await invoke("model_catalog");
    renderModelCatalog(models);
  } catch (reason) {
    list.textContent = "Model catalog unavailable while the worker is disconnected.";
    $("#preparation-status").textContent = "Open Preparation again after the local worker reconnects.";
    setError(String(reason));
  } finally {
    list.dataset.loading = "false";
  }
}
async function showSettings() {
  const dialog = $("#settings-dialog");
  if (!dialog.open) dialog.showModal();
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
  $("#delete-story").disabled = !story;
  $("#delete-story-cascade").disabled = !story;
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
$("#complete-onboarding").addEventListener("click", () => { localStorage.setItem(ONBOARDING_KEY, "complete"); void maybeShowRequiredModelSetup(); });
$("#download-required-model").addEventListener("click", () => void downloadRequiredModel());
$("#generate-required-profile").addEventListener("click", () => {
  const calibration = requiredModel?.calibration?.[state.recipe] || requiredModel?.calibration?.Balanced;
  if (!requiredModel || !calibration?.reference) return setError("The selected model has no available profile recipe.");
  $("#required-model-dialog").close();
  void runCalibration(requiredModel, state.recipe in (requiredModel.calibration || {}) ? state.recipe : "Balanced", calibration.reference, $("#generate-required-profile"));
});
$("#required-model-not-now").addEventListener("click", () => $("#required-model-dialog").close());
$("#start-walkthrough").addEventListener("click", startWalkthrough);
for (const button of document.querySelectorAll("[data-workspace-tab]")) button.addEventListener("click", () => setWorkspaceTab(button.dataset.workspaceTab));
$("#walkthrough-back").addEventListener("click", () => { if (walkthroughIndex > 0) { walkthroughIndex -= 1; renderWalkthrough(); } });
$("#walkthrough-next").addEventListener("click", () => { if (walkthroughIndex === WALKTHROUGH_STEPS.length - 1) stopWalkthrough(); else { walkthroughIndex += 1; renderWalkthrough(); } });
$("#walkthrough-close").addEventListener("click", stopWalkthrough);
window.addEventListener("resize", repositionWalkthrough);
window.addEventListener("scroll", repositionWalkthrough, true);
$("#prompt").addEventListener("input", saveHistory); $("#seed").addEventListener("change", saveHistory);
$("#random-seed").addEventListener("click", () => { $("#seed").value = String(Math.floor(Math.random() * 2_147_483_647)); saveHistory(); });
for (const button of document.querySelectorAll("[data-recipe]")) button.addEventListener("click", () => { if (button.disabled) return; setRecipe(button.dataset.recipe); saveHistory(); });
for (const button of document.querySelectorAll("[data-aspect]")) button.addEventListener("click", () => { if (button.disabled) return; setAspect(button.dataset.aspect); saveHistory(); });
$("#duration-slider").addEventListener("input", () => {
  const options = durationOptionsFor(selectedModel(), state.quality);
  const option = options[Number($("#duration-slider").value)];
  if (option) setDuration(option.frames);
});
$("#duration-slider").addEventListener("change", () => saveHistory());
for (const button of document.querySelectorAll("[data-mode]")) button.addEventListener("click", () => {
  if (isImageModel() || button.disabled) return;
  state.mode = button.dataset.mode;
  for (const item of document.querySelectorAll("[data-mode]")) { const selected = item === button; item.setAttribute("aria-checked", String(selected)); item.classList.toggle("selected", selected); }
  $("#choose-image").hidden = state.mode !== "image"; $("#source-image-status").hidden = state.mode !== "image";
});
$("#choose-image").addEventListener("click", async () => {
  try { const selected = await invoke("choose_source_image"); state.sourceImageId = selected.sourceImageId; $("#source-image-status").textContent = state.sourceImageId ? "Source image selected and copied into SynVid storage." : "No source image selected."; }
  catch (reason) { setError(String(reason)); }
});
$("#model").addEventListener("change", () => {
  state.modelId = $("#model").value;
  state.frames = isWanModel() ? 41 : 49;
  state.aspect = isWanModel() ? "Landscape" : "Square";
  setAspect(state.aspect);
  setRecipe("Balanced");
  updateControls();
});
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
$("#reset-preset").addEventListener("click", () => { state.frames = 49; setAspect("Square"); setRecipe("Balanced"); saveHistory(); });
$("#undo").addEventListener("click", () => { if (state.historyIndex > 0) { state.historyIndex--; applyDraft(state.history[state.historyIndex]); renderHistory(); saveDraft(); } });
$("#redo").addEventListener("click", () => { if (state.historyIndex < state.history.length - 1) { state.historyIndex++; applyDraft(state.history[state.historyIndex]); renderHistory(); saveDraft(); } });
$("#library-button").addEventListener("click", () => showLibrary()); $("#settings-button").addEventListener("click", showSettings); $("#about-button").addEventListener("click", showAbout); $("#recovery-button").addEventListener("click", showRecovery);
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
async function deleteStory(cascade) {
  if (!activeStory) return;
  const message = cascade
    ? `Delete "${activeStory.title}" and every generated still, clip, narration, and composed movie it still owns? Media shared with another story is kept.`
    : `Delete "${activeStory.title}"? The story project is permanently removed, but its generated media stays in the Library.`;
  if (!(await appConfirm(message))) return;
  $("#delete-story").disabled = true; $("#delete-story-cascade").disabled = true;
  try {
    const result = await invoke("story_delete", { request: { storyId: activeStory.story_id, expectedRevision: activeStory.revision, cascade } });
    const select = $("#story-list");
    const option = select.querySelector(`option[value="${result.story_id}"]`);
    if (option) option.remove();
    select.value = "";
    renderStory(null);
    const freed = cascade ? ` Freed ${formatBytes(result.freed_bytes ?? result.freedBytes)}.` : "";
    $("#story-draft-note").textContent = `Story deleted.${freed}`;
  } catch (reason) { setError(String(reason)); $("#delete-story").disabled = !activeStory; $("#delete-story-cascade").disabled = !activeStory; }
}
$("#delete-story").addEventListener("click", () => void deleteStory(false));
$("#delete-story-cascade").addEventListener("click", () => void deleteStory(true));
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
  if (!(await appConfirm("Clean SynVid temporary files? Completed outputs, stories, and models will not be deleted."))) return;
  const button = $("#clean-temporary"); button.disabled = true;
  try { const result = await invoke("clean_temporary"); $("#cleanup-status").textContent = `Temporary files cleaned; freed ${formatBytes(result.freed_bytes)}.`; }
  catch (reason) { $("#cleanup-status").textContent = `Cleanup could not run: ${String(reason)}`; }
  finally { button.disabled = false; }
});
let diagnosticsText = "";
$("#preview-diagnostics").addEventListener("click", async () => {
  const button = $("#preview-diagnostics"); button.disabled = true;
  try {
    const { text } = await invoke("diagnostic_bundle");
    diagnosticsText = text; $("#diagnostics-preview").value = text; $("#export-diagnostics").disabled = false;
    $("#diagnostics-status").textContent = "Preview matches exactly what export will save.";
  } catch (reason) { $("#diagnostics-status").textContent = `Could not build a preview: ${String(reason)}`; }
  finally { button.disabled = false; }
});
$("#export-diagnostics").addEventListener("click", async () => {
  if (!diagnosticsText) return;
  const button = $("#export-diagnostics"); button.disabled = true;
  try { const result = await invoke("export_diagnostics", { text: diagnosticsText }); $("#diagnostics-status").textContent = result.saved ? "Diagnostic export saved." : "Export cancelled."; }
  catch (reason) { $("#diagnostics-status").textContent = `Export could not run: ${String(reason)}`; }
  finally { button.disabled = false; }
});
generateButton.addEventListener("click", async () => {
  const prompt = $("#prompt").value.trim(); const seed = Number($("#seed").value);
  if (!prompt) return setError(`Add an ${isImageModel() ? "image" : "video"} description before generating.`);
  if (!Number.isInteger(seed) || seed < 0 || seed > 2_147_483_647) return setError("Seed must be a whole number from 0 to 2147483647.");
  if (!selectedModel()?.installed) return setError("Install the selected model before generating.");
  if (!activeProfile()) return setError("The selected model recipe is not available.");
  if (state.mode === "image" && !state.sourceImageId) {
    setError("Choose a source image before starting image-to-video.");
    return;
  }
  setError(); state.lastJobMessage = ""; generateButton.disabled = true; jobStatus.textContent = "Submitting generation…";
  try { const accepted = await invoke("generate", { request: { modelId: state.modelId, prompt, seed, recipe: state.recipe, sourceImageId: state.mode === "image" && !isImageModel() ? state.sourceImageId : null } }); state.activeJob = { job_id: accepted.job_id || accepted.jobId, operation: "job", status_text: "Loading model", progress: 0 }; jobStatus.textContent = "Loading model…"; }
  catch (reason) { setError(String(reason)); jobStatus.textContent = "Generation was not started."; }
  updateControls();
});
async function cancelActiveJob(button) {
  if (!state.activeJob) return;
  button.disabled = true;
  const preparationJob = state.activeJob.operation === "model_download" || state.activeJob.operation === "calibrate";
  if (preparationJob) $("#preparation-status").textContent = "Cancelling preparation…";
  else jobStatus.textContent = "Cancelling generation…";
  try { await invoke("cancel", { jobId: state.activeJob.job_id || state.activeJob.jobId }); }
  catch (reason) { setError(String(reason)); }
  finally { button.disabled = false; }
}
cancelButton.addEventListener("click", () => void cancelActiveJob(cancelButton));
preparationCancelButton.addEventListener("click", () => void cancelActiveJob(preparationCancelButton));
void loadPreparationCatalog(); void refresh(); window.setInterval(() => void refresh(), 750);
