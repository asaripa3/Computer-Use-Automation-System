"""Synthetic member records for ShareBase.

Nothing here is real. The records are shaped like regulated member data
(names, DOB, SSN last four, balances) so that redaction and PII handling
downstream have something realistic to bite on, but every value is invented.

The store is deliberately in-memory and rebuilt per process. Mutations made
during a run (opening a sub-account) live only for the life of the process so
that replays start from a known state after a restart.
"""

from __future__ import annotations

import copy
import itertools
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterator


# Account types offered by the sub-account form, in display order.
ACCOUNT_TYPES: list[tuple[str, str]] = [
    ("SAV", "Regular Savings"),
    ("SAV-H", "Holiday Club Savings"),
    ("SAV-V", "Vacation Club Savings"),
    ("MMA", "Money Market"),
]

# A member may hold at most this many savings-family sub-accounts.
SUBACCOUNT_LIMIT = 4

MIN_OPENING_DEPOSIT = Decimal("25.00")


@dataclass
class Account:
    number: str
    kind: str  # one of ACCOUNT_TYPES codes, or "CHK"
    description: str
    balance: Decimal
    opened: str  # ISO date
    status: str  # ACTIVE | DORMANT | CLOSED


@dataclass
class Member:
    member_id: str
    first_name: str
    last_name: str
    dob: str  # ISO date
    ssn_last4: str
    phone: str
    email: str
    status: str  # ACTIVE | INACTIVE | CLOSED
    branch: str
    accounts: list[Account] = field(default_factory=list)
    # Servicing restriction: teller-level credentials may read this member but
    # may not open accounts for them. Exercises a permission-denial outcome.
    restricted: bool = False

    @property
    def display_name(self) -> str:
        return f"{self.last_name}, {self.first_name}"

    def savings_accounts(self) -> list[Account]:
        return [a for a in self.accounts if a.kind.startswith("SAV") or a.kind == "MMA"]

    def primary_savings(self) -> Account | None:
        for account in self.accounts:
            if account.kind == "SAV" and account.status == "ACTIVE":
                return account
        return None

    def funding_sources(self) -> list[Account]:
        return [a for a in self.accounts if a.status == "ACTIVE"]


def _seed() -> list[Member]:
    """Deterministic seed set.

    Each member exists to make one branch of the flow reachable:

      12345  happy path: active, has a regular savings account to read
      12346  has checking only -- savings balance is legitimately absent
      12347  restricted: readable, but opening an account is denied
      12348  already at the sub-account limit
      12349  dormant savings -- balance present but account not ACTIVE
      22001  shares a surname with 22002 so name search returns two rows
      22002  second row of that multi-result search
      33100  closed member: found, but not serviceable
    """
    return [
        Member(
            member_id="12345",
            first_name="Dolores",
            last_name="Ashworth",
            dob="1974-03-11",
            ssn_last4="4182",
            phone="(319) 555-0142",
            email="d.ashworth@example.invalid",
            status="ACTIVE",
            branch="Cedar Falls Main",
            accounts=[
                Account("0001234501", "SAV", "Regular Savings", Decimal("4821.37"), "2009-06-02", "ACTIVE"),
                Account("0001234502", "CHK", "Free Checking", Decimal("1290.04"), "2009-06-02", "ACTIVE"),
                Account("0001234503", "SAV-H", "Holiday Club Savings", Decimal("310.00"), "2018-01-15", "ACTIVE"),
            ],
        ),
        Member(
            member_id="12346",
            first_name="Marcus",
            last_name="Pell",
            dob="1988-11-27",
            ssn_last4="7734",
            phone="(319) 555-0198",
            email="m.pell@example.invalid",
            status="ACTIVE",
            branch="Waterloo Branch",
            accounts=[
                Account("0001234601", "CHK", "Free Checking", Decimal("612.55"), "2015-02-19", "ACTIVE"),
            ],
        ),
        Member(
            member_id="12347",
            first_name="Ruthanne",
            last_name="Kolbeck",
            dob="1961-07-04",
            ssn_last4="2205",
            phone="(319) 555-0177",
            email="r.kolbeck@example.invalid",
            status="ACTIVE",
            branch="Cedar Falls Main",
            restricted=True,
            accounts=[
                Account("0001234701", "SAV", "Regular Savings", Decimal("18204.90"), "2001-09-14", "ACTIVE"),
            ],
        ),
        Member(
            member_id="12348",
            first_name="Gil",
            last_name="Tamsett",
            dob="1979-01-30",
            ssn_last4="9016",
            phone="(319) 555-0163",
            email="g.tamsett@example.invalid",
            status="ACTIVE",
            branch="Hudson Branch",
            accounts=[
                Account("0001234801", "SAV", "Regular Savings", Decimal("2044.12"), "2011-04-08", "ACTIVE"),
                Account("0001234802", "SAV-H", "Holiday Club Savings", Decimal("500.00"), "2013-11-01", "ACTIVE"),
                Account("0001234803", "SAV-V", "Vacation Club Savings", Decimal("1750.25"), "2016-05-23", "ACTIVE"),
                Account("0001234804", "MMA", "Money Market", Decimal("9300.00"), "2019-08-12", "ACTIVE"),
            ],
        ),
        Member(
            member_id="12349",
            first_name="Estelle",
            last_name="Brannigan",
            dob="1951-12-19",
            ssn_last4="6640",
            phone="(319) 555-0121",
            email="e.brannigan@example.invalid",
            status="ACTIVE",
            branch="Cedar Falls Main",
            accounts=[
                Account("0001234901", "SAV", "Regular Savings", Decimal("87.10"), "1998-03-05", "DORMANT"),
            ],
        ),
        Member(
            member_id="22001",
            first_name="Harold",
            last_name="Vandermeer",
            dob="1966-05-08",
            ssn_last4="1177",
            phone="(319) 555-0210",
            email="h.vandermeer@example.invalid",
            status="ACTIVE",
            branch="Waterloo Branch",
            accounts=[
                Account("0002200101", "SAV", "Regular Savings", Decimal("15310.66"), "2004-07-30", "ACTIVE"),
            ],
        ),
        Member(
            member_id="22002",
            first_name="Nadia",
            last_name="Vandermeer",
            dob="1992-02-14",
            ssn_last4="3398",
            phone="(319) 555-0211",
            email="n.vandermeer@example.invalid",
            status="ACTIVE",
            branch="Waterloo Branch",
            accounts=[
                Account("0002200201", "SAV", "Regular Savings", Decimal("903.41"), "2020-10-06", "ACTIVE"),
            ],
        ),
        Member(
            member_id="33100",
            first_name="Wendell",
            last_name="Croy",
            dob="1943-08-22",
            ssn_last4="5521",
            phone="(319) 555-0250",
            email="w.croy@example.invalid",
            status="CLOSED",
            branch="Hudson Branch",
            accounts=[
                Account("0003310001", "SAV", "Regular Savings", Decimal("0.00"), "1989-01-11", "CLOSED"),
            ],
        ),
    ]


class MemberStore:
    """Process-local member store with deterministic seeding."""

    def __init__(self) -> None:
        self._members: dict[str, Member] = {}
        self._account_seq: Iterator[int] = itertools.count(1)
        self.reset()

    def reset(self) -> None:
        self._members = {m.member_id: copy.deepcopy(m) for m in _seed()}
        self._account_seq = itertools.count(1)

    def get(self, member_id: str) -> Member | None:
        return self._members.get((member_id or "").strip())

    def search(self, member_id: str = "", last_name: str = "") -> list[Member]:
        """Search by exact member id or last-name prefix.

        Returns an empty list when nothing matches. An empty result is a
        legitimate business outcome here, not an error -- the app renders a
        "no records" results page rather than an error page.
        """
        member_id = (member_id or "").strip()
        last_name = (last_name or "").strip().lower()

        if member_id:
            member = self._members.get(member_id)
            return [member] if member else []

        if last_name:
            matches = [
                m for m in self._members.values()
                if m.last_name.lower().startswith(last_name)
            ]
            return sorted(matches, key=lambda m: (m.last_name, m.first_name))

        return []

    def next_account_number(self, member: Member) -> str:
        """Allocate a sub-account number derived from the member id.

        Deterministic given a fresh process: the Nth account opened for a
        member always gets the same number.
        """
        suffix = len(member.accounts) + 1
        return f"{int(member.member_id):010d}"[:6] + f"{suffix:04d}"

    def open_subaccount(
        self,
        member: Member,
        kind: str,
        description: str,
        deposit: Decimal,
        opened: str,
    ) -> Account:
        account = Account(
            number=self.next_account_number(member),
            kind=kind,
            description=description,
            balance=deposit,
            opened=opened,
            status="ACTIVE",
        )
        member.accounts.append(account)
        return account


store = MemberStore()


def account_type_label(code: str) -> str:
    for value, label in ACCOUNT_TYPES:
        if value == code:
            return label
    return code
