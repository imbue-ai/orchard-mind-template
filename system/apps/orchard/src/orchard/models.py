"""Shared data models for candidate outreach.

A candidate's pipeline ``status`` is never stored directly -- it is derived from
``sent_at`` / ``opened_at`` so the two can never drift out of sync. ``raw``
preserves whatever a later lookup (e.g. LinkedIn Recruiter) returned, so the
origin of a candidate's details is always recoverable.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["not_contacted", "sent", "opened"]

# The manual pipeline stage the recruiter sets by hand (an outreach can happen off
# -platform, e.g. a LinkedIn InMail). Ordered from earliest to latest, with a
# closed "not a fit" state last. Kept as a tuple so the UI and validation share one
# source of truth.
STAGES: tuple[str, ...] = ("not_contacted", "reached_out", "replied", "not_interested")
STAGE_LABELS: dict[str, str] = {
    "not_contacted": "Not contacted",
    "reached_out": "Reached out",
    "replied": "Replied",
    "not_interested": "Not a fit",
}

# The three sending identities. ``key`` is the stable id the UI and stored
# candidates reference; ``sender_name`` fills the {{sender_name}} token.
InboxKey = Literal["you", "josh", "kj"]


class Inbox(BaseModel):
    """One sending identity (the recruiter's own inbox or a founder alias)."""

    model_config = ConfigDict(frozen=True)

    key: InboxKey
    name: str
    address: str
    sender_name: str


class Template(BaseModel):
    """A reusable email template with personalization tokens in its text."""

    id: str
    name: str
    subject: str
    body: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class Notification(BaseModel):
    """A surfaced engagement event -- currently 'a candidate opened your email'."""

    id: str
    candidate_id: str
    candidate_name: str
    inbox: InboxKey | None
    opened_at: str
    # How many times this candidate has opened (1 = first open, >1 = a re-open).
    open_count: int = 1
    seen: bool = False

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class Candidate(BaseModel):
    """A person in the outreach pipeline.

    Everything but ``name`` is optional -- a candidate can be added with just a
    name and enriched later. ``status`` is computed, not stored.
    """

    id: str
    name: str
    company: str = ""
    role: str = ""
    email: str = ""
    linkedin: str = ""
    notes: str = ""
    # A free-form custom line the recruiter writes for this candidate; fills the
    # {{personalization}} token so a template can carry a bespoke sentence per person.
    personalization: str = ""
    # Manually set pipeline stage (one of STAGES). Empty means "not set explicitly",
    # in which case ``pipeline_stage`` derives it from whether an email was sent.
    stage: str = ""
    # How the outreach happened (e.g. "LinkedIn InMail", "Email", "Referral") --
    # free text so any channel can be recorded, shown next to the stage.
    channel: str = ""
    created_at: str
    sent_at: str | None = None
    opened_at: str | None = None
    # Timestamp of the most recent open, used to debounce repeat-open notifications
    # so automated image prefetches don't spam (opened_at stays the FIRST open).
    last_open_at: str | None = None
    # When a reply from this candidate was detected in the connected inbox.
    replied_at: str | None = None
    from_inbox: InboxKey | None = None
    last_template_id: str | None = None
    # Opaque id embedded in the tracking pixel of the last email sent to this
    # candidate; a pixel hit carrying it is what flips them to "opened".
    track_token: str | None = None
    open_count: int = 0
    # Where the details came from (e.g. a LinkedIn Recruiter lookup payload),
    # preserved so a later processing change needs no refetch.
    raw: dict[str, Any] = Field(default_factory=dict)
    # History of emails actually sent to this candidate, newest last. Each entry:
    # {inbox_key, subject, body, sent_at, thread_id, message_id} -- used to show what
    # went out and to thread a follow-up onto the same conversation.
    sends: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def status(self) -> Status:
        if self.opened_at:
            return "opened"
        if self.sent_at:
            return "sent"
        return "not_contacted"

    @property
    def pipeline_stage(self) -> str:
        """The stage to show: the manual one if set, else derived from whether an
        email has been sent (so candidates predating manual stages still read right)."""
        if self.stage in STAGES:
            return self.stage
        return "reached_out" if self.sent_at else "not_contacted"

    def to_dict(self) -> dict[str, Any]:
        data = self.model_dump()
        data["status"] = self.status
        data["stage"] = self.pipeline_stage
        return data
