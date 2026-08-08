// This frontend is deliberately vanilla static assets, not an npm bundle.
// Tauri exposes this narrow API only because `withGlobalTauri` is enabled in
// the checked-in desktop configuration.
const invoke = window.__TAURI__.core.invoke;

const DRAFT_KEY = "synvid.stage2.draft.v1";
const ONBOARDING_KEY = "synvid.stage2.onboarding.v1";
const MAX_HISTORY = 20;
const state = { recipes: null, activeJob: null, connected: false, recipe: "Balanced", mode: "text", sourceImageId: null, variants: [], selectedVariant: null, history: [], historyIndex: -1 };
const $ = (selector) => document.querySelector(selector);
const connection = $("#connection");
const jobStatus = $("#job-status");
const generateButton = $("#generate");
const cancelButton = $("#cancel");
const error = $("#form-error");

function setError(message = "") { error.textContent = message; error.hidden = !message; }
function profileLabel(profile) { return `${profile.width} × ${profile.height} · ${profile.frames} frames · ${profile.steps} steps`; }
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
function activeProfile() { return state.recipes?.[state.recipe] || null; }
function updateControls() {
  const profile = activeProfile(); const available = state.connected && profile && !state.activeJob;
  generateButton.disabled = !available; cancelButton.hidden = !state.activeJob;
  $("#profile").textContent = profile ? profileLabel(profile) : "Not available";
  $("#fps").textContent = profile ? `${profile.fps} FPS (Native)` : "—";
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
  state.selectedVariant = variant.outputId; $("#result-message").textContent = `Selected ${variant.outputId}. The canonical output remains immutable.`; $("#export-controls").hidden = false; renderVariants();
}
function recordTerminal(events) {
  for (const event of events) {
    if (event.kind !== "terminal") continue;
    const payload = event.payload ?? {};
    if (payload.state === "succeeded" && payload.output_id && !state.variants.some((item) => item.outputId === payload.output_id)) {
      const variant = { outputId: payload.output_id, seed: $("#seed").value };
      state.variants.unshift(variant); promoteVariant(variant); jobStatus.textContent = "Generation completed and saved atomically.";
    } else if (payload.state) jobStatus.textContent = payload.error || `Generation ${payload.state}.`;
  }
}
async function refresh() {
  try {
    const status = await invoke("worker_status"); state.connected = status.connected; state.recipes = status.measuredRecipes; state.activeJob = status.activeJob;
    connection.textContent = status.connected ? `Ready · worker protocol v${status.protocolVersion}` : status.error || "Worker unavailable";
    if (state.activeJob) jobStatus.textContent = `${state.activeJob.status_text || state.activeJob.statusText || "Generating"} · ${Math.round((state.activeJob.progress || 0) * 100)}%`;
    recordTerminal(status.events || []); updateControls();
  } catch { state.connected = false; connection.textContent = "Worker unavailable"; updateControls(); }
}
async function showLibrary() {
  const dialog = $("#library-dialog"); const list = $("#library-list"); list.replaceChildren();
  try {
    const { outputs = [] } = await invoke("list_outputs");
    for (const output of outputs) { const item = document.createElement("li"); const button = document.createElement("button"); button.type = "button"; button.className = "variant"; button.textContent = `${output.output_id} · ${output.prompt || "Untitled"}`; button.addEventListener("click", () => { promoteVariant({ outputId: output.output_id, seed: output.seed ?? "unknown" }); dialog.close(); }); item.append(button); list.append(item); }
    if (!outputs.length) list.textContent = "No completed local outputs yet.";
  } catch { list.textContent = "The local library is unavailable while the worker is disconnected."; }
  dialog.showModal();
}
async function showRecovery() {
  const dialog = $("#recovery-dialog"); $("#recovery-preview").textContent = "Checking recoverable state…"; dialog.showModal();
  try { const status = await invoke("worker_status"); $("#recovery-preview").textContent = status.connected ? "Recovery can safely check incomplete local work. No completed output will be deleted." : "Worker unavailable. Reopen SynVid to inspect recovery state."; } catch { $("#recovery-preview").textContent = "Worker unavailable. Reopen SynVid to inspect recovery state."; }
}

restoreDraft();
if (!localStorage.getItem(ONBOARDING_KEY)) $("#onboarding").showModal();
$("#complete-onboarding").addEventListener("click", () => localStorage.setItem(ONBOARDING_KEY, "complete"));
$("#prompt").addEventListener("input", saveHistory); $("#seed").addEventListener("change", saveHistory);
$("#random-seed").addEventListener("click", () => { $("#seed").value = String(Math.floor(Math.random() * 2_147_483_647)); saveHistory(); });
for (const button of document.querySelectorAll("[data-recipe]")) button.addEventListener("click", () => { setRecipe(button.dataset.recipe); saveHistory(); });
for (const button of document.querySelectorAll("[data-mode]")) button.addEventListener("click", () => {
  state.mode = button.dataset.mode;
  for (const item of document.querySelectorAll("[data-mode]")) { const selected = item === button; item.setAttribute("aria-checked", String(selected)); item.classList.toggle("selected", selected); }
  $("#choose-image").hidden = state.mode !== "image"; $("#source-image-status").hidden = state.mode !== "image";
});
$("#choose-image").addEventListener("click", async () => {
  try { const selected = await invoke("choose_source_image"); state.sourceImageId = selected.sourceImageId; $("#source-image-status").textContent = state.sourceImageId ? "Source image selected and copied into SynVid storage." : "No source image selected."; }
  catch (reason) { setError(String(reason)); }
});
for (const button of document.querySelectorAll("[data-export]")) button.addEventListener("click", async () => {
  if (!state.selectedVariant) return;
  button.disabled = true;
  try { const result = await invoke("export_video", { outputId: state.selectedVariant, profile: button.dataset.export }); $("#result-message").textContent = `${result.profile} export completed without regenerating the canonical video.`; }
  catch (reason) { setError(String(reason)); }
  finally { button.disabled = false; }
});
$("#reset-preset").addEventListener("click", () => { setRecipe("Balanced"); saveHistory(); });
$("#undo").addEventListener("click", () => { if (state.historyIndex > 0) { state.historyIndex--; applyDraft(state.history[state.historyIndex]); renderHistory(); saveDraft(); } });
$("#redo").addEventListener("click", () => { if (state.historyIndex < state.history.length - 1) { state.historyIndex++; applyDraft(state.history[state.historyIndex]); renderHistory(); saveDraft(); } });
$("#library-button").addEventListener("click", showLibrary); $("#recovery-button").addEventListener("click", showRecovery);
for (const button of document.querySelectorAll(".close-dialog")) button.addEventListener("click", () => button.closest("dialog").close());
$("#run-recovery").addEventListener("click", async () => { const button = $("#run-recovery"); button.disabled = true; try { const recovered = await invoke("recover"); $("#recovery-preview").textContent = `Recovered ${recovered.partialOutputCount ?? recovered.partial_output_count ?? 0} incomplete output(s). Completed media was not changed.`; } catch (reason) { $("#recovery-preview").textContent = `Recovery could not run: ${String(reason)}`; } finally { button.disabled = false; } });
generateButton.addEventListener("click", async () => {
  const prompt = $("#prompt").value.trim(); const seed = Number($("#seed").value);
  if (!prompt) return setError("Add a video description before generating.");
  if (!Number.isInteger(seed) || seed < 0 || seed > 2_147_483_647) return setError("Seed must be a whole number from 0 to 2147483647.");
  if (!activeProfile()) return setError("The selected LTX recipe is not measured on this Mac.");
  if (state.mode === "image" && !state.sourceImageId) {
    setError("Choose a source image before starting image-to-video.");
    return;
  }
  setError(); generateButton.disabled = true; jobStatus.textContent = "Submitting generation…";
  try { const accepted = await invoke("generate", { request: { prompt, seed, recipe: state.recipe, sourceImageId: state.mode === "image" ? state.sourceImageId : null } }); state.activeJob = { job_id: accepted.job_id || accepted.jobId, status_text: "Loading model", progress: 0 }; jobStatus.textContent = "Loading model…"; }
  catch (reason) { setError(String(reason)); jobStatus.textContent = "Generation was not started."; }
  updateControls();
});
cancelButton.addEventListener("click", async () => { if (!state.activeJob) return; cancelButton.disabled = true; jobStatus.textContent = "Cancelling generation…"; try { await invoke("cancel", { jobId: state.activeJob.job_id || state.activeJob.jobId }); } catch (reason) { setError(String(reason)); } finally { cancelButton.disabled = false; } });
void refresh(); window.setInterval(() => void refresh(), 750);
