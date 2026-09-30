"""Author the seed capability artifacts by observing the running application.

The recorder that will do this from a model-driven run arrives in step 4. Until
then these artifacts are hand-authored -- but *not* hand-typed: every locator
is derived from a real observation of the real application, so the field names
and column headers in the artifact are facts read off the surface rather than
values remembered from the markup.

That ordering is deliberate. Building replay against an artifact authored this
way forces the schema to be right before anything depends on a model.

    PYTHONPATH=src python3 tools/author_capabilities.py
"""

from __future__ import annotations

import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from contract import io  # noqa: E402
from contract.capability import (  # noqa: E402
    Capability, Condition, InputSpec, OutcomeSpec, OutputSpec,
    Provenance, RecoverySpec, Step, SurfaceSpec,
)
from contract.derive import locator_for  # noqa: E402
from contract.locator import DERIVED, Locator, LocatorCandidate  # noqa: E402
from contract.values import Value  # noqa: E402
from surface.browser import browser_session  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "capabilities"
RECORDED_AT = datetime.now(timezone.utc).isoformat(timespec="seconds")


def grid_locator(description, column, key_column, key_value, frame="contentFrame"):
    """A value addressed by its column and a key in another column."""
    return Locator(
        description=description,
        frame=frame,
        candidates=(
            LocatorCandidate(
                strategy="grid_cell",
                robustness=DERIVED,
                column=column,
                key_column=key_column,
                key_value=key_value,
                rationale=(
                    "addressed by column and a key in another column, so it "
                    "survives the row moving when a member holds a different "
                    "number of accounts; a row index would not"
                ),
            ),
        ),
    )


def build(observe_at: str, record_as: str) -> list[Capability]:
    # Observed on a throwaway port so authoring never collides with a
    # server the developer is already running; recorded against the
    # canonical address, because the artifact has to be replayable by
    # whoever runs `make run` -- not only on the port it was authored on.
    base = record_as
    with browser_session() as surface:
        surface.goto(f"{observe_at}/login")
        login = surface.observe()
        surface.fill(login.field("Operator ID").ref, "svc_agent")
        surface.fill(login.field("Password").ref, "Demo-Pass-1234")
        surface.click(login.find(role="button", name="Sign On")[0].ref)

        surface.goto(f"{observe_at}/console/search")
        search = surface.observe()
        member_no_field = locator_for(
            search.field("Member Number"), description="the Member Number field on the lookup form"
        )
        search_button = locator_for(
            search.find(role="button", name="Search")[0], description="the Search button"
        )

        surface.goto(f"{observe_at}/console/member/12345")
        detail = surface.observe()
        member_name = locator_for(
            detail.value_cell("Name"), description="the member's name on the detail panel"
        )

        surface.goto(f"{observe_at}/console/member/12345/subaccount")
        form = surface.observe()
        acct_type = locator_for(form.field("Account Type"), description="the Account Type list")
        nickname = locator_for(form.field("Nickname"), description="the Nickname field")
        deposit = locator_for(form.field("Initial Deposit"), description="the Initial Deposit field")
        funding = locator_for(form.field("Funding Source"), description="the Funding Source list")
        continue_button = locator_for(
            form.find(role="button", name="Continue")[0], description="the Continue button"
        )

    # -- shared runtime conditions -----------------------------------------
    # Each of these is a condition the target can be made to produce on
    # demand, which is what makes them testable rather than aspirational.
    recoveries = (
        RecoverySpec(
            name="maintenance_notice",
            description="A system notice is served in place of the requested page.",
            detect=Condition("text_present", "the maintenance advisory banner",
                             text="Scheduled maintenance advisory"),
            action="dismiss",
            target=Locator(
                description="the Continue button on the notice",
                frame="contentFrame",
                candidates=(
                    LocatorCandidate("role_and_name", DERIVED, role="button", name="Continue",
                                     name_source="value",
                                     rationale="the notice's only control"),
                ),
            ),
            max_attempts=1,
        ),
        RecoverySpec(
            name="transient_server_error",
            description="A timeout from the data tier that clears on retry.",
            detect=Condition("text_present", "the timeout error page", text="Timeout expired"),
            action="retry",
            max_attempts=2,
        ),
        RecoverySpec(
            name="session_expired",
            description="The console session timed out and bounced to sign-on.",
            detect=Condition("text_present", "the expiry notice on the sign-on page",
                             text="Your session has expired"),
            action="reauthenticate",
            max_attempts=1,
        ),
    )

    not_found = OutcomeSpec(
        code="MEMBER_NOT_FOUND",
        kind="business",
        description="No member record exists for the supplied member number. "
                    "A legitimate answer, not a failure.",
        detect=Condition("text_present", "the empty results banner",
                         text="No member records match"),
    )
    index_down = OutcomeSpec(
        code="MEMBER_INDEX_UNAVAILABLE",
        kind="hard",
        description="The member index is down. Nothing can be answered until it returns.",
        detect=Condition("text_present", "the repository failure page",
                         text="RepositoryUnavailableException"),
    )

    origins = (base,)

    # -- capability 1: read a savings balance (read-only) -------------------
    savings = Capability(
        id="member.savings_balance",
        version="1.0.0",
        status="approved",
        title="Read a member's current savings balance",
        description=(
            "Look up a member by number and return the current balance of "
            "their regular savings share, together with the member's name so "
            "the caller can confirm it reached the right record."
        ),
        surface=SurfaceSpec(
            kind="web", entry_url=f"{base}/console/search",
            requires_origins=origins, app_fingerprint="ShareBase 4.2.1",
        ),
        inputs=(
            InputSpec(
                name="member_id", type="string",
                description="The member number to look up.",
                pattern=r"^[0-9]{4,10}$", sensitivity="pii", example="12345",
            ),
        ),
        outputs=(
            OutputSpec(
                name="member_name", type="string",
                description="The member's name as shown on the detail panel.",
                source=member_name, sensitivity="pii",
            ),
            OutputSpec(
                name="savings_balance", type="money",
                description="Current balance of the regular savings share.",
                source=grid_locator(
                    "the Current Balance cell of the Regular Savings row",
                    column="Current Balance",
                    key_column="Description",
                    key_value=Value(literal="Regular Savings"),
                ),
                sensitivity="internal",
                optional=True,  # a member may legitimately hold no savings share
            ),
        ),
        steps=(
            Step(1, "navigate", "Open the member lookup form.", url=f"{base}/console/search"),
            Step(2, "fill", "Enter the member number.",
                 target=member_no_field, value=Value(from_input="member_id")),
            Step(3, "click", "Run the search.", target=search_button),
            Step(4, "click", "Open the matching member's record.",
                 target=grid_locator(
                     "the Member No cell of the row matching the supplied member number",
                     column="Member No", key_column="Member No",
                     key_value=Value(from_input="member_id"),
                 ),
                 expect=Condition("text_present", "the member detail page has loaded",
                                  text="Share & Deposit Accounts")),
        ),
        success=Condition("text_present", "the share and deposit grid is on screen",
                          text="Share & Deposit Accounts"),
        outcomes=(not_found, index_down),
        recoveries=recoveries,
        provenance=Provenance(
            recorded_at=RECORDED_AT, recorded_by="hand-authored from live observation",
            goal="look up member 12345 and read their current savings balance",
        ),
    )

    # -- capability 2: open a sub-account (ends in a commit) ----------------
    subaccount = Capability(
        id="member.open_subaccount",
        version="1.0.0",
        status="approved",
        title="Open a savings sub-account for a member",
        description=(
            "Open an additional savings share for an existing member and "
            "return the new account number and confirmation reference. The "
            "final step commits the account and cannot be undone."
        ),
        surface=SurfaceSpec(
            kind="web", entry_url=f"{base}/console/search",
            requires_origins=origins, app_fingerprint="ShareBase 4.2.1",
        ),
        inputs=(
            InputSpec("member_id", "string", "The member to open the share for.",
                      pattern=r"^[0-9]{4,10}$", sensitivity="pii", example="12345"),
            InputSpec("account_type", "enum", "Which savings product to open.",
                      choices=("Regular Savings", "Holiday Club Savings",
                               "Vacation Club Savings", "Money Market"),
                      example="Vacation Club Savings"),
            InputSpec("nickname", "string", "Operator-visible name for the new share.",
                      pattern=r"^[A-Za-z0-9 ]{1,24}$", example="Vacation 2027"),
            InputSpec("initial_deposit", "money", "Opening deposit; the product minimum is 25.00.",
                      example="150.00"),
            InputSpec("funding_account", "string",
                      "The existing account the opening deposit is drawn from.",
                      sensitivity="internal", example="0001234502"),
        ),
        outputs=(
            OutputSpec(
                "new_account_number", "string", "The account number that was opened.",
                source=Locator(
                    "the New Account Number field on the confirmation page",
                    frame="contentFrame",
                    candidates=(
                        LocatorCandidate("role_and_name", DERIVED, role="cell",
                                         name="New Account Number",
                                         name_source="adjacent-label",
                                         rationale="label/value panel on the confirmation page"),
                    ),
                ),
                sensitivity="internal",
            ),
            OutputSpec(
                "confirmation_number", "string", "The posting reference for this opening.",
                source=Locator(
                    "the Confirmation Number field on the confirmation page",
                    frame="contentFrame",
                    candidates=(
                        LocatorCandidate("role_and_name", DERIVED, role="cell",
                                         name="Confirmation Number",
                                         name_source="adjacent-label",
                                         rationale="label/value panel on the confirmation page"),
                    ),
                ),
                sensitivity="internal",
            ),
        ),
        steps=(
            Step(1, "navigate", "Open the sub-account form for the member.",
                 url=f"{base}/console/search"),
            Step(2, "fill", "Enter the member number.",
                 target=member_no_field, value=Value(from_input="member_id")),
            Step(3, "click", "Run the search.", target=search_button),
            Step(4, "click", "Open the matching member's record.",
                 target=grid_locator(
                     "the Member No cell of the row matching the supplied member number",
                     column="Member No", key_column="Member No",
                     key_value=Value(from_input="member_id"),
                 )),
            Step(5, "click", "Start a sub-account request.",
                 target=Locator(
                     "the Open Sub-Account servicing action", frame="contentFrame",
                     candidates=(
                         LocatorCandidate("role_and_name", "asserted", role="link",
                                          name="Open Sub-Account", name_source="text",
                                          rationale="link text asserted by the application"),
                     ),
                 ),
                 expect=Condition("text_present", "the sub-account form is on screen",
                                  text="Open Sub-Account")),
            Step(6, "select", "Choose the savings product.",
                 target=acct_type, value=Value(from_input="account_type")),
            Step(7, "fill", "Name the new share.",
                 target=nickname, value=Value(from_input="nickname")),
            Step(8, "fill", "Enter the opening deposit.",
                 target=deposit, value=Value(from_input="initial_deposit")),
            Step(9, "select", "Choose the funding account.",
                 target=funding, value=Value(from_input="funding_account")),
            Step(10, "click", "Submit the request for review.", target=continue_button,
                 expect=Condition("text_present", "the review page is on screen",
                                  text="Confirm Sub-Account Request")),
            Step(11, "click", "Commit the new share.",
                 risk="irreversible",
                 target=Locator(
                     "the Confirm button on the review page", frame="contentFrame",
                     candidates=(
                         LocatorCandidate("asserted_id", "asserted", id_kind="field_name",
                                          id_value="btnConfirm",
                                          rationale="the form field name the application posts back"),
                         LocatorCandidate("role_and_name", "asserted", role="button",
                                          name="Confirm", name_source="value",
                                          rationale="button caption asserted by the application"),
                     ),
                 ),
                 expect=Condition("text_present", "the account was opened",
                                  text="Sub-account opened successfully")),
        ),
        success=Condition("text_present", "the confirmation page is on screen",
                          text="Sub-account opened successfully"),
        outcomes=(
            not_found,
            index_down,
            OutcomeSpec("PERMISSION_DENIED", "business",
                        "A servicing restriction is on file; a supervisor must authorise this.",
                        Condition("text_present", "the denial banner", text="SEC-403")),
            OutcomeSpec("SUBACCOUNT_LIMIT_REACHED", "business",
                        "The member already holds the maximum number of savings shares.",
                        Condition("text_present", "the limit banner", text="ACCT-422")),
            OutcomeSpec("MEMBER_NOT_SERVICEABLE", "business",
                        "The member record is closed or inactive and may not be serviced.",
                        Condition("text_present", "the not-serviceable banner", text="MBR-409")),
            OutcomeSpec("VALIDATION_REJECTED", "business",
                        "The application rejected the submitted values.",
                        Condition("text_present", "the field validation banner",
                                  text="The request could not be processed")),
            OutcomeSpec("REQUEST_NOT_IN_PROGRESS", "hard",
                        "The server has no sub-account request in progress; the flow was "
                        "replayed out of order or the session was lost mid-request.",
                        Condition("text_present", "the sequence error", text="SEQ-409")),
        ),
        recoveries=recoveries,
        provenance=Provenance(
            recorded_at=RECORDED_AT, recorded_by="hand-authored from live observation",
            goal="open a new sub-account for this member and reach the confirmation screen",
        ),
    )

    return [savings, subaccount]


def main() -> int:
    from werkzeug.serving import make_server

    from sharebase import create_app

    server = make_server("127.0.0.1", 8099, create_app(), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for capability in build("http://127.0.0.1:8099", "http://127.0.0.1:8080"):
            path = io.save(capability, OUT)
            print(f"wrote {path.relative_to(Path.cwd())}  ({len(capability.steps)} steps, "
                  f"{len(capability.outcomes)} declared outcomes)")
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
