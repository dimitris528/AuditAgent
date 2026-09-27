"""
The public demo tenant: who it is, and what it may not do.

The demo credentials are printed on the login page (NEXT_PUBLIC_DEMO_LOGIN) and
provisioned by scripts/seed_demo.py, so every visitor shares one account. Most
of what they can do to it is harmless and undone by the next re-seed. A few
things are not, and those are refused here:

  * billing — a Stripe checkout, portal session or cancellation on a shared
    account acts on real Stripe objects, not on the demo's sample books;
  * enrolling 2FA — the first visitor to do it locks every other visitor out
    until someone re-runs the seed script;
  * invoice scanning — each scan is a paid OpenAI call, and a public login
    would make this server an open proxy for them;
  * password reset — the reset mail goes to demo@auditagent.io, and whoever
    reads that mailbox could take the account over for everyone.

The check is on the JWT subject (the username) rather than a database lookup:
it is cheap enough to hang in front of any route as a dependency, and
seed_demo.py refuses to run unless DEMO_USERNAME and DEMO_EMAIL are the SAME
row, so the username alone identifies the demo tenant.
"""

from fastapi import Depends, HTTPException

from server import deps

DEMO_USERNAME = "demo"
DEMO_EMAIL = "demo@auditagent.io"

_BLOCKED_MESSAGE = ("Αυτή η λειτουργία δεν είναι διαθέσιμη στον λογαριασμό "
                    "επίδειξης. Δημιουργήστε δικό σας λογαριασμό για να τη "
                    "χρησιμοποιήσετε.")


def is_demo_username(username):
    return (username or "").strip() == DEMO_USERNAME


def is_demo_email(email):
    return (email or "").strip().lower() == DEMO_EMAIL


def blocked():
    """The 403 every guarded route answers the demo with. Structured like the
    paywall's 402 body so the UI can recognise it by `code`."""
    return HTTPException(status_code=403,
                         detail={"message": _BLOCKED_MESSAGE,
                                 "code": "demo_account_restricted"})


def forbid_demo_user(user: str = Depends(deps.get_current_user)) -> str:
    """Route dependency: the caller's username, unless it is the demo's.

    A drop-in replacement for `Depends(get_current_user)` on the guarded
    routes, so the refusal happens before the handler runs at all — no Stripe
    call, no OpenAI call, no staged 2FA secret.
    """
    if is_demo_username(user):
        raise blocked()
    return user
