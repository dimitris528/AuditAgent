"""
Subscription policy — the 14-day free trial and the paywall that follows it.

Deliberately PURE: no database, no config beyond two env knobs, no HTTP. It
answers one question — "given a stored status and a trial end date, what is this
account allowed to do right now?" — so the same rule is applied by the
registration endpoint, by every request gate, by the Stripe webhook and by the
tests, instead of each re-deriving it slightly differently. Persistence lives in
store.refresh_subscription(); the HTTP mapping lives in server/deps.py.

The three states
----------------
    trialing  free trial running, writes allowed, `trial_ends_at` in the future
    active    paid (a Stripe checkout completed), writes allowed, no trial date
    inactive  trial elapsed or subscription gone — reads still work, writes 402

Two invariants make the rules above unambiguous, and both are worth stating
because a lot of edge cases collapse once they hold:

1. `trial_ends_at` set  ⇔  the account is on (or has just fallen off) a trial.
   Paying CLEARS it (store.set_subscription_status), so a paid account can never
   be dragged back to inactive by a stale trial date. This is also what
   correctly re-classifies the rows written before this module existed, which
   carry status "Active" *together with* a trial date: they were trials, and
   resolve() now reads them as exactly that rather than as permanently paid.

2. An explicitly stored `inactive` always wins. Deactivation is a decision
   (cancellation, a chargeback, an admin), and a trial date left behind on the
   row must not silently re-open access.

Everything else fails closed: an unrecognised status with no trial date is
inactive, not active.
"""

import datetime as dt
import math

# Read through config rather than os.getenv, for the reason spelled out in
# server/database.py: config.load_dotenv() is what makes a local .env visible,
# so reading the environment directly here would make TRIAL_DAYS depend on
# whether config happened to be imported first — and this module is imported by
# server/models.py, which is early.
from config import TRIAL_DAYS as _CONFIGURED_TRIAL_DAYS

# --- The stored `users.subscription_status` values ------------------------
# Lower-case on purpose. The column previously held Airtable's Title Case
# single-select ("Active"), which is why normalize() exists and why
# database.py lower-cases the column once on boot.
TRIALING = "trialing"
ACTIVE = "active"
INACTIVE = "inactive"

#: Statuses that may write. Reads are deliberately NOT gated: an expired tenant
#: has to be able to see the billing page, and locking them out of their own
#: figures would be a hostage-taking, not a paywall.
_WRITABLE = frozenset({TRIALING, ACTIVE})

#: Length of the free trial granted at registration. Env-overridable (TRIAL_DAYS)
#: so a trial can be extended without a deploy.
TRIAL_DAYS = _CONFIGURED_TRIAL_DAYS

#: Below this the billing page nags. Purely cosmetic — nothing is enforced by it.
ENDING_SOON_DAYS = 3

# Values seen in the wild that mean the same thing as one of the three above.
# Stripe's own subscription statuses are included so a status copied verbatim
# off a Stripe object still resolves sensibly.
_ALIASES = {
    "active": ACTIVE,
    "paid": ACTIVE,
    "trialing": TRIALING,
    "trial": TRIALING,
    "inactive": INACTIVE,
    "canceled": INACTIVE,
    "cancelled": INACTIVE,
    "expired": INACTIVE,
    "past_due": INACTIVE,
    "unpaid": INACTIVE,
    "incomplete_expired": INACTIVE,
}


class SubscriptionState:
    """The resolved verdict for one account at one instant.

    Immutable and cheap: it is built on every request that touches the database,
    so it holds only what the gate and the billing page need.
    """

    __slots__ = ("status", "trial_ends_at", "days_left", "stored_status")

    def __init__(self, status, trial_ends_at=None, days_left=0, stored_status=None):
        self.status = status
        self.trial_ends_at = trial_ends_at
        self.days_left = days_left
        # What the row said BEFORE resolve() ran, so the caller can tell whether
        # the database needs updating without recomputing the verdict.
        self.stored_status = stored_status if stored_status is not None else status

    @property
    def allows_writes(self):
        return self.status in _WRITABLE

    @property
    def is_trialing(self):
        return self.status == TRIALING

    @property
    def needs_persisting(self):
        """True when the stored column no longer matches reality — a trial that
        has just elapsed, or a legacy Title-Case value."""
        return self.stored_status != self.status

    def to_dict(self):
        """The payload shape the dashboard and the billing page consume."""
        return {
            "status": self.status,
            "trial_ends_at": iso_utc(self.trial_ends_at),
            "days_left": self.days_left,
            "is_trialing": self.is_trialing,
            "allows_writes": self.allows_writes,
            "ending_soon": self.is_trialing and self.days_left <= ENDING_SOON_DAYS,
            "trial_days": TRIAL_DAYS,
        }

    def __repr__(self):  # pragma: no cover - debugging aid
        return (f"SubscriptionState(status={self.status!r}, "
                f"days_left={self.days_left}, ends={self.trial_ends_at!r})")


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def as_utc(stamp):
    """Attach UTC to a naive datetime.

    Needed because the trial date only round-trips as tz-aware on PostgreSQL:
    the column is TIMESTAMPTZ there, but the test suite runs on SQLite, which
    hands back a NAIVE datetime for the same column. Comparing that against an
    aware `now` raises TypeError, so every read goes through here first.
    """
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=dt.timezone.utc)
    return stamp.astimezone(dt.timezone.utc)


def iso_utc(stamp):
    """UTC ISO-8601 with a "Z" suffix, matching how the rest of the API renders
    instants (server/models.py `_iso_z`)."""
    stamp = as_utc(stamp)
    return stamp.isoformat().replace("+00:00", "Z") if stamp else None


def normalize(status):
    """Map whatever is stored onto one of the three canonical statuses.

    Unknown values fall through to INACTIVE rather than to ACTIVE: a typo in the
    column must cost someone a support ticket, not hand out a free account.
    """
    key = str(status or "").strip().lower()
    return _ALIASES.get(key, INACTIVE)


def trial_end(now=None, days=None):
    """When a trial started `now` runs out."""
    return (now or utcnow()) + dt.timedelta(days=TRIAL_DAYS if days is None else days)


def remaining_days(trial_ends_at, now=None):
    """Whole days left, rounded UP and floored at zero.

    Rounded up so an account registered a minute ago reads "14 ημέρες" rather
    than "13" — the user counts the day they are in.
    """
    ends = as_utc(trial_ends_at)
    if ends is None:
        return 0
    seconds = (ends - (now or utcnow())).total_seconds()
    return max(0, math.ceil(seconds / 86400.0))


def resolve(status, trial_ends_at, now=None):
    """The whole policy, in one place. See the module docstring for the rules."""
    now = now or utcnow()
    stored = normalize(status)
    ends = as_utc(trial_ends_at)

    # Invariant 2: an explicit deactivation is never undone by a leftover date.
    if stored == INACTIVE:
        return SubscriptionState(INACTIVE, ends, 0, stored)

    # Invariant 1: a trial date present means this is a trial, whatever the
    # column says — including the legacy "Active" + trial_expiry rows.
    if ends is not None:
        if now < ends:
            return SubscriptionState(TRIALING, ends, remaining_days(ends, now), stored)
        return SubscriptionState(INACTIVE, ends, 0, stored)

    if stored == ACTIVE:
        return SubscriptionState(ACTIVE, None, 0, stored)

    # trialing with no end date should not exist; treat it as a lapsed trial.
    return SubscriptionState(INACTIVE, None, 0, stored)
