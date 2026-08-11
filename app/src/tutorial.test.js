import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("./main.js", import.meta.url), "utf8");
const markup = await readFile(new URL("./index.html", import.meta.url), "utf8");

test("walkthrough explains and highlights the primary creation flow", () => {
  assert.match(markup, /id="start-walkthrough"/);
  assert.match(markup, /id="walkthrough"/);
  for (const control of ["#prompt", "#model", "#model-settings", "#seed", "#generate", "#variant-list", "#library-button"]) {
    assert.ok(source.includes(control));
  }
  assert.match(source, /walkthrough-target/);
  assert.match(source, /scrollIntoView/);
  assert.match(source, /positionWalkthrough/);
  assert.match(source, /addEventListener\("scroll", repositionWalkthrough, true\)/);
});

test("the frontend has no Keychain or Hugging Face token dependency", () => {
  assert.doesNotMatch(source, /hugging_face_credential_status|HUGGINGFACE_TOKEN|SYNVID_HF_TOKEN/);
  assert.match(markup, /starts without a Hugging Face account or API token/);
});

test("missing required video setup opens an explicit download dialog", () => {
  assert.match(markup, /id="required-model-dialog"/);
  assert.match(markup, /id="download-required-model"/);
  assert.match(markup, /id="generate-required-profile"/);
  assert.match(source, /maybeShowRequiredModelSetup/);
  assert.match(source, /model_id === DEFAULT_MODEL_ID/);
  assert.match(markup, /Download and validate/);
});

test("preparation is a separate tab before compose", () => {
  assert.match(markup, /id="preparation-tab"[^>]*aria-selected="true"/);
  assert.match(markup, /id="preparation-panel"/);
  assert.match(markup, /id="preparation-model-list"/);
  assert.match(markup, /id="preparation-cancel"/);
  assert.ok(markup.indexOf("data-workspace-tab=\"preparation\"") < markup.indexOf("data-workspace-tab=\"compose\""));
  assert.match(source, /function loadPreparationCatalog/);
  assert.match(source, /renderModelCatalog\(models\)/);
  assert.match(source, /invoke\("calibrate_model"/);
  assert.match(source, /Generate \$\{recipeName\} profile/);
  assert.doesNotMatch(markup, /id="setup-model"/);
  assert.doesNotMatch(markup, /id="generate-profile"/);
  assert.doesNotMatch(source, /const setupModelButton/);
});

test("video controls follow each model's measured recipe shapes", () => {
  assert.match(source, /function aspectForReference/);
  assert.match(source, /function availableAspectsFor/);
  assert.match(source, /function availableQualitiesFor/);
  assert.match(source, /durationOptionsFor\(model, quality, aspect/);
  assert.match(source, /button\.disabled = !enabled/);
  assert.match(source, /options\.length === 1/);
});

test("Wan 2.2 is the default and uses model-aware measured settings", () => {
  assert.match(markup, /<option value="wan2\.2-ti2v-5b-mlx">Wan 2\.2 TI2V 5B<\/option>/);
  assert.match(source, /DEFAULT_MODEL_ID = "wan2\.2-ti2v-5b-mlx"/);
  assert.match(source, /modelId: DEFAULT_MODEL_ID/);
  assert.match(source, /function recipeDescriptor/);
  assert.match(source, /selectedGenerationMode/);
  assert.doesNotMatch(markup, /Wan 2\.2[^<]*experimental/i);
  assert.match(markup, /id="wan-settings"/);
  assert.match(markup, /1280 × 704/);
  assert.match(markup, /<dt>Frames<\/dt><dd>41<\/dd>/);
  assert.doesNotMatch(source, /LTX Video is required before SynVid can create video/);
});

test("library deletion reports its result inside the open dialog", () => {
  assert.match(markup, /id="library-status"/);
  assert.match(source, /Could not delete this generation:/);
  assert.doesNotMatch(source, /Force delete/);
  assert.doesNotMatch(markup, /Force delete/);
  assert.match(source, /invoke\("delete_output", \{ request: \{ outputId: output\.output_id, cascade: false \} \}\)/);
  assert.match(source, /await showLibrary\(success\)/);
  assert.match(source, /Could not refresh the Library:/);
});

test("quality-failed Wan models are retired from generation settings", () => {
  for (const modelId of ["wan2.1-1.3b", "wan2.1-14b", "wan2.2-ti2v-5b"]) {
    assert.doesNotMatch(markup, new RegExp(`value="${modelId.replace(/\./g, "\\.")}"`));
  }
  assert.doesNotMatch(source, /Ready for experimental Wan 2\.2 testing/);
  assert.match(source, /model\.retired/);
  assert.match(source, /Could not remove \$\{model\.display_name\}/);
});

test("personal research models are hidden from Settings and generation controls", () => {
  for (const modelId of ["flux-dev", "flux-kontext-dev", "hunyuan15-480p-t2v", "hunyuan15-480p-i2v"]) {
    assert.match(source, new RegExp(`"${modelId}"`));
    assert.doesNotMatch(markup, new RegExp(`value="${modelId}"`));
  }
  assert.match(source, /models\.filter\(\(candidate\) => !SETTINGS_HIDDEN_MODEL_IDS\.has\(candidate\.model_id\)\)/);
});

test("model downloads expose byte progress and diagnostics preview", async () => {
  assert.match(markup, /id="model-download-progress"/);
  assert.doesNotMatch(markup, /Developer tools/);
  assert.doesNotMatch(markup, /id="debug-log-window"/);
  assert.match(markup, /id="preview-diagnostics"/);
  assert.match(source, /operation === "model_download"/);
  assert.match(source, /Preview matches exactly what export will save/);
});

test("failed generation stays visible after controls refresh", () => {
  assert.match(source, /lastJobMessage/);
  assert.match(source, /if \(state\.lastJobMessage\) jobStatus\.textContent = state\.lastJobMessage/);
  assert.match(source, /if \(failure\) setError\(failure\)/);
});
