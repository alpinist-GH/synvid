import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("./main.js", import.meta.url), "utf8");
const markup = await readFile(new URL("./index.html", import.meta.url), "utf8");

test("walkthrough explains and highlights the primary creation flow", () => {
  assert.match(markup, /id="start-walkthrough"/);
  assert.match(markup, /id="walkthrough"/);
  for (const control of ["#prompt", "#model", "#recipe-buttons", "#seed", "#generate", "#variant-list", "#library-button"]) {
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
  assert.match(source, /model_id === "ltx-video"/);
  assert.match(markup, /Download and validate/);
});

test("an unavailable profile offers generation with live progress", () => {
  assert.match(markup, /id="generate-profile"/);
  assert.match(markup, /id="profile-generation-progress"/);
  assert.match(source, /canGenerateProfile/);
  assert.match(source, /invoke\("calibrate_model"/);
  assert.match(source, /profileGenerationProgress/);
});

test("video controls follow each model's measured recipe shapes", () => {
  assert.match(source, /function aspectForReference/);
  assert.match(source, /function availableAspectsFor/);
  assert.match(source, /function availableQualitiesFor/);
  assert.match(source, /durationOptionsFor\(model, quality, aspect/);
  assert.match(source, /button\.disabled = !enabled/);
  assert.match(source, /options\.length === 1/);
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
