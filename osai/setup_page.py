"""HTML renderer for the local OS AI Core setup wizard."""

from __future__ import annotations

import html
import json
from collections.abc import Mapping


def render_setup_page(status: Mapping[str, object], csrf_token: str) -> str:
    payload = json.dumps(dict(status), ensure_ascii=False).replace("</", "<\\/")
    return _PAGE.replace("__STATUS_JSON__", payload).replace("__CSRF__", html.escape(csrf_token, quote=True))


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>OS AI Core Setup</title>
<style>body{font-family:Segoe UI,Arial,sans-serif;background:#f4f7fb;color:#172033;margin:0;padding:28px}.shell{max-width:900px;margin:auto}.card{background:white;padding:22px;border-radius:16px;margin:14px 0;box-shadow:0 8px 24px #0001}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}label{display:block;font-weight:600;margin:10px 0 5px}input,select{width:100%;padding:10px;box-sizing:border-box}.row{display:flex;gap:8px;align-items:center}.row input[type=checkbox]{width:auto}button,a{padding:10px 14px;border:0;border-radius:9px;text-decoration:none;font-weight:600}button{cursor:pointer}.primary{background:#1d4ed8;color:white}.secondary,a{background:#e2e8f0;color:#172033}.ok{color:#166534}.warn{color:#92400e}.muted{color:#64748b}@media(max-width:700px){.grid{grid-template-columns:1fr}}</style>
</head><body><div class="shell">
<div class="row" style="justify-content:space-between"><div><h1>OS AI Core Setup</h1><div class="muted">Windows local configuration wizard</div></div><a href="/">Dashboard</a></div>
<div class="card"><strong>UAT boundary:</strong> Excel can be activated now. Telegram and AI credentials can be stored securely, but live network connectors remain pending.</div>
<div class="grid">
<div class="card"><h2>1. Excel Sandbox</h2><div id="excelStatus" class="muted"></div><label class="row"><input id="excelEnabled" type="checkbox">Enable Excel Sandbox</label><label>Workbook</label><input id="excelPath" readonly><p><button id="createExcel" class="secondary">Create / Use Managed Workbook</button></p></div>
<div class="card"><h2>2. Telegram</h2><div id="telegramStatus" class="muted"></div><label class="row"><input id="telegramEnabled" type="checkbox">Enable Telegram configuration</label><label>Bot alias</label><input id="botAlias"><label>Bot token</label><input id="telegramToken" type="password" autocomplete="new-password" placeholder="New token only"><p><button id="clearTelegram" class="secondary">Clear stored token</button></p></div>
<div class="card"><h2>3. AI Provider</h2><div id="llmStatus" class="muted"></div><label>Provider</label><select id="provider"><option value="none">None</option><option value="openai">OpenAI</option><option value="local">Local model</option></select><label>Model</label><input id="model"><label>OpenAI API key</label><input id="openaiKey" type="password" autocomplete="new-password" placeholder="New key only"><p><button id="clearOpenAI" class="secondary">Clear stored key</button></p><label>Local model URL</label><input id="localUrl" placeholder="http://127.0.0.1:11434"></div>
<div class="card"><h2>4. Save</h2><div id="overallStatus" class="muted"></div><p class="muted">Secrets are encrypted by Windows DPAPI and are never returned by this page.</p><button id="save" class="primary">Save Setup</button><p id="message" class="muted"></p></div>
</div></div>
<script>
const csrf="__CSRF__";let state=__STATUS_JSON__,clearTelegram=false,clearOpenAI=false;const q=id=>document.getElementById(id);
function render(){q("excelEnabled").checked=!!state.excel_enabled;q("excelPath").value=state.excel_workbook_path||state.managed_excel_path||"";q("telegramEnabled").checked=!!state.telegram_enabled;q("botAlias").value=state.telegram_bot_alias||"primary-bot";q("provider").value=state.llm_provider||"none";q("model").value=state.llm_model||"";q("localUrl").value=state.local_llm_base_url||"";q("excelStatus").textContent=state.managed_excel_exists?"Workbook ready":"Workbook not created";q("telegramStatus").textContent=state.telegram_token_configured?"Credential stored; connector pending":"Not configured";q("llmStatus").textContent=(state.llm_provider==="local"||(state.llm_provider==="openai"&&state.openai_key_configured))?"Configuration stored; connector pending":"Not configured";q("overallStatus").textContent=state.setup_complete?"Setup saved":"Setup incomplete"}
async function api(path,body){const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json","X-OSAI-CSRF":csrf},body:JSON.stringify(body||{})});const d=await r.json();if(!r.ok)throw new Error(d.error||"Request failed");return d}
q("createExcel").onclick=async()=>{try{state=await api("/api/setup/excel/init",{});q("message").textContent="Managed workbook ready.";render()}catch(e){q("message").textContent=e.message}};
q("clearTelegram").onclick=()=>{clearTelegram=true;q("telegramToken").value="";q("message").textContent="Stored Telegram token will be cleared on save."};
q("clearOpenAI").onclick=()=>{clearOpenAI=true;q("openaiKey").value="";q("message").textContent="Stored OpenAI key will be cleared on save."};
q("save").onclick=async()=>{const p=q("provider").value;const settings={excel_enabled:q("excelEnabled").checked,excel_workbook_path:q("excelPath").value||null,telegram_enabled:q("telegramEnabled").checked,telegram_bot_alias:q("botAlias").value,llm_provider:p,llm_model:q("model").value||null,local_llm_base_url:p==="local"?(q("localUrl").value||null):null};const b={settings:settings,clear_telegram_token:clearTelegram,clear_openai_api_key:clearOpenAI};if(q("telegramToken").value)b.telegram_bot_token=q("telegramToken").value;if(q("openaiKey").value)b.openai_api_key=q("openaiKey").value;try{state=await api("/api/setup",b);clearTelegram=false;clearOpenAI=false;q("telegramToken").value="";q("openaiKey").value="";q("message").textContent="Setup saved securely.";render()}catch(e){q("message").textContent=e.message}};
render();
</script></body></html>"""