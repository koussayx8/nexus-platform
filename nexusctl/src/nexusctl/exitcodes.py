"""One meaning per exit code across every command (spec §16A)."""

OK = 0
ERROR = 1
DENIED = 2  # RBAC or admission refused; the policy message is printed
NOT_FOUND = 3
STALE = 4  # the proposal hash changed since display
WRONG_PHASE = 5  # wrong phase, or already decided
DEGRADED = 6  # status: a component not ready, or the Kill Switch halted (not built yet)
CONFLICT = 7  # request: the target already has an open incident (not built yet)
MISMATCH = 8  # audit: reconciliation mismatch (not built yet)
UNREACHABLE = 9  # a required data source is unreachable
