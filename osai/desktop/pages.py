"""HTML for the desktop app (self-contained: no external fonts, scripts or CDNs)."""

from __future__ import annotations

STYLE = """
:root { --bg:#f5f6f8; --card:#fff; --text:#1b1f24; --muted:#5b6470; --accent:#1f6feb; --line:#dde1e6;
        --warn-bg:#fff7e6; --warn:#8a5a00; --ok:#166534; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0f1115; --card:#181b21; --text:#e6e8eb; --muted:#9aa3ad; --accent:#2f81f7; --line:#2a2f37;
          --warn-bg:#2b2210; --warn:#e3b341; --ok:#3fb950; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text);
       font-family: "Segoe UI", Tahoma, "Noto Naskh Arabic", system-ui, sans-serif; }
main { max-width: 800px; margin: 0 auto; min-height: 100vh; display:flex; flex-direction:column; padding: 16px; }
header { display:flex; flex-wrap:wrap; align-items:center; gap:8px 16px; margin-bottom: 8px; }
header h1 { font-size: 18px; margin: 0; flex: 1 1 auto; }
a, .link { color: var(--accent); background:none; border:0; font:inherit; cursor:pointer; padding:0; text-decoration:none; }
.muted { color: var(--muted); font-size: 13px; }
.card { background: var(--card); border:1px solid var(--line); border-radius: 12px; padding: 16px; margin: 8px 0; }
.warn { background: var(--warn-bg); color: var(--warn); border-radius: 10px; padding: 10px 12px; font-size: 13px;
        margin: 6px 0; white-space: pre-wrap; }
label { display:block; font-weight: 600; margin: 14px 0 6px; }
input, select { width:100%; padding: 10px 12px; border-radius: 10px; border:1px solid var(--line);
                background: var(--card); color: var(--text); font: inherit; }
.btn { padding: 10px 18px; border-radius: 10px; border: 0; background: var(--accent); color: #fff;
       font: inherit; cursor: pointer; }
.btn.secondary { background: transparent; color: var(--text); border:1px solid var(--line); }
.btn:disabled { opacity: .6; cursor: default; }
#log { flex:1; display:flex; flex-direction:column; gap:10px; padding: 8px 0 16px; }
.msg { max-width: 85%; padding: 10px 14px; border-radius: 14px; white-space: pre-wrap; line-height: 1.6;
       background: var(--card); border: 1px solid var(--line); unicode-bidi: plaintext; }
.me { align-self: flex-end; background: var(--accent); color: #fff; border-color: transparent; }
.bot { align-self: flex-start; }
.wait { color: var(--muted); font-style: italic; }
.examples { display:flex; flex-wrap:wrap; gap:8px; margin: 6px 0 10px; }
.examples button { background: var(--card); color: var(--text); border:1px solid var(--line);
                   border-radius: 999px; padding: 6px 12px; cursor:pointer; font: inherit; font-size: 13px; }
form.ask { display:flex; gap:8px; position: sticky; bottom: 0; background: var(--bg); padding: 8px 0; }
form.ask input { flex:1; unicode-bidi: plaintext; }
ul.files { margin: 6px 0 0; padding-left: 20px; font-size: 13px; }
"""

COMMON_JS = """
const TOKEN = "__TOKEN__";
async function post(path, body) {
  const r = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json", "X-OSAI-Token": TOKEN},
                               body: JSON.stringify(body || {})});
  let data = {};
  try { data = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
  return data;
}
function openFolder() { post("/api/open-data-folder").catch(e => alert(e.message)); }
async function quitApp() {
  if (!confirm("Stop OS AI Assistant?")) return;
  try { await post("/api/quit"); } catch (e) {}
  document.body.innerHTML = "<main><h1>OS AI Assistant stopped.</h1><p class='muted'>Open it again from the Start menu or desktop shortcut.</p></main>";
}
"""

SETUP_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>OS AI Assistant — Settings</title><style>__STYLE__</style></head>
<body><main>
<header><h1>OS AI Assistant — Settings</h1>
  <a href="/" id="back">Back to chat</a><button class="link" onclick="quitApp()">Quit</button></header>
<p class="muted">Choose which AI service answers your questions. Your key is encrypted for your Windows account and
stays on this computer. Your spreadsheet numbers are calculated on this computer; only your question and the
column names are sent to the AI service.</p>
<div class="card">
<form id="settings">
  <label for="preset">AI service</label>
  <select id="preset" name="preset">
    <option value="openrouter">OpenRouter (has free models)</option>
    <option value="gemini">Google Gemini</option>
    <option value="groq">Groq</option>
    <option value="openai">OpenAI</option>
    <option value="anthropic">Anthropic Claude</option>
    <option value="deepseek">DeepSeek</option>
    <option value="mistral">Mistral</option>
    <option value="together">Together AI</option>
    <option value="ollama">Ollama on this computer (no key)</option>
    <option value="lmstudio">LM Studio on this computer (no key)</option>
    <option value="custom">Other (OpenAI-compatible address)</option>
  </select>
  <div id="urlrow" hidden>
    <label for="base_url">API address</label>
    <input id="base_url" name="base_url" placeholder="https://example.com/v1" dir="ltr">
  </div>
  <label for="model">Model name</label>
  <input id="model" name="model" dir="ltr" placeholder="e.g. meta-llama/llama-3.3-70b-instruct:free">
  <p class="muted" id="modelhint"></p>
  <div id="keyrow">
    <label for="api_key">API key</label>
    <input id="api_key" name="api_key" type="password" dir="ltr" autocomplete="off">
    <p class="muted" id="keyhint"></p>
  </div>
  <div id="error" class="warn" hidden></div>
  <p><button class="btn" type="submit" id="save">Save</button></p>
</form>
</div>
<div class="card">
  <b>Your data folder</b>
  <p class="muted">Put Excel (.xlsx) or CSV files here. The first row of each sheet must contain the column names.</p>
  <p class="muted" dir="ltr">__DATA_FOLDER__</p>
  <button class="btn secondary" type="button" onclick="openFolder()">Open data folder</button>
</div>
</main>
<script>
__COMMON_JS__
const current = __CURRENT__;
const HINTS = {
  openrouter: "On openrouter.ai open Models, filter by Free, and copy a name ending in :free.",
  gemini: "For example gemini-2.5-flash. Create a key at aistudio.google.com.",
  groq: "For example llama-3.3-70b-versatile. Create a key at console.groq.com.",
  anthropic: "Leave empty to use the default Claude model.",
  ollama: "The model you pulled in Ollama, for example qwen2.5:7b.",
  lmstudio: "The model loaded in LM Studio.",
};
const NO_KEY = new Set(["ollama", "lmstudio"]);
const f = document.getElementById("settings"), preset = document.getElementById("preset");
function refresh() {
  document.getElementById("urlrow").hidden = preset.value !== "custom";
  document.getElementById("keyrow").hidden = NO_KEY.has(preset.value);
  document.getElementById("modelhint").textContent = HINTS[preset.value] || "";
}
preset.value = current.preset || "openrouter";
document.getElementById("model").value = current.model || "";
document.getElementById("base_url").value = current.base_url || "";
document.getElementById("keyhint").textContent = current.key_saved
  ? "A key is saved. Leave empty to keep it." : "Paste your key. It is never shown again.";
if (!current.configured) document.getElementById("back").hidden = true;
preset.addEventListener("change", refresh); refresh();
f.addEventListener("submit", async e => {
  e.preventDefault();
  const err = document.getElementById("error"); err.hidden = true;
  const btn = document.getElementById("save"); btn.disabled = true;
  try {
    await post("/api/settings", {preset: preset.value, model: f.model.value.trim(),
      base_url: f.base_url.value.trim(), api_key: f.api_key.value.trim()});
    location.href = "/";
  } catch (x) { err.textContent = x.message; err.hidden = false; }
  finally { btn.disabled = false; }
});
</script></body></html>
"""

CHAT_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>OS AI Assistant</title><style>__STYLE__</style></head>
<body><main>
<header><h1>OS AI Assistant</h1>
  <button class="link" onclick="openFolder()">Data folder</button>
  <a href="/settings">Settings</a>
  <button class="link" onclick="quitApp()">Quit</button></header>
<div class="card" id="data"><span class="muted">Checking your data folder…</span></div>
<div class="examples">
  <button type="button">مجموع مصارف چقدر است؟</button>
  <button type="button">ټول لګښتونه څومره دي؟</button>
  <button type="button">What is the total amount?</button>
</div>
<div id="log"></div>
<form class="ask" id="f">
  <input id="q" dir="auto" autocomplete="off" placeholder="سؤال خود را بنویسید / Type your question" autofocus>
  <button class="btn" type="submit" id="send">Send</button>
</form>
</main>
<script>
__COMMON_JS__
const log = document.getElementById("log"), q = document.getElementById("q"), send = document.getElementById("send");
function add(text, cls) {
  const d = document.createElement("div"); d.className = "msg " + cls; d.dir = "auto"; d.textContent = text;
  log.appendChild(d); d.scrollIntoView({behavior: "smooth", block: "end"}); return d;
}
async function loadStatus() {
  const box = document.getElementById("data");
  try {
    const s = await post("/api/status");
    box.innerHTML = "";
    const title = document.createElement("div");
    title.innerHTML = s.tables.length ? "<b>Your data</b> <span class='muted'>(" + s.tables.length + " tables)</span>"
      : "<b>No data yet.</b> <span class='muted'>Click “Data folder”, copy your Excel or CSV files there, then click Refresh.</span>";
    box.appendChild(title);
    if (s.tables.length) {
      const ul = document.createElement("ul"); ul.className = "files";
      s.tables.forEach(t => { const li = document.createElement("li"); li.dir = "auto";
        li.textContent = t.file + (t.sheet ? " / " + t.sheet : "") + " — " + t.columns.join(", "); ul.appendChild(li); });
      box.appendChild(ul);
    }
    s.skipped.forEach(line => { const w = document.createElement("div"); w.className = "warn"; w.textContent = "Skipped: " + line; box.appendChild(w); });
    const r = document.createElement("button"); r.className = "link"; r.textContent = "Refresh"; r.onclick = loadStatus;
    box.appendChild(r);
  } catch (e) { box.textContent = "Could not read the data folder: " + e.message; }
}
async function ask(text) {
  if (!text.trim()) return;
  add(text, "me"); q.value = ""; send.disabled = true;
  const waiting = add("…", "bot wait");
  try {
    const data = await post("/api/ask", {text});
    waiting.textContent = data.reply + (data.detail ? "\\n\\n(" + data.detail + ")" : "");
    waiting.className = "msg bot";
  } catch (e) {
    waiting.textContent = "OS AI Assistant is not responding. Is it still running? (" + e.message + ")";
    waiting.className = "msg bot";
  } finally { send.disabled = false; q.focus(); }
}
document.getElementById("f").addEventListener("submit", e => { e.preventDefault(); ask(q.value); });
document.querySelectorAll(".examples button").forEach(b => b.addEventListener("click", () => ask(b.textContent)));
loadStatus();
</script></body></html>
"""


def render(template: str, **values: str) -> str:
    page = template.replace("__STYLE__", STYLE).replace("__COMMON_JS__", COMMON_JS)
    for key, value in values.items():
        page = page.replace(f"__{key}__", value)
    return page
