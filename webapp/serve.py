#!/usr/bin/env python3
"""HTTP server for the OptiMIMO webapp with COOP/COEP headers and REW API proxy.

SharedArrayBuffer requires cross-origin isolation:
  Cross-Origin-Opener-Policy: same-origin
  Cross-Origin-Embedder-Policy: require-corp

The COEP header blocks cross-origin fetches to REW's API (which lacks CORP).
We proxy /rew/* to REW's API to make it same-origin.

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
                self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(f'{{"error": "REW proxy error: {e}"}}'.encode())


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8878
    server = HTTPServer(("127.0.0.1", port), COOPCOEPHandler)
    print(f"Serving on http://127.0.0.1:{port} (COOP/COEP enabled)")
    print(f"Proxying /rew/* to http://{REW_HOST}:{REW_PORT}/*")
    server.serve_forever()
