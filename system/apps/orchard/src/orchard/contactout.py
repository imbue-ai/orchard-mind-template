"""Look up a candidate's email from their LinkedIn URL via the ContactOut API.

ContactOut is not a latchkey-supported service, so we call its HTTP API directly
with the user's own API token (stored server-side; see ``store.load_contactout_token``).
The one endpoint used here, ``GET /v1/people/linkedin?profile=<url>``, returns the
emails associated with a public LinkedIn profile URL. The token travels in a
``token`` header (not a bearer), which is what ContactOut expects.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from orchard.errors import EnrichError

logger = logging.getLogger("orchard.contactout")

_ENDPOINT = "https://api.contactout.com/v1/people/linkedin"
_TIMEOUT_SECONDS = 30

# The HTTP fetch is injectable so tests never hit the network; production uses
# _urllib_fetch. It takes the full URL and the API token and returns parsed JSON.
Fetcher = Callable[[str, str], dict[str, Any]]


def _http_error_message(code: int, detail: str) -> str:
    if code == 401:
        return "ContactOut rejected the API token -- check it in Settings."
    if code == 403:
        return "ContactOut denied the request -- you may be out of API credits, or your plan lacks API access."
    if code == 404:
        return "ContactOut has no profile for that LinkedIn URL."
    return f"ContactOut error (HTTP {code}): {detail}"


def _urllib_fetch(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Content-Type": "application/json", "Accept": "application/json", "token": token},
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise EnrichError(_http_error_message(exc.code, detail)) from exc
    except urllib.error.URLError as exc:
        raise EnrichError(f"Couldn't reach ContactOut: {exc.reason}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EnrichError(f"ContactOut returned a non-JSON response: {raw[:200]}") from exc
    if not isinstance(parsed, dict):
        raise EnrichError(f"Unexpected ContactOut response: {raw[:200]}")
    return parsed


def lookup_emails(linkedin_url: str, token: str, fetch: Fetcher = _urllib_fetch) -> dict[str, Any]:
    """Return the emails ContactOut has for a LinkedIn profile URL.

    Result shape::

        {"work": [...], "personal": [...], "all": [...], "profile": {...raw...}}

    ``all`` is ContactOut's own combined/ranked list (with work+personal as a
    fallback when the API only splits them). ``profile`` is the raw payload,
    preserved on the candidate so a later change needs no refetch. Raises
    ``EnrichError`` on any precondition or API failure.
    """
    if not token:
        raise EnrichError("No ContactOut API token set -- add one in Settings.")
    if not linkedin_url:
        raise EnrichError("This candidate has no LinkedIn URL to look up.")
    query = urllib.parse.urlencode({"profile": linkedin_url, "email_type": "personal,work"})
    payload = fetch(f"{_ENDPOINT}?{query}", token)
    profile = payload.get("profile") or {}
    work = [e for e in profile.get("work_email", []) if e]
    personal = [e for e in profile.get("personal_email", []) if e]
    combined = [e for e in profile.get("email", []) if e]
    everything = combined or (work + personal)
    return {"work": work, "personal": personal, "all": everything, "profile": profile}
