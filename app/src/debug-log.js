const invoke = window.__TAURI__.core.invoke;
const lines = document.querySelector("#debug-log-lines");
const state = document.querySelector("#debug-log-state");

async function refresh() {
  try {
    const result = await invoke("debug_log");
    lines.textContent = result.lines?.length ? result.lines.join("\n") : "No worker diagnostics have been emitted.";
    state.textContent = "Live · last 128 lines";
    lines.scrollTop = lines.scrollHeight;
  } catch (error) {
    state.textContent = "Unavailable";
    lines.textContent = `Could not read worker diagnostics: ${String(error)}`;
  }
}

void refresh();
window.setInterval(() => void refresh(), 750);
