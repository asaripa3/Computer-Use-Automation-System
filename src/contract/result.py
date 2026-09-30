"""What a replay hands back to the caller.

§7 names "the artifact schema and replay contract" as the two central pieces.
This is the second one, and it is defined here rather than inside the engine
for the same reason the capability schema was written before the recorder: the
shape a caller depends on should be designed for the caller, not fall out of
whatever the engine found convenient to return.

The contract turns on one distinction, which §3.3 makes and the glossary calls
the most common design mistake in this problem:

    success             the flow completed and the declared outputs were read
    business_outcome    the flow reached a known, legitimate ending that is
                        not success -- "no such member", "permission denied"
    failure             something is broken and needs a person to look

A caller handles the first two. Only the third is an incident. They are
separate fields rather than one status string with a code bolted on, so that
code reading a result cannot accidentally treat a denial as an outage.

Two further things every result carries, both of which exist because a run
that cannot be explained afterwards is not much use:

**A step-by-step report**, including *which rung of the locator ladder
resolved each target*. That is the drift signal: a run that succeeded on a
less-trusted candidate than the one recorded is telling you the surface moved,
before that movement becomes a failure.

**A failure that says what was expected and what was observed**, at which
step. "Step 10 expected the review page and observed a field validation
banner" is debuggable; "replay failed" is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

SUCCESS = "success"
BUSINESS_OUTCOME = "business_outcome"
FAILURE = "failure"
STATUSES = (SUCCESS, BUSINESS_OUTCOME, FAILURE)

# Why a run stopped. Each maps to a different thing a person would do next,
# which is the only reason to distinguish them.
FAILURE_KINDS = (
    "target_not_found",      # no ladder candidate resolved
    "target_ambiguous",      # a candidate matched more than one control
    "checkpoint_failed",     # the step ran but the page is not what was expected
    "timeout",               # an expectation never came true
    "application_error",     # the application itself failed
    "policy_refused",        # a guardrail stopped the run
    "escalated",             # handed to a human and not resumed
    "contract_violation",    # inputs or outputs did not satisfy the contract
    "recovery_exhausted",    # a known condition kept recurring past its budget
    # An irreversible step a person reported completing, which the page can no
    # longer confirm. Deliberately separate from "it failed": the two demand
    # opposite next actions. A failure should be retried; this must not be,
    # until someone has checked, because retrying may do it twice.
    "effect_unverified",
)

STEP_STATUSES = (
    "ok",
    "recovered",     # a declared recovery fired and the step still completed
    "handed_over",   # a person carried this step out; its checkpoint still ran
    "failed",
    "skipped",
)


@dataclass(frozen=True)
class DriftSignal:
    """A target that resolved on a less-trusted rung than was recorded.

    Not an error. The run worked. It is the early warning that the surface has
    changed under an artifact, reported while there is still time to act on it.
    """

    step_index: int
    target: str
    recorded_best: str
    resolved_by: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_index": self.step_index, "target": self.target,
            "recorded_best": self.recorded_best, "resolved_by": self.resolved_by,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class StepReport:
    index: int
    action: str
    description: str
    status: str
    duration_ms: int = 0
    resolved_by: str | None = None      # which ladder rung found the target
    attempts: int = 1
    recoveries_applied: tuple[str, ...] = ()
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "index": self.index, "action": self.action,
            "description": self.description, "status": self.status,
            "duration_ms": self.duration_ms, "attempts": self.attempts,
        }
        if self.resolved_by:
            out["resolved_by"] = self.resolved_by
        if self.recoveries_applied:
            out["recoveries_applied"] = list(self.recoveries_applied)
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass(frozen=True)
class Failure:
    """Enough to debug without re-running.

    `expected` and `observed` are both required. A failure that reports only
    what went wrong, and not what the run was looking for at the time, sends
    whoever reads it back to the artifact to reconstruct the question.
    """

    kind: str
    expected: str
    observed: str
    step_index: int | None = None
    step_description: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "step_index": self.step_index,
            "step_description": self.step_description,
            "expected": self.expected, "observed": self.observed,
            "detail": self.detail,
        }

    def summary(self) -> str:
        where = f"step {self.step_index}" if self.step_index is not None else "the run"
        return f"{where} expected {self.expected}, observed {self.observed}"


@dataclass(frozen=True)
class OutcomeReport:
    """A known ending that is not success."""

    code: str
    kind: str            # business | hard
    description: str
    detected_at_step: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code, "kind": self.kind,
            "description": self.description,
            "detected_at_step": self.detected_at_step,
        }


@dataclass(frozen=True)
class ReplayResult:
    capability_ref: str
    status: str
    outputs: dict[str, Any] = field(default_factory=dict)
    outcome: OutcomeReport | None = None
    failure: Failure | None = None
    steps: tuple[StepReport, ...] = ()
    drift: tuple[DriftSignal, ...] = ()
    tenant: str | None = None
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0
    evidence_dir: str | None = None
    escalation_id: str | None = None
    # Every transfer of control during this run: what was asked, what the
    # person decided, and what they did while they held the session.
    handoffs: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"{self.status!r} is not one of {list(STATUSES)}")
        # The three statuses are mutually exclusive and each carries exactly
        # the field it is about. Enforcing that here is what stops a caller
        # having to defensively check for a failure on a successful result.
        if self.status == BUSINESS_OUTCOME and self.outcome is None:
            raise ValueError("a business_outcome result must carry the outcome")
        if self.status == FAILURE and self.failure is None:
            raise ValueError("a failure result must carry the failure")
        if self.status == SUCCESS and (self.failure or self.outcome):
            raise ValueError("a success result carries neither a failure nor an outcome")

    # -- the caller's view -------------------------------------------------

    @property
    def ok(self) -> bool:
        return self.status == SUCCESS

    @property
    def may_be_retried_safely(self) -> bool:
        """Whether re-running this invocation is safe.

        False when an irreversible step may or may not have taken effect. A
        caller that retries on any failure would, in that one case, do the
        thing twice.
        """
        return not (
            self.failure is not None and self.failure.kind == "effect_unverified"
        )

    @property
    def needs_attention(self) -> bool:
        """True only for things a person should look at.

        A business outcome is an answer, so it is deliberately *not* included.
        This property is the whole point of the contract: if it were computed
        from a status code, "no such member" would page somebody.
        """
        return self.status == FAILURE

    @property
    def has_drifted(self) -> bool:
        return bool(self.drift)

    @property
    def involved_a_human(self) -> bool:
        return bool(self.handoffs)

    def summary(self) -> str:
        if self.status == SUCCESS:
            names = ", ".join(self.outputs) or "no outputs"
            return f"{self.capability_ref}: success ({names})"
        if self.status == BUSINESS_OUTCOME:
            return f"{self.capability_ref}: {self.outcome.code} -- {self.outcome.description}"
        return f"{self.capability_ref}: failed -- {self.failure.summary()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability_ref,
            "tenant": self.tenant,
            "status": self.status,
            "outputs": self.outputs,
            "outcome": self.outcome.to_dict() if self.outcome else None,
            "failure": self.failure.to_dict() if self.failure else None,
            "drift": [d.to_dict() for d in self.drift],
            "steps": [s.to_dict() for s in self.steps],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "evidence_dir": self.evidence_dir,
            "escalation_id": self.escalation_id,
            "handoffs": list(self.handoffs),
            "safe_to_retry": self.may_be_retried_safely,
        }

    # -- constructors ------------------------------------------------------

    @classmethod
    def succeeded(cls, capability_ref: str, outputs: dict[str, Any], **kw) -> "ReplayResult":
        return cls(capability_ref=capability_ref, status=SUCCESS, outputs=outputs, **kw)

    @classmethod
    def business(cls, capability_ref: str, outcome: OutcomeReport, **kw) -> "ReplayResult":
        return cls(capability_ref=capability_ref, status=BUSINESS_OUTCOME, outcome=outcome, **kw)

    @classmethod
    def failed(cls, capability_ref: str, failure: Failure, **kw) -> "ReplayResult":
        return cls(capability_ref=capability_ref, status=FAILURE, failure=failure, **kw)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
