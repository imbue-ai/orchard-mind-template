"""The public tracking-pixel service.

This is the ONLY part of Orchard exposed to the open internet (via a Cloudflare
quick tunnel, see tunnel_runner). It deliberately serves just two routes -- the
1x1 tracking GIF and a health check -- and never touches candidate data directly:
a pixel hit is appended to a queue that the private main app drains and records.
So a recipient loading the pixel can, at most, enqueue an open for an opaque
token; there is no access to the pipeline, candidates, or any other data here.

Runs on ORCHARD_PIXEL_PORT (default 8083), bound to localhost; the tunnel is what
makes it reachable.
"""

import datetime
import os

from flask import Flask, Response
from werkzeug.serving import run_simple

from orchard import store

app = Flask("orchard_pixel", static_folder=None)

# A 1x1 transparent GIF (same bytes the main app used to serve).
_PIXEL_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00"
    b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


def _now_iso() -> str:
    return datetime.datetime.now(tz=datetime.timezone.utc).isoformat()


@app.route("/pixel/<token>")
def pixel(token: str) -> Response:
    """Record an open (by queuing it) and return the invisible GIF.

    Always returns the GIF, even for an unknown token, so a broken image never
    shows in the recipient's mail client."""
    store.append_pending_open(token.removesuffix(".gif"), _now_iso())
    response = Response(_PIXEL_GIF, mimetype="image/gif")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@app.route("/health")
def health() -> Response:
    return Response('{"status": "ok"}', mimetype="application/json")


def main() -> None:
    port = int(os.environ.get("ORCHARD_PIXEL_PORT", "8083"))
    run_simple("127.0.0.1", port, app, threaded=True, use_reloader=False, use_debugger=False)
