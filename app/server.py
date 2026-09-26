"""Launch with: python -m app.server. Python standard library only."""
from __future__ import annotations
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import json
import mimetypes
import os
from app.core import CoreDemo

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
CORE = CoreDemo()


class Handler(BaseHTTPRequestHandler):
    def json_reply(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/status": return self.json_reply(CORE.status())
        if path == "/api/sources": return self.json_reply(CORE.sources())
        if path == "/api/audit": return self.json_reply({"events": CORE.events()})
        if path.startswith("/api/report/") and path.endswith(".csv"):
            kind = path[len("/api/report/"):-4]
            try: report = CORE.report(kind)
            except ValueError: return self.json_reply({"error": "Unknown report"}, 404)
            data = report.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="osai-demo-{kind}.csv"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(data)
        if path == "/": path = "/index.html"
        filepath = (WEB / path.lstrip("/")).resolve()
        if not filepath.is_relative_to(WEB.resolve()) or not filepath.is_file():
            return self.json_reply({"error": "Not found"}, 404)
        data = filepath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(filepath.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if urlparse(self.path).path != "/api/chat": return self.json_reply({"error": "Not found"}, 404)
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n <= 0 or n > 8192: return self.json_reply({"error": "Invalid body length"}, 400)
            payload = json.loads(self.rfile.read(n))
            if not isinstance(payload, dict): return self.json_reply({"error": "JSON object required"}, 400)
            result = CORE.ask(payload.get("message", ""), tenant=payload.get("tenant", "demo-company"))
            return self.json_reply(result)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self.json_reply({"error": str(exc)}, 400)
        except PermissionError:
            return self.json_reply({"error": "Demo tenant not found"}, 403)

    def log_message(self, *_): pass


def main():
    host = os.environ.get("OSAI_HOST", "127.0.0.1")
    port = int(os.environ.get("OSAI_PORT", "8765"))
    with ThreadingHTTPServer((host, port), Handler) as server:
        print(f"OS AI Core DEMO ONLY at http://{host}:{server.server_port}", flush=True)
        server.serve_forever()


if __name__ == "__main__": main()
