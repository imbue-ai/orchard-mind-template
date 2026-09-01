"""Expose the public tracking-pixel service over a Cloudflare quick tunnel.

Runs ``cloudflared tunnel --url http://localhost:<pixel-port>``, which returns a
public ``*.trycloudflare.com`` URL with no login gate (unlike the workspace's
Access-gated share) -- exactly what an email tracking pixel needs so a recipient's
client can load it silently. The discovered URL is written to the app's
``public_base_url`` setting, so mail sent afterwards embeds a pixel that points at
it.

The URL is regenerated whenever the tunnel restarts (e.g. a workspace restart), so
newly sent mail tracks under the fresh URL; pixels in already-sent mail stop
recording once the old URL is gone. Only the pixel service is exposed -- never the
main app or candidate data.
"""

import logging
import os
import re
import signal
import subprocess
import sys

from orchard import store

logger = logging.getLogger("orchard.tunnel")

_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    port = os.environ.get("ORCHARD_PIXEL_PORT", "8083")
    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://localhost:{port}", "--no-autoupdate"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    def _stop(_signum: int, _frame: object) -> None:
        if proc.poll() is None:
            proc.terminate()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    logger.info("starting cloudflared quick tunnel to localhost:%s", port)
    url_set = False
    assert proc.stdout is not None
    for line in proc.stdout:
        logger.info("cloudflared: %s", line.rstrip("\n"))
        if not url_set:
            match = _URL_RE.search(line)
            if match:
                url = match.group(0)
                store.save_settings({"public_base_url": url})
                url_set = True
                logger.info("public tracking URL set to %s", url)

    code = proc.wait()
    logger.info("cloudflared exited with %s; exiting so it is restarted", code)
    sys.exit(code or 1)
