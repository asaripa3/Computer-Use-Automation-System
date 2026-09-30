"""Deterministic replay: the production execution path.

No model is involved in any decision here. Every choice the engine makes --
which control to act on, whether a page is what was expected, whether an
ending is an answer or a failure -- is read from the artifact.

The ordering inside a step is the load-bearing part, and it is this:

    1. absorb any declared recovery that is on screen
    2. check for a declared outcome
    3. only then judge the step's own expectation

Getting that order wrong is the mistake the brief warns about. If the
checkpoint were judged first, a page reading "no member records match" would
be reported as a failed checkpoint -- an incident for someone to investigate
-- when it is the correct answer to the question the caller asked. Recoveries
come before outcomes for the same reason in reverse: an interstitial standing
in front of the page is not an ending, it is something in the way.

Waiting is always *for a declared condition* and never a sleep. The loop polls
until the expectation holds, an outcome appears, or the step's timeout is
reached -- so a not-found result returns immediately instead of after the full
timeout, and a genuinely slow page is waited out rather than raced.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from contract.binding import (
    BindingError, as_step_values, bind_inputs, coerce_output, jsonable,
)
from contract.capability import Capability, Condition, OutcomeSpec, RecoverySpec, Step
from contract.result import (
    DriftSignal, Failure, OutcomeReport, ReplayResult, StepReport, now,
)
from policy.allowlist import Allowlist, PolicyViolation
from policy.redaction import Redactor
from policy.risk import Authorization, decide
from surface.model import Observation, Surface

from .conditions import first_outcome, first_recovery, holds
from .evidence import Evidence
from .resolve import Resolution, Unresolved, resolve

POLL_MS = 200
STABILISE_MS = 2_000


class Escalated(Exception):
    """A human was asked to take over and the run did not resume.

    The seam step 5 plugs into. The engine raises this rather than deciding
    what a handoff looks like, because the decision belongs to whoever owns
    the session, not to the thing replaying steps.
    """

    def __init__(self, reason: str, step_index: int | None, escalation_id: str | None = None):
        self.reason = reason
        self.step_index = step_index
        self.escalation_id = escalation_id
        super().__init__(reason)


@dataclass
class Settled:
    observation: Observation
    outcome: OutcomeSpec | None = None
    satisfied: bool = True
    recoveries: tuple[str, ...] = ()
    timed_out: bool = False


@dataclass
class _Run:
    capability: Capability
    step_values: dict[str, str]
    surface: Surface
    policy: Allowlist
    authorization: Authorization | None
    evidence: Evidence | None
    escalate: Callable | None
    reauthenticate: Callable | None
    recovery_budget: dict[str, int] = field(default_factory=dict)
    # Where the run should be if it is interrupted. Set when a navigation is
    # issued and refreshed after every settled step.
    resume_url: str = ""
    reports: list[StepReport] = field(default_factory=list)
    drift: list[DriftSignal] = field(default_factory=list)
    captured: dict[str, str | None] = field(default_factory=dict)


# -- waiting ---------------------------------------------------------------

def _apply_recovery(run: _Run, recovery: RecoverySpec, observation: Observation) -> None:
    if recovery.action == "dismiss" and recovery.target is not None:
        found = resolve(recovery.target, observation, inputs=run.step_values)
        if isinstance(found, Resolution):
            run.surface.click(found.node.ref)
        return

    if recovery.action == "retry":
        # Re-issue the request. A transient failure is one that clears when
        # the same thing is asked again, so asking again is the recovery.
        run.surface.goto(run.surface.url)
        return

    if recovery.action == "reauthenticate":
        # Credentials never live in the artifact, so the engine cannot sign in
        # by itself. The caller supplies a hook, or this becomes a handoff.
        if run.reauthenticate is None:
            raise Escalated(
                "the session expired and no re-authentication hook was supplied",
                step_index=None,
            )
        run.reauthenticate(run.surface)
        # Signing on lands on the application's own home page, not on the page
        # the run was working through. Without returning to it the flow would
        # carry on against whatever the sign-on happened to leave on screen --
        # so the recovery is not complete until the run is back where it was.
        if run.resume_url:
            run.surface.goto(run.resume_url)


def await_state(
    run: _Run,
    *,
    condition: Condition | None,
    timeout_ms: int,
) -> Settled:
    """Wait until the page settles into something worth judging."""
    deadline = time.monotonic() + (timeout_ms / 1000)
    applied: list[str] = []

    while True:
        observation = run.surface.observe()

        recovery = first_recovery(
            run.capability, observation,
            budget=run.recovery_budget, inputs=run.step_values,
        )
        if recovery is not None:
            remaining = run.recovery_budget.get(recovery.name, recovery.max_attempts)
            run.recovery_budget[recovery.name] = remaining - 1
            if run.evidence:
                run.evidence.note("recovery", name=recovery.name,
                                  action=recovery.action, remaining=remaining - 1)
            _apply_recovery(run, recovery, observation)
            applied.append(recovery.name)
            if time.monotonic() < deadline:
                continue
            return Settled(observation, None, False, tuple(applied), True)

        outcome = first_outcome(run.capability, observation, inputs=run.step_values)
        if outcome is not None:
            return Settled(observation, outcome, False, tuple(applied), False)

        if condition is None or holds(condition, observation, inputs=run.step_values):
            return Settled(observation, None, True, tuple(applied), False)

        if time.monotonic() >= deadline:
            return Settled(observation, None, False, tuple(applied), True)

        time.sleep(POLL_MS / 1000)


# -- acting ----------------------------------------------------------------

def _perform(run: _Run, step: Step, observation: Observation) -> tuple[str | None, Observation]:
    """Carry out one step. Returns which ladder rung resolved its target."""
    if step.action == "navigate":
        # Recorded as a path, resolved against the surface this capability is
        # running against -- which is what a tenant overlay changes.
        url = run.capability.surface.absolute(step.url or "")
        run.policy.check_navigation(url)
        run.resume_url = url
        run.surface.goto(url)
        return None, observation

    # Every action that reaches here has a target: the schema refuses a
    # click, fill or select without one.
    assert step.target is not None, f"step {step.index} has no target"

    found = resolve(step.target, observation, inputs=run.step_values)
    if isinstance(found, Unresolved):
        raise _Unresolvable(found)

    if found.drifted:
        run.drift.append(DriftSignal(
            step_index=step.index,
            target=step.target.description,
            recorded_best=step.target.ladder()[0].strategy,
            resolved_by=found.candidate.strategy,
            detail=(
                f"the recorded {step.target.ladder()[0].strategy} candidate no "
                f"longer resolves; fell back to {found.candidate.strategy}"
            ),
        ))

    ref = found.node.ref
    if step.action == "click":
        run.surface.click(ref)
    elif step.action == "fill":
        run.surface.fill(ref, step.value.resolve(run.step_values))
    elif step.action == "select":
        run.surface.select(ref, step.value.resolve(run.step_values))
    elif step.action in {"wait_for", "assert"}:
        pass

    return found.candidate.strategy, observation


class _Unresolvable(Exception):
    def __init__(self, unresolved: Unresolved) -> None:
        self.unresolved = unresolved
        super().__init__(unresolved.detail)


# -- extraction ------------------------------------------------------------

def _extract(run: _Run, observation: Observation, specs) -> None:
    for spec in specs:
        found = resolve(spec.source, observation, inputs=run.step_values)
        if isinstance(found, Resolution):
            run.captured[spec.name] = found.node.text or found.node.value or ""
        else:
            run.captured.setdefault(spec.name, None)


# -- the engine ------------------------------------------------------------

def replay(
    capability: Capability,
    inputs: dict,
    surface: Surface,
    *,
    policy: Allowlist,
    authorization: Authorization | None = None,
    tenant: str | None = None,
    evidence: Evidence | None = None,
    escalate: Callable | None = None,
    reauthenticate: Callable | None = None,
) -> ReplayResult:
    """Run a capability against a surface and report what happened."""
    started = now()
    clock = time.monotonic()
    ref = capability.ref
    run: _Run | None = None

    def finish(result: ReplayResult) -> ReplayResult:
        from dataclasses import replace as _replace

        result = _replace(
            result,
            steps=tuple(run.reports) if run else result.steps,
            drift=tuple(run.drift) if run else result.drift,
            tenant=tenant,
            started_at=started,
            finished_at=now(),
            duration_ms=int((time.monotonic() - clock) * 1000),
            evidence_dir=str(evidence.directory) if evidence else None,
        )
        if evidence:
            evidence.finish(result)
        return result

    # 1. The contract, before anything is touched.
    try:
        bound = bind_inputs(capability, inputs)
    except BindingError as exc:
        return finish(ReplayResult.failed(ref, Failure(
            kind="contract_violation",
            expected="invocation parameters satisfying the capability's contract",
            observed=str(exc),
            detail="refused before the application was touched",
        )))

    # 2. The guardrail, before anything is touched.
    refusals = policy.refusals_for(capability)
    if refusals:
        return finish(ReplayResult.failed(ref, Failure(
            kind="policy_refused",
            expected=f"a capability permitted by policy {policy.label!r}",
            observed="; ".join(refusals),
            detail="refused before the application was touched",
        )))

    redactor = Redactor.for_capability(capability)
    redactor.learn_all(bound.values)
    if evidence:
        evidence.redactor = redactor
        evidence.note("start", capability=ref, tenant=tenant, policy=policy.label,
                      inputs=redactor.mapping({k: str(v) for k, v in bound.values.items()}))

    run = _Run(
        capability=capability,
        step_values=as_step_values(bound),
        surface=surface,
        policy=policy,
        authorization=authorization,
        evidence=evidence,
        escalate=escalate,
        reauthenticate=reauthenticate,
        recovery_budget={r.name: r.max_attempts for r in capability.recoveries},
    )

    settled: Settled | None = None

    try:
        for step in capability.steps:
            step_clock = time.monotonic()

            # Guardrails per action, every time -- a declaration is a promise,
            # and this is where the promise is actually kept.
            try:
                policy.check_action(step.action)
            except PolicyViolation as exc:
                return finish(_policy_failure(run, step, exc))

            if step.is_irreversible:
                decision = decide(step, capability, policy=policy,
                                  authorization=authorization)
                if evidence:
                    evidence.note("risk", step=step.index,
                                  disposition=decision.disposition, reason=decision.reason)
                if decision.disposition == "block":
                    return finish(_policy_failure(run, step, None, decision.reason))
                if decision.needs_human:
                    raise Escalated(decision.reason, step.index)

            # Settle before acting: absorb anything standing in the way, and
            # notice an ending that has already been reached.
            pre = await_state(run, condition=None, timeout_ms=STABILISE_MS)
            if pre.outcome is not None:
                return finish(_classify(run, pre.outcome, step.index))

            try:
                rung, _ = _perform(run, step, pre.observation)
            except PolicyViolation as exc:
                return finish(_policy_failure(run, step, exc))
            except _Unresolvable as exc:
                return finish(_unresolved_failure(run, step, exc.unresolved, pre.observation))

            settled = await_state(
                run, condition=step.expect,
                timeout_ms=capability.timeout_for(step),
            )

            report = StepReport(
                index=step.index, action=step.action, description=step.description,
                status="recovered" if settled.recoveries else "ok",
                duration_ms=int((time.monotonic() - step_clock) * 1000),
                resolved_by=rung,
                attempts=1 + len(settled.recoveries),
                recoveries_applied=settled.recoveries,
            )

            if settled.outcome is not None:
                run.reports.append(report)
                if evidence:
                    evidence.step(report)
                return finish(_classify(run, settled.outcome, step.index))

            if not settled.satisfied:
                run.reports.append(_failed_report(report))
                if evidence:
                    evidence.step(run.reports[-1])
                return finish(_expectation_failure(run, step, settled))

            run.reports.append(report)
            run.resume_url = settled.observation.url
            if evidence:
                evidence.step(report)

            _extract(run, settled.observation,
                     [o for o in capability.outputs if o.after_step == step.index])

    except Escalated as exc:
        return finish(_escalation_failure(run, exc, settled))

    # 3. The success condition.
    final = await_state(run, condition=capability.success,
                        timeout_ms=capability.default_timeout_ms)
    if final.outcome is not None:
        return finish(_classify(run, final.outcome, None))
    if not final.satisfied:
        return finish(_success_failure(run, final))

    # 4. The declared outputs, given the shape the contract promises.
    _extract(run, final.observation,
             [o for o in capability.outputs if o.after_step is None])
    try:
        outputs = {
            spec.name: jsonable(coerce_output(spec, run.captured.get(spec.name)))
            for spec in capability.outputs
        }
    except BindingError as exc:
        return finish(ReplayResult.failed(ref, Failure(
            kind="contract_violation",
            expected="every declared output present and of its declared type",
            observed=str(exc),
            step_index=len(capability.steps),
        )))

    return finish(ReplayResult.succeeded(ref, outputs))


# -- failure constructors --------------------------------------------------

def _classify(run: _Run, outcome: OutcomeSpec, step_index: int | None) -> ReplayResult:
    """Turn a declared outcome into the right kind of result.

    A *business* outcome is an answer the caller asked for. A *hard* one is
    something broken that happens to have been anticipated -- knowing it can
    occur does not make it acceptable.
    """
    report = OutcomeReport(
        code=outcome.code, kind=outcome.kind,
        description=outcome.description, detected_at_step=step_index,
    )
    if run.evidence:
        run.evidence.note("outcome", code=outcome.code, kind=outcome.kind, step=step_index)

    if outcome.kind == "business":
        return ReplayResult.business(run.capability.ref, report)

    return ReplayResult.failed(run.capability.ref, Failure(
        kind="application_error",
        expected="the application to answer",
        observed=f"{outcome.code}: {outcome.description}",
        step_index=step_index,
        detail="a declared hard outcome; anticipated, but still broken",
    ))


def _record_failure(run: _Run, failure: Failure, observation: Observation | None) -> ReplayResult:
    if run.evidence:
        written = run.evidence.capture_failure(run.surface, observation)
        run.evidence.note("failure", **failure.to_dict(), evidence=written)
    return ReplayResult.failed(run.capability.ref, failure)


def _policy_failure(run, step, violation, reason: str | None = None) -> ReplayResult:
    return _record_failure(run, Failure(
        kind="policy_refused",
        expected=f"step {step.index} to be permitted",
        observed=reason or str(violation),
        step_index=step.index, step_description=step.description,
    ), None)


def _unresolved_failure(run, step, unresolved: Unresolved, observation) -> ReplayResult:
    return _record_failure(run, Failure(
        kind=unresolved.kind,
        expected=step.target.description if step.target else step.description,
        observed=unresolved.detail,
        step_index=step.index, step_description=step.description,
        detail=f"tried {list(unresolved.tried)}",
    ), observation)


def _expectation_failure(run, step, settled: Settled) -> ReplayResult:
    observed = settled.observation
    return _record_failure(run, Failure(
        kind="timeout" if settled.timed_out else "checkpoint_failed",
        expected=step.expect.description if step.expect else "the step to complete",
        observed=f"{observed.title!r} at {observed.url}",
        step_index=step.index, step_description=step.description,
        detail=(
            f"waited {run.capability.timeout_for(step)}ms"
            + (f"; recoveries applied: {list(settled.recoveries)}" if settled.recoveries else "")
        ),
    ), observed)


def _success_failure(run, settled: Settled) -> ReplayResult:
    return _record_failure(run, Failure(
        kind="checkpoint_failed",
        expected=run.capability.success.description,
        observed=f"{settled.observation.title!r} at {settled.observation.url}",
        step_index=len(run.capability.steps),
        detail="every step ran, but the capability's success condition never held",
    ), settled.observation)


def _escalation_failure(run, exc: Escalated, settled: Settled | None) -> ReplayResult:
    observation = settled.observation if settled else None
    result = _record_failure(run, Failure(
        kind="escalated",
        expected="a human to take over and hand control back",
        observed=exc.reason,
        step_index=exc.step_index,
        detail="the run paused for a person and was not resumed",
    ), observation)
    from dataclasses import replace as _replace
    return _replace(result, escalation_id=exc.escalation_id)


def _failed_report(report: StepReport) -> StepReport:
    from dataclasses import replace as _replace
    return _replace(report, status="failed")
