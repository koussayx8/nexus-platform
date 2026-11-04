"""Errors the client raises; nexusctl maps each to one exit code (spec §16A)."""


class NexusClientError(Exception):
    """Base class."""


class NotFound(NexusClientError):
    """The Incident does not exist (HTTP 404)."""


class Denied(NexusClientError):
    """RBAC or admission refused the request (HTTP 403, or 422 from a webhook or CEL rule)."""

    def __init__(self, message: str, audit_id: str | None = None) -> None:
        super().__init__(message)
        self.audit_id = audit_id


class Conflict(NexusClientError):
    """The object changed under the client (HTTP 409, a stale resourceVersion)."""


class Unreachable(NexusClientError):
    """A data source could not be reached."""


class StaleProposal(NexusClientError):
    """The proposal hash changed since it was displayed."""


class WrongPhase(NexusClientError):
    """The Incident is not in AwaitingApproval, has no proposal, or is already decided."""
