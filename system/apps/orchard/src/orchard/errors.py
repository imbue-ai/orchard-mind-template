"""Error types for the candidate outreach service."""


class CandidateOutreachError(Exception):
    """Base error for the candidate outreach service."""


class NotFoundError(CandidateOutreachError):
    """Raised when a candidate or template id does not exist."""


class SendError(CandidateOutreachError):
    """Raised when an email could not be sent (e.g. the mail connection is down)."""


class EnrichError(CandidateOutreachError):
    """Raised when a ContactOut lookup could not complete (bad token, no credits,
    no profile, or the connection failed)."""


class DraftError(CandidateOutreachError):
    """Raised when an AI voice-draft could not be produced (no samples, or the
    ``claude -p`` call failed or returned an unusable result)."""
