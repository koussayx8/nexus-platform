"""The Flight Recorder client (spec §14): the Writer, the write-ahead helper and the Audit-Id wrapper.

The operator imports this package only; nothing else in the operator talks to the database.
"""

from .act import (
    ActionRecord,
    ActRecorder,
    PostgresActRecorder,
    ReconnectingRecorder,
    UnavailableRecorder,
    connect_act,
)
from .actions import ActionOutcome, execute_recorded
from .audit import ApiResult, AuditedClient
from .writer import (
    ActionIntent,
    PostgresRecorder,
    Recorder,
    RecorderUnavailable,
    connect,
)

__all__ = [
    "ActRecorder",
    "ActionIntent",
    "ActionOutcome",
    "ActionRecord",
    "ApiResult",
    "AuditedClient",
    "PostgresActRecorder",
    "PostgresRecorder",
    "ReconnectingRecorder",
    "Recorder",
    "RecorderUnavailable",
    "UnavailableRecorder",
    "connect",
    "connect_act",
    "execute_recorded",
]
