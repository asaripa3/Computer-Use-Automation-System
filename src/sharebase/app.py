"""ShareBase -- a stand-in for the back-office systems this project automates.

ShareBase is a fictional credit-union member servicing console. It exists to be
automated, and it is built to be awkward in the specific ways that matter:

  * the authenticated console is a frameset, so the surface layer has to traverse a
    frame tree rather than a single document;
  * control ids are regenerated on every render, so no locator may depend on
    them;
  * there are no test ids and almost no ARIA -- fields are associated with
    their labels only by table-cell adjacency;
  * some controls are anchors and table cells carrying onclick handlers rather
    than real buttons;
  * balances are rendered without a currency symbol, so extraction has to
    understand a field from its label and column, not from a sigil.

None of that is decoration. Every one of those properties is the common case in
the systems this is standing in for, and each one invalidates a locator
strategy that would otherwise look fine on a modern app.

Runtime conditions -- timeouts, interstitials, transient errors, permission
denials, validation failures -- are armed through :mod:`sharebase.faults` so the
replay error taxonomy can be exercised deliberately and repeatedly.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from flask import (
    Flask,
    Response,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from . import session as svc_session
from .data import (
    ACCOUNT_TYPES,
    MIN_OPENING_DEPOSIT,
    SUBACCOUNT_LIMIT,
    account_type_label,
    store,
)
from .faults import FAULT_SPECS, board
from .legacy import ctl_id, legacy_date, money, viewstate

NICKNAME_PATTERN = re.compile(r"^[A-Za-z0-9 ]{1,24}$")

# Routes that make up the console chrome rather than its content. Runtime
# faults are not injected into these: breaking the frameset itself would only
# obscure whichever condition we were actually trying to provoke.
CHROME_PATHS = {"/console", "/console/nav"}


def business_date() -> str:
    override = os.environ.get("SHAREBASE_BUSINESS_DATE")
    if override:
        return legacy_date(override)
    return legacy_date(date.today().isoformat())


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("SHAREBASE_SECRET", "dev-only-not-a-real-secret")
    app.config["SESSION_COOKIE_NAME"] = "SHAREBASE_SESSIONID"

    # ---------------------------------------------------------------- render

    @app.context_processor
    def _template_globals() -> dict[str, object]:
        return {
            "ctl": ctl_id,
            "viewstate": viewstate(),
            "operator": svc_session.current_user() or "(not signed on)",
            "rendered_at": datetime.now().strftime("%m/%d/%Y %H:%M:%S"),
            "business_date": business_date(),
        }

    def app_error(error_class: str, message: str, frame: str, status: int = 500) -> Response:
        stack = (
            f"[{error_class.rsplit('.', 1)[-1]} (0x80131904): {message}]\r\n"
            f"   {frame} +214\r\n"
            "   ShareBase.Web.Servicing.ConsolePage.Page_Load(Object sender, EventArgs e) +88\r\n"
            "   System.Web.UI.Control.OnLoad(EventArgs e) +99\r\n"
            "   System.Web.UI.Control.LoadRecursive() +50\r\n"
            "   System.Web.UI.Page.ProcessRequestMain(Boolean, Boolean) +1724"
        )
        body = render_template(
            "app_error.html",
            error_class=error_class,
            error_message=message,
            stack=stack,
            reference=uuid.uuid4().hex[:12].upper(),
        )
        return make_response(body, status)

    def outcome_page(
        *,
        heading: str,
        code: str,
        detail: str,
        member=None,
        box_class: str = "warnBox",
        status: int = 200,
    ) -> Response:
        """Render a legitimate business outcome.

        These return HTTP 200 on purpose. "No such member" and "permission
        denied" are answers, not transport failures, and the status code is the
        wrong place to encode them -- a caller that reads outcomes off the
        status code cannot tell a denial apart from a crashed app.
        """
        body = render_template(
            "outcome.html",
            page_title="Member Servicing",
            heading=heading,
            outcome_code=code,
            detail=detail,
            member=member,
            box_class=box_class,
        )
        return make_response(body, status)

    # ------------------------------------------------- injected runtime state

    @app.before_request
    def _runtime_conditions():
        path = request.path
        if not path.startswith("/console") or path in CHROME_PATHS:
            return None

        if board.consume("slow_response"):
            time.sleep(board.delay_seconds)

        if board.consume("transient_error"):
            return app_error(
                "System.Data.SqlClient.SqlException",
                "Timeout expired. The timeout period elapsed prior to completion "
                "of the operation or the server is not responding.",
                "ShareBase.Data.SessionGateway.Acquire(Int32 operatorId)",
            )

        # The interstitial is served in place of the page that was asked for and
        # carries the original target forward, so acknowledging it lands where
        # the caller was originally going.
        if (
            request.method == "GET"
            and "btnAck" not in request.args
            and board.consume("interstitial_notice")
        ):
            return make_response(
                render_template(
                    "notice.html",
                    page_title="Member Servicing",
                    notice_code=f"MA-{uuid.uuid4().hex[:4].upper()}",
                    return_to=path,
                )
            )

        return None

    # ------------------------------------------------------------ sign on/off

    @app.route("/")
    def index():
        if svc_session.current_user():
            return redirect(url_for("console"))
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("txtOperator", "")
            password = request.form.get("txtPassword", "")
            if svc_session.check_credentials(username, password):
                svc_session.begin(username)
                return redirect(url_for("console"))
            return make_response(
                render_template("login.html", reason="invalid"), 200
            )

        reason = request.args.get("reason", "")
        return render_template("login.html", reason=reason)

    @app.route("/logout")
    def logout():
        svc_session.end()
        return redirect(url_for("login", reason="required"))

    # --------------------------------------------------------- console chrome

    @app.route("/console")
    def console():
        if svc_session.current_user() is None:
            return redirect(url_for("login", reason="required"))
        return render_template("frameset.html", content_src=url_for("console_home"))

    @app.route("/console/nav")
    def console_nav():
        return render_template("nav.html")

    @app.route("/console/home")
    def console_home():
        blocked = svc_session.guard()
        if blocked:
            return blocked
        return render_template("home.html", page_title="Servicing Home")

    # -------------------------------------------------------- member lookup

    @app.route("/console/search", methods=["GET", "POST"])
    def console_search():
        blocked = svc_session.guard()
        if blocked:
            return blocked

        if request.method == "GET":
            return render_template(
                "search.html", page_title="Member Lookup", message=None
            )

        if board.consume("search_unavailable"):
            return app_error(
                "ShareBase.Data.RepositoryUnavailableException",
                "The member index is not available. Contact the core operations desk.",
                "ShareBase.Data.MemberRepository.Search(SearchCriteria criteria)",
            )

        member_no = request.form.get("txtMemberNo", "").strip()
        surname = request.form.get("txtSurname", "").strip()

        if not member_no and not surname:
            return render_template(
                "search.html",
                page_title="Member Lookup",
                message="Enter a member number or a surname to search.",
            )

        members = store.search(member_id=member_no, last_name=surname)
        criteria = (
            f"member number {member_no}" if member_no else f"surname beginning {surname!r}"
        )
        return render_template(
            "results.html",
            page_title="Member Lookup",
            members=members,
            criteria=criteria,
        )

    @app.route("/console/member/<member_id>")
    def console_member(member_id: str):
        blocked = svc_session.guard()
        if blocked:
            return blocked

        if board.consume("hard_error"):
            return app_error(
                "System.NullReferenceException",
                "Object reference not set to an instance of an object.",
                f"ShareBase.Web.Servicing.MemberDetail.BindShares(String memberNo='{member_id}')",
            )

        member = store.get(member_id)
        if member is None:
            return outcome_page(
                heading="Member Not Found",
                code="MBR-404",
                detail=f"No member record exists for member number {member_id}.",
            )

        accounts = [
            SimpleNamespace(
                number=a.number,
                kind=a.kind,
                description=a.description,
                status=a.status,
                opened_display=legacy_date(a.opened),
                balance_display=money(a.balance),
            )
            for a in member.accounts
        ]
        return render_template(
            "member.html",
            page_title="Member Detail",
            member=member,
            accounts=accounts,
            dob=legacy_date(member.dob),
        )

    # ----------------------------------------------------- sub-account flow

    def _servicing_gate(member_id: str):
        """Return ``(member, response)``; a response means stop and return it.

        Every branch here is a business outcome the caller has to be able to
        act on, which is why they are all separated from transport failures.
        """
        member = store.get(member_id)
        if member is None:
            return None, outcome_page(
                heading="Member Not Found",
                code="MBR-404",
                detail=f"No member record exists for member number {member_id}.",
            )

        if member.status != "ACTIVE":
            return member, outcome_page(
                heading="Member Not Serviceable",
                code="MBR-409",
                detail=(
                    f"Member {member.member_id} is {member.status}. Account opening "
                    "is not permitted on this record."
                ),
                member=member,
            )

        if board.consume("permission_denied") or member.restricted:
            return member, outcome_page(
                heading="Permission Denied",
                code="SEC-403",
                detail=(
                    "Your operator profile is not authorized to open accounts for "
                    "this member. A servicing restriction is on file and supervisor "
                    "authorization is required."
                ),
                member=member,
            )

        if len(member.savings_accounts()) >= SUBACCOUNT_LIMIT:
            return member, outcome_page(
                heading="Sub-Account Limit Reached",
                code="ACCT-422",
                detail=(
                    f"Member {member.member_id} already holds {SUBACCOUNT_LIMIT} "
                    "share sub-accounts, which is the maximum permitted."
                ),
                member=member,
            )

        return member, None

    def _render_subaccount_form(member, form: dict[str, str], errors: list[str]):
        return render_template(
            "subaccount_form.html",
            page_title="Open Sub-Account",
            member=member,
            account_types=ACCOUNT_TYPES,
            funding_sources=member.funding_sources(),
            min_deposit=money(MIN_OPENING_DEPOSIT),
            form=SimpleNamespace(**form),
            errors=errors,
        )

    @app.route("/console/member/<member_id>/subaccount", methods=["GET", "POST"])
    def console_subaccount(member_id: str):
        blocked = svc_session.guard()
        if blocked:
            return blocked

        member, stop = _servicing_gate(member_id)
        if stop:
            return stop

        empty = {"ddlAcctType": "", "txtNickname": "", "txtDeposit": "", "ddlFunding": ""}

        if request.method == "GET":
            return _render_subaccount_form(member, empty, [])

        form = {
            "ddlAcctType": request.form.get("ddlAcctType", "").strip(),
            "txtNickname": request.form.get("txtNickname", "").strip(),
            "txtDeposit": request.form.get("txtDeposit", "").strip(),
            "ddlFunding": request.form.get("ddlFunding", "").strip(),
        }
        errors = _validate_subaccount(member, form)

        if board.consume("spurious_validation"):
            errors.append(
                "Initial deposit could not be verified against the funding share. "
                "Re-enter the amount and resubmit. (E-4417)"
            )

        if errors:
            return _render_subaccount_form(member, form, errors)

        deposit = Decimal(form["txtDeposit"].replace(",", ""))
        session["pending_subacct"] = {
            "member_id": member.member_id,
            "kind": form["ddlAcctType"],
            "type_label": account_type_label(form["ddlAcctType"]),
            "nickname": form["txtNickname"],
            "deposit": str(deposit),
            "funding": form["ddlFunding"],
        }

        if board.consume("unexpected_confirm"):
            return render_template(
                "subaccount_warning.html",
                page_title="Open Sub-Account",
                member=member,
                advisory_code=f"AD-{uuid.uuid4().hex[:4].upper()}",
            )

        return _review(member)

    def _validate_subaccount(member, form: dict[str, str]) -> list[str]:
        errors: list[str] = []

        if not form["ddlAcctType"]:
            errors.append("Account Type is required.")
        elif form["ddlAcctType"] not in {code for code, _ in ACCOUNT_TYPES}:
            errors.append("Account Type is not a recognized product code.")

        if not form["txtNickname"]:
            errors.append("Nickname is required.")
        elif not NICKNAME_PATTERN.match(form["txtNickname"]):
            errors.append(
                "Nickname may contain only letters, digits and spaces, "
                "up to 24 characters."
            )

        if not form["txtDeposit"]:
            errors.append("Initial Deposit is required.")
        else:
            try:
                deposit = Decimal(form["txtDeposit"].replace(",", ""))
            except InvalidOperation:
                errors.append("Initial Deposit is not a valid amount.")
            else:
                if deposit < MIN_OPENING_DEPOSIT:
                    errors.append(
                        f"Minimum opening deposit is {money(MIN_OPENING_DEPOSIT)}."
                    )

        if not form["ddlFunding"]:
            errors.append("Funding Source is required.")
        elif form["ddlFunding"] not in {a.number for a in member.funding_sources()}:
            errors.append("Funding Source is not an active account on this member.")

        return errors

    def _review(member):
        pending = session.get("pending_subacct")
        deposit = Decimal(pending["deposit"])
        return render_template(
            "subaccount_review.html",
            page_title="Open Sub-Account",
            member=member,
            pending=SimpleNamespace(
                type_label=pending["type_label"],
                nickname=pending["nickname"],
                deposit_display=money(deposit),
                funding=pending["funding"],
            ),
        )

    def _pending_for(member_id: str):
        """Fetch server-side wizard state, or the outcome for having lost it.

        The pending request lives on the server, not in the page, so the
        confirm step cannot be reached by posting to it directly. Replaying the
        commit out of order fails loudly here instead of silently opening an
        account with stale values.
        """
        pending = session.get("pending_subacct")
        if not pending or pending.get("member_id") != member_id:
            return None, outcome_page(
                heading="Request Not In Progress",
                code="SEQ-409",
                detail=(
                    "There is no sub-account request in progress for this member. "
                    "The request may have timed out or been completed already. "
                    "Start the request again from the member record."
                ),
                box_class="errBox",
                status=409,
            )
        return pending, None

    @app.route("/console/member/<member_id>/subaccount/review", methods=["POST"])
    def console_subaccount_review(member_id: str):
        blocked = svc_session.guard()
        if blocked:
            return blocked

        member, stop = _servicing_gate(member_id)
        if stop:
            return stop

        _, missing = _pending_for(member_id)
        if missing:
            return missing

        return _review(member)

    @app.route("/console/member/<member_id>/subaccount/commit", methods=["POST"])
    def console_subaccount_commit(member_id: str):
        blocked = svc_session.guard()
        if blocked:
            return blocked

        member, stop = _servicing_gate(member_id)
        if stop:
            return stop

        pending, missing = _pending_for(member_id)
        if missing:
            return missing

        deposit = Decimal(pending["deposit"])
        account = store.open_subaccount(
            member,
            kind=pending["kind"],
            description=pending["nickname"] or pending["type_label"],
            deposit=deposit,
            opened=date.today().isoformat(),
        )
        session.pop("pending_subacct", None)

        return render_template(
            "subaccount_done.html",
            page_title="Open Sub-Account",
            member=member,
            account=account,
            balance_display=money(account.balance),
            confirmation=f"CNF-{member.member_id}-{account.number[-4:]}",
            posted_at=datetime.now().strftime("%m/%d/%Y %H:%M:%S"),
        )

    # ------------------------------------------------------- harness control

    @app.route("/admin/faults", methods=["GET", "POST"])
    def admin_faults():
        if request.method == "POST":
            payload: dict[str, object]
            if request.is_json:
                payload = dict(request.get_json(silent=True) or {})
            else:
                # An unchecked box is simply absent from a form post, so the
                # form path has to disarm everything it did not receive.
                payload = {spec.key: False for spec in FAULT_SPECS}
                payload.update({k: True for k in request.form if k in payload})
                if request.form.get("delay_seconds"):
                    payload["delay_seconds"] = request.form["delay_seconds"]

            changed = board.apply(payload)

            if request.is_json:
                return {
                    "changed": changed,
                    "faults": board.snapshot(),
                    "delay_seconds": board.delay_seconds,
                }
            return redirect(url_for("admin_faults"))

        return render_template(
            "admin_faults.html",
            specs=FAULT_SPECS,
            state=board.snapshot(),
            armed=board.armed_keys(),
            delay_seconds=board.delay_seconds,
        )

    @app.route("/admin/reset", methods=["POST"])
    def admin_reset():
        board.reset()
        store.reset()
        if request.is_json:
            return {"reset": True, "faults": board.snapshot()}
        return redirect(url_for("admin_faults"))

    @app.route("/healthz")
    def healthz():
        return {"status": "ok", "app": "sharebase", "release": "4.2.1"}

    return app
