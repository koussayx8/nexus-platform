"""Test seams. Production leaves both as None; only operator/tests/ sets them.

before_intake(name)          awaited at the start of the intake handler
before_status_write(name)    awaited by the loop between its read and its status write
The envtest P5 race uses them to force a stale resourceVersion (operator/tests/envtest/).
"""

from collections.abc import Awaitable, Callable

before_intake: Callable[[str], Awaitable[None]] | None = None
before_status_write: Callable[[str], Awaitable[None]] | None = None
