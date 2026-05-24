"""Vercel serverless function: /ok and /api/ok endpoint.

Returns a minimal JSON response for the RemoteAgentClient.health() check.
"""

from http.server import BaseHTTPRequestHandler
import json


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        body = json.dumps({"ok": True, "service": "opensre", "deployment": "vercel"})
        self.wfile.write(body.encode())
