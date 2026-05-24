"""Vercel serverless function: /health and /api/health endpoint.

Returns a JSON health response compatible with the OpenSRE RemoteAgentClient
and the shared poll_deployment_health flow.
"""

from http.server import BaseHTTPRequestHandler
import json


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        body = json.dumps({
            "status": "ok",
            "service": "opensre",
            "deployment": "vercel",
            "ok": True,
        })
        self.wfile.write(body.encode())
