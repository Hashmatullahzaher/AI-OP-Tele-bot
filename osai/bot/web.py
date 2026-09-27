"""Localhost web chat for testing the bot without Telegram.

Binds to 127.0.0.1 only. Every request must carry the per-run token embedded
in the page, and the Host/Origin headers must be this localhost address, so
other websites open in the same browser cannot use the chat.
"""

from __future__ import annotations

import html
import json
import logging
import secrets
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

log = logging.getLogger("osai.bot.web")

Answer = Callable[[str], str]

MAX_BODY_BYTES = 16_384

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OS AI Core — local chat</title>
<style>
  :root { --bg:#f5f6f8; --card:#fff; --text:#1b1f24; --muted:#5b6470; --me:#1f6feb; --me-text:#fff; --line:#dde1e6; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0f1115; --card:#181b21; --text:#e6e8eb; --muted:#9aa3ad; --me:#2f81f7; --me-text:#fff; --line:#2a2f37; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font-family: "Segoe UI", Tahoma, "Noto Naskh Arabic", system-ui, sans-serif; }
  main { max-width: 760px; margin: 0 auto; min-height: 100vh; display:flex; flex-direction:column; padding: 16px; }
  header h1 { font-size: 18px; margin: 4px 0; }
  header p { color: var(--muted); margin: 0 0 12px; font-size: 13px; }
  #log { flex:1; display:flex; flex-direction:column; gap:10px; padding: 8px 0 16px; }
  .msg { max-width: 85%; padding: 10px 14px; border-radius: 14px; white-space: pre-wrap; line-height: 1.6;
         background: var(--card); border: 1px solid var(--line); unicode-bidi: plaintext; }
  .me { align-self: flex-end; background: var(--me); color: var(--me-text); border-color: transparent; }
  .bot { align-self: flex-start; }
  .wait { color: var(--muted); font-style: italic; }
  .examples { display:flex; flex-wrap:wrap; gap:8px; margin-bottom: 10px; }
  .examples button { background: var(--card); color: var(--text); border:1px solid var(--line);
                     border-radius: 999px; padding: 6px 12px; cursor:pointer; font: inherit; font-size: 13px; }
  form { display:flex; gap:8px; position: sticky; bottom: 0; background: var(--bg); padding: 8px 0; }
  input { flex:1; padding: 12px 14px; border-radius: 12px; border:1px solid var(--line);
          background: var(--card); color: var(--text); font: inherit; unicode-bidi: plaintext; }
  button[type=submit] { padding: 12px 18px; border-radius: 12px; border: 0; background: var(--me); color: #fff;
                        font: inherit; cursor: pointer; }
  button:disabled { opacity: .6; cursor: default; }
</style>
</head>
<body>
<main>
  <header>
    <h1>OS AI Core — local test chat</h1>
    <p>Signed in as <b>__USER__</b>. Ask in Dari, Pashto or English. Runs only on this computer.</p>
  </header>
  <div class="examples">
    <button type="button">مجموع مصارف چقدر است؟</button>
    <button type="button">ټول لګښتونه څومره دي؟</button>
    <button type="button">What were total expenses in 2026-09?</button>
  </div>
  <div id="log"></div>
  <form id="f">
    <input id="q" dir="auto" autocomplete="off" placeholder="سؤال خود را بنویسید / Type your question" autofocus>
    <button type="submit" id="send">Send</button>
  </form>
</main>
<script>
const TOKEN = "__TOKEN__";
const log = document.getElementById("log"), q = document.getElementById("q"), send = document.getElementById("send");
function add(text, cls) {
  const d = document.createElement("div"); d.className = "msg " + cls; d.dir = "auto"; d.textContent = text;
  log.appendChild(d); d.scrollIntoView({behavior: "smooth", block: "end"}); return d;
}
async function ask(text) {
  if (!text.trim()) return;
  add(text, "me"); q.value = ""; send.disabled = true;
  const waiting = add("…", "bot wait");
  try {
    const r = await fetch("/api/ask", {method: "POST",
      headers: {"Content-Type": "application/json", "X-OSAI-Token": TOKEN},
      body: JSON.stringify({text})});
    const data = await r.json();
    waiting.textContent = data.reply || data.error || "Error"; waiting.className = "msg bot";
  } catch (e) {
    waiting.textContent = "The local server is not responding. Is the bot still running?"; waiting.className = "msg bot";
  } finally { send.disabled = false; q.focus(); }
}
document.getElementById("f").addEventListener("submit", e => { e.preventDefault(); ask(q.value); });
document.querySelectorAll(".examples button").forEach(b => b.addEventListener("click", () => ask(b.textContent)));
</script>
</body>
</html>
"""


class _Server(HTTPServer):
    # Single-threaded on purpose: one local user, and the SQLite audit store
    # must be used from the thread that opened it.
    answer: Answer
    token: str
    user_label: str


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def _allowed_hosts(self) -> set[str]:
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in self._allowed_hosts()

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._json(403, {"error": "forbidden"})
            return
        if self.path not in {"/", "/index.html"}:
            self._json(404, {"error": "not found"})
            return
        page = PAGE.replace("__TOKEN__", self.server.token).replace("__USER__", html.escape(self.server.user_label))
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        origin = self.headers.get("Origin")
        allowed_origins = {f"http://{host}" for host in self._allowed_hosts()}
        if (
            not self._host_ok()
            or self.path != "/api/ask"
            or (origin is not None and origin not in allowed_origins)
            or not secrets.compare_digest(self.headers.get("X-OSAI-Token", ""), self.server.token)
        ):
            self._json(403, {"error": "forbidden"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 1 or length > MAX_BODY_BYTES:
            self._json(413, {"error": "request too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            text = payload["text"]
            if not isinstance(text, str):
                raise TypeError
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
            self._json(400, {"error": "invalid request"})
            return
        try:
            reply = self.server.answer(text[:4_000])
        except Exception:
            log.exception("web chat answer failed")
            self._json(500, {"error": "internal error"})
            return
        self._json(200, {"reply": reply})

    def log_message(self, format: str, *args: object) -> None:  # questions are not logged
        return


def build_web_server(*, answer: Answer, user_label: str, port: int = 8770) -> _Server:
    if port != 0 and not 1024 <= port <= 65535:
        raise ValueError("web port must be between 1024 and 65535")
    server = _Server(("127.0.0.1", port), _Handler)
    server.answer = answer
    server.token = secrets.token_urlsafe(32)
    server.user_label = user_label
    return server
