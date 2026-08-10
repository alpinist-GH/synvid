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
  assert.match(source, /maybeShowRequiredModelSetup/);
  assert.match(source, /model_id === "ltx-video"/);
  assert.match(markup, /Download and validate/);
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

test("model downloads expose byte progress and diagnostics preview", async () => {
  assert.match(markup, /id="model-download-progress"/);
  assert.doesNotMatch(markup, /Developer tools/);
  assert.doesNotMatch(markup, /id="debug-log-window"/);
  assert.match(markup, /id="preview-diagnostics"/);
  assert.match(source, /operation === "model_download"/);
  assert.match(source, /Preview matches exactly what export will save/);
});
