#!/usr/bin/env python3
"""HTTP server for the OptiMIMO webapp with COOP/COEP headers and REW API proxy.

Two roles:

1. Local serving (default): serves the webapp with cross-origin isolation
   (SharedArrayBuffer needs it):
     Cross-Origin-Opener-Policy: same-origin
     Cross-Origin-Embedder-Policy: require-corp
   The COEP header blocks cross-origin fetches to REW's API (which lacks
   CORP), so /rew/* is proxied to REW to make it same-origin.

2. Companion proxy for the hosted app (https://optimimo.app): the hosted
   page cannot reach REW directly — browsers block public→loopback
   requests without a Private Network Access preflight. Running this
   locally and pointing the webapp's "REW proxy URL" at
   http://127.0.0.1:<port> lets the hosted page import from REW: we
   answer the PNA preflight (Access-Control-Allow-Private-Network) and
   add CORS headers to proxy responses.

Usage: python3 serve.py [port]
"""

import sys
import urllib.request
import urllib.error
from http.server import HTTPServer, SimpleHTTPRequestHandler

REW_HOST = "127.0.0.1"
REW_PORT = 4735


class COOPCOEPHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        super().end_headers()

    def _cors_headers(self):
        # No credentials involved, so "*" is valid everywhere.
        self.send_header("Access-Control-Allow-Origin", "*")

    def do_OPTIONS(self):
        # CORS / Private Network Access preflight. Chrome requires
        # Access-Control-Allow-Private-Network when a public site fetches
        # from loopback; without it the hosted app's REW import is blocked.
        self.send_response(204)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        if self.headers.get("Access-Control-Request-Headers"):
            self.send_header(
                "Access-Control-Allow-Headers",
                self.headers["Access-Control-Request-Headers"],
            )
        else:
            self.send_header("Access-Control-Allow-Headers", "*")
        if self.headers.get("Access-Control-Request-Private-Network") == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        # Proxy /rew/* to REW's API
        if self.path.startswith("/rew/"):
            self.proxy_rew()
        else:
            super().do_GET()

    def proxy_rew(self):
        # Strip /rew prefix and forward to REW
        rew_path = self.path[4:]  # Remove "/rew"
        url = f"http://{REW_HOST}:{REW_PORT}{rew_path}"

        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                self.send_response(resp.status)
                self._cors_headers()
                self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            self.send_response(502)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(f'{{"error": "REW proxy error: {e}"}}'.encode())


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8878
    server = HTTPServer(("127.0.0.1", port), COOPCOEPHandler)
    print(f"Serving on http://127.0.0.1:{port} (COOP/COEP enabled)")
    print(f"Proxying /rew/* to http://{REW_HOST}:{REW_PORT}/* (CORS + Private Network Access enabled)")
    print(f"Hosted app: set REW proxy URL to http://127.0.0.1:{port}")
    server.serve_forever()
