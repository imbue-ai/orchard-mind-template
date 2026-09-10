"""Send mail through the Gmail API via latchkey.

Sending from the recruiter's own inbox or a founder alias uses the same call:
``users/me/messages/send`` with a raw RFC-822 message whose ``From`` header is
the chosen address. For an alias to be accepted, that address must be a verified
"Send mail as" identity on the connected Google account -- ``list_send_as`` lets
the UI check which of the three inboxes are ready before the recruiter relies on
them.
"""

import logging
import subprocess
import urllib.parse
from typing import Any

from orchard import latchkey_client
from orchard.errors import SendError

logger = logging.getLogger("orchard.gmail")

_API = "https://gmail.googleapis.com/gmail/v1/users/me"

# The subprocess runner is threaded through so tests can drive these without a
# live mail connection; production uses subprocess.run.
Runner = latchkey_client.Runner


def get_account_email(runner: Runner = subprocess.run) -> str:
    """The address of the connected mailbox (used to fill the 'you' inbox)."""
    profile = latchkey_client.get_json(f"{_API}/profile", runner)
    email = profile.get("emailAddress")
    if not email:
        raise SendError(f"Gmail profile missing emailAddress: {profile}")
    return str(email)


def list_send_as(runner: Runner = subprocess.run) -> list[str]:
    """Every address the connected account is allowed to send mail as."""
    data = latchkey_client.get_json(f"{_API}/settings/sendAs", runner)
    return [row["sendAsEmail"] for row in data.get("sendAs", []) if row.get("sendAsEmail")]


def send_raw(raw_message: str, thread_id: str | None = None, runner: Runner = subprocess.run) -> dict[str, str]:
    """Send a base64url RFC-822 message; return ``{"id", "thread_id"}``.

    When ``thread_id`` is given, Gmail files the message into that existing thread
    (the follow-up case), so it shows up in the same conversation."""
    body: dict[str, Any] = {"raw": raw_message}
    if thread_id:
        body["threadId"] = thread_id
    result: dict[str, Any] = latchkey_client.post_json(f"{_API}/messages/send", body, runner)
    if "error" in result:
        raise SendError(f"Gmail rejected the message: {result['error']}")
    message_id = result.get("id")
    if not message_id:
        raise SendError(f"Gmail send returned no message id: {result}")
    return {"id": str(message_id), "thread_id": str(result.get("threadId") or "")}


def list_sent_to(email: str, runner: Runner = subprocess.run, limit: int = 10) -> list[dict[str, Any]]:
    """Messages the connected account has sent TO ``email`` (its Sent mail).

    Returns newest-first dicts {gmail_id, thread_id, subject, from_addr, snippet,
    date_ms} -- used to fold in follow-ups the recruiter sent from their own client.
    Raises ``SendError`` if the mailbox can't be read."""
    query = urllib.parse.quote(f"in:sent to:{email}")
    listing = latchkey_client.get_json(f"{_API}/messages?q={query}&maxResults={limit}", runner)
    if "error" in listing:
        raise SendError(f"Couldn't read sent mail: {listing['error']}")
    out: list[dict[str, Any]] = []
    for stub in listing.get("messages") or []:
        headers_q = "metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Date"
        msg = latchkey_client.get_json(
            f"{_API}/messages/{stub['id']}?format=metadata&{headers_q}", runner
        )
        if "error" in msg:
            continue
        headers = {str(h.get("name", "")).lower(): str(h.get("value", "")) for h in (msg.get("payload") or {}).get("headers", [])}
        out.append({
            "gmail_id": str(msg.get("id", "")),
            "thread_id": str(msg.get("threadId", "")),
            "subject": headers.get("subject", ""),
            "from_addr": headers.get("from", ""),
            "snippet": str(msg.get("snippet", "")),
            "date_ms": int(msg.get("internalDate", "0") or 0),
        })
    return out


def get_rfc_message_id(gmail_id: str, runner: Runner = subprocess.run) -> str:
    """The RFC-822 ``Message-ID`` header of a sent message (for threading a reply).

    Best-effort: returns "" if it can't be read. Gmail threads on ``threadId``
    regardless; this header makes the recipient's client thread it too."""
    data = latchkey_client.get_json(
        f"{_API}/messages/{gmail_id}?format=metadata&metadataHeaders=Message-ID", runner
    )
    if "error" in data:
        raise SendError(f"Couldn't read the sent message: {data['error']}")
    headers = (data.get("payload") or {}).get("headers") or []
    for header in headers:
        if str(header.get("name", "")).lower() == "message-id":
            return str(header.get("value", ""))
    return ""


def find_latest_from(email: str, runner: Runner = subprocess.run) -> dict[str, Any] | None:
    """The newest message *from* ``email`` in the connected mailbox.

    Returns ``{"date_ms": int, "snippet": str}`` for the most recent message that
    address sent us, or ``None`` if there are none. Used to detect a candidate's
    reply (a message from them that arrived after we reached out). Raises
    ``SendError`` when the mail connection is down or the read is not permitted.
    """
    query = urllib.parse.quote(f"from:{email}")
    listing = latchkey_client.get_json(f"{_API}/messages?q={query}&maxResults=1", runner)
    if "error" in listing:
        raise SendError(f"Couldn't read mail: {listing['error']}")
    messages = listing.get("messages") or []
    if not messages:
        return None
    message = latchkey_client.get_json(f"{_API}/messages/{messages[0]['id']}?format=metadata", runner)
    if "error" in message:
        raise SendError(f"Couldn't read mail: {message['error']}")
    return {"date_ms": int(message.get("internalDate", "0") or 0), "snippet": str(message.get("snippet", ""))}
