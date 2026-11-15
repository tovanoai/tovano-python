"""python examples/webhook_server.py — receive finished runs (standard library only).

Set TOVANO_WEBHOOK_SECRET (App → Settings → Webhooks) and pass webhook_url when you create a run.
"""

import json
import os
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from tovano import WebhookVerificationError, webhooks

SECRET = os.environ.get("TOVANO_WEBHOOK_SECRET", "whsec_example")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        raw = self.rfile.read(int(self.headers["content-length"]))  # verify the raw bytes
        try:
            run = webhooks.construct_event(raw, self.headers.get("tovano-signature"), SECRET)
        except WebhookVerificationError as e:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(str(e).encode())
            return
        print(f"{run.id} {run.status}, charged {run.charged_micro} micro-dollars")
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


server = HTTPServer(("127.0.0.1", 8788), Handler)
print("Listening on http://127.0.0.1:8788")

if "TOVANO_WEBHOOK_SECRET" in os.environ:
    server.serve_forever()
else:
    # Without a secret: send ourselves a signed webhook the way Tovano does.
    threading.Thread(target=server.serve_forever, daemon=True).start()
    body = json.dumps({"id": "run_example", "object": "run", "status": "completed", "charged_micro": 96000})
    req = urllib.request.Request(
        "http://127.0.0.1:8788",
        data=body.encode(),
        headers={"content-type": "application/json", "tovano-signature": webhooks.sign(body, SECRET)},
    )
    print("Test delivery answered", urllib.request.urlopen(req).status)
    server.shutdown()
