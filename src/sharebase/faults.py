"""Fault injection switchboard for ShareBase.

The point of this module is to make every branch of the replay error taxonomy
reachable on demand and repeatably, without having to wait for a real outage.

Each fault is tagged with the taxonomy class it is meant to exercise:

  business     a legitimate outcome the calling agent needs to be told about
               ("no such member", "permission denied", "validation failed").
               Not a crash.
  recoverable  a condition automation can deliberately absorb -- dismiss the
               interstitial, wait out the slowness, retry the transient 500,
               re-authenticate after a timeout.
  hard         something automation should stop on and surface with enough
               detail to debug.

Faults are process-global and settable over HTTP so that a replay harness can
arm one, drive the flow, and observe how the automation classifies it.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class FaultSpec:
    key: str
    label: str
    taxonomy: str  # business | recoverable | hard
    description: str
    # How many times the fault fires once armed. None means "every time until
    # disarmed"; a number means it self-disarms after that many firings, which
    # is how genuinely transient conditions behave.
    fires: int | None = None


FAULT_SPECS: tuple[FaultSpec, ...] = (
    FaultSpec(
        key="session_expired",
        label="Expire session on next request",
        taxonomy="recoverable",
        description="Next authenticated request is bounced to the login screen "
                    "with a session-expiry notice.",
        fires=1,
    ),
    FaultSpec(
        key="interstitial_notice",
        label="Inject system maintenance notice",
        taxonomy="recoverable",
        description="A full-page notice is served in place of the next console "
                    "page and must be acknowledged before continuing.",
        fires=1,
    ),
    FaultSpec(
        key="slow_response",
        label="Delay responses",
        taxonomy="recoverable",
        description="Adds the configured delay to console responses, pushing "
                    "naive fixed waits past their budget.",
    ),
    FaultSpec(
        key="transient_error",
        label="Transient server error",
        taxonomy="recoverable",
        description="Returns HTTP 500 for the configured number of requests, "
                    "then serves normally.",
        fires=2,
    ),
    FaultSpec(
        key="unexpected_confirm",
        label="Extra confirmation step",
        taxonomy="recoverable",
        description="Inserts an unannounced 'are you sure' page into the "
                    "sub-account flow that the recorded flow did not expect.",
        fires=1,
    ),
    FaultSpec(
        key="spurious_validation",
        label="Force a validation error",
        taxonomy="business",
        description="Rejects the next sub-account submission with a field-level "
                    "validation error regardless of the values supplied.",
        fires=1,
    ),
    FaultSpec(
        key="permission_denied",
        label="Deny servicing permission",
        taxonomy="business",
        description="Treats every member as servicing-restricted, so the "
                    "open-sub-account action is refused.",
    ),
    FaultSpec(
        key="search_unavailable",
        label="Search subsystem down",
        taxonomy="hard",
        description="Member search returns the legacy application error page.",
    ),
    FaultSpec(
        key="hard_error",
        label="Persistent server error",
        taxonomy="hard",
        description="Member detail returns HTTP 500 on every request until "
                    "disarmed.",
    ),
)

FAULTS_BY_KEY: dict[str, FaultSpec] = {spec.key: spec for spec in FAULT_SPECS}


@dataclass
class FaultState:
    enabled: bool = False
    remaining: int | None = None


class FaultBoard:
    """Thread-safe holder for armed faults and their tunables."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, FaultState] = {}
        self.delay_seconds: float = 3.0
        self.reset()

    # -- state management -------------------------------------------------

    def reset(self) -> None:
        with self._lock:
            self._state = {spec.key: FaultState() for spec in FAULT_SPECS}
            self.delay_seconds = 3.0

    def arm(self, key: str, enabled: bool = True, fires: int | None = None) -> None:
        spec = FAULTS_BY_KEY.get(key)
        if spec is None:
            raise KeyError(f"unknown fault: {key}")
        with self._lock:
            if not enabled:
                self._state[key] = FaultState()
                return
            remaining = fires if fires is not None else spec.fires
            self._state[key] = FaultState(enabled=True, remaining=remaining)

    def is_armed(self, key: str) -> bool:
        with self._lock:
            state = self._state.get(key)
            return bool(state and state.enabled)

    def consume(self, key: str) -> bool:
        """Report whether the fault fires for this request, and account for it.

        A fault with a finite firing budget disarms itself once spent, which is
        what makes "retry and it works" a real behaviour rather than a mock.
        """
        with self._lock:
            state = self._state.get(key)
            if state is None or not state.enabled:
                return False
            if state.remaining is None:
                return True
            if state.remaining <= 0:
                self._state[key] = FaultState()
                return False
            state.remaining -= 1
            if state.remaining <= 0:
                self._state[key] = FaultState()
            return True

    # -- introspection ----------------------------------------------------

    def snapshot(self) -> dict[str, dict[str, object]]:
        with self._lock:
            return {
                key: {
                    "enabled": state.enabled,
                    "remaining": state.remaining,
                    "taxonomy": FAULTS_BY_KEY[key].taxonomy,
                }
                for key, state in self._state.items()
            }

    def armed_keys(self) -> list[str]:
        with self._lock:
            return [k for k, s in self._state.items() if s.enabled]

    def apply(self, payload: dict[str, object]) -> list[str]:
        """Arm or disarm faults from a request payload.

        Accepts ``{"hard_error": true}``, ``{"transient_error": 3}`` to override
        the firing budget, and ``{"delay_seconds": 8}`` for the tunable.
        Returns the keys that were changed.
        """
        changed: list[str] = []
        for key, value in payload.items():
            if key == "delay_seconds":
                with self._lock:
                    self.delay_seconds = max(0.0, float(value))  # type: ignore[arg-type]
                changed.append(key)
                continue
            if key not in FAULTS_BY_KEY:
                continue
            if isinstance(value, bool):
                self.arm(key, enabled=value)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                count = int(value)
                self.arm(key, enabled=count > 0, fires=count if count > 0 else None)
            elif isinstance(value, str):
                truthy = value.strip().lower() in {"1", "true", "on", "yes"}
                self.arm(key, enabled=truthy)
            else:
                continue
            changed.append(key)
        return changed


board = FaultBoard()


def specs_by_taxonomy() -> dict[str, list[FaultSpec]]:
    grouped: dict[str, list[FaultSpec]] = {"business": [], "recoverable": [], "hard": []}
    for spec in FAULT_SPECS:
        grouped[spec.taxonomy].append(spec)
    return grouped
