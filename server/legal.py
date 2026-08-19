"""
The legal documents' identity: which version of the Terms and the Privacy Policy
is currently in force.

A version STRING rather than a boolean "accepted", and that is the whole point
of this module. Consent under the GDPR is consent to a specific text at a
specific time; a bare flag cannot answer "what did this customer actually agree
to?", which is the only question that matters when someone asks eighteen months
later. So `users.terms_version` records what was on screen and
`users.terms_accepted_at` records when — together they reconstruct the exact
agreement.

Dated rather than numbered (2026-08-20, not v1.3) so the record reads correctly
without a changelog to hand.

Keep in step with:
  * web/src/lib/legal.ts     — what the /terms and /privacy pages render
  * web/src/app/(legal)/*    — the documents themselves

The value is published on GET /api/meta, so the signup form can send back the
version it actually displayed rather than the API assuming they matched.
"""

#: Current Terms of Service (Όροι Χρήσης).
TERMS_VERSION = "2026-08-20"

#: Current Privacy Policy (Πολιτική Απορρήτου). Versioned separately because the
#: two change for different reasons — a pricing change touches the Terms, a new
#: sub-processor touches the Privacy Policy — and bumping both every time would
#: make the record say a customer re-accepted something that never changed.
PRIVACY_VERSION = "2026-08-20"

#: What is stored on a user row at registration. One string covering both
#: documents, because they are accepted together by a single checkbox and
#: storing them apart would imply a choice the form never offered.
CONSENT_VERSION = f"terms:{TERMS_VERSION}|privacy:{PRIVACY_VERSION}"


def to_dict():
    """The shape GET /api/meta publishes."""
    return {
        "terms_version": TERMS_VERSION,
        "privacy_version": PRIVACY_VERSION,
        "consent_version": CONSENT_VERSION,
        "terms_url": "/terms",
        "privacy_url": "/privacy",
    }
