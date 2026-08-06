"use client";

import { useState } from "react";
import { AlertCircle, ExternalLink, Loader2, Wallet } from "lucide-react";
import { openBillingPortal, startCheckout } from "@/lib/api";
import { ApiError } from "@/lib/errors";

/**
 * Opens Stripe's hosted customer portal: card, invoices, plan change,
 * cancellation.
 *
 * `hasCustomer` says whether this tenant has ever been through checkout. The
 * portal can only be opened for a Stripe customer, so without one the backend
 * answers 409 and there is nothing to manage yet — the click is sent to
 * Checkout instead, which is the step that creates the customer. That makes the
 * button safe to render for a trial account that has never paid: it hands off
 * to Stripe either way rather than landing on an error.
 *
 * The 409 is ALSO caught after the fact, not just pre-empted by the prop: the
 * prop comes from a server render that can be seconds stale, and the failure
 * mode it guards against (customer deleted in the Stripe dashboard, status
 * fetched before a checkout completed) is exactly the one a user cannot act on.
 *
 * Secondary styling on purpose — on the billing page this sits next to
 * "Ενεργοποίηση συνδρομής", and two filled indigo buttons would give a visitor
 * no idea which one charges them.
 *
 * Like SubscribeButton it stays busy after a successful call: the page is being
 * replaced by Stripe's, and returning the button to idle mid-hand-off invites a
 * second click and a second (wasted) portal session.
 */
export function ManageBillingButton({
  hasCustomer = true,
}: {
  /** False when the account has no Stripe customer yet — see above. */
  hasCustomer?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function onClick() {
    setBusy(true);
    setError("");
    try {
      const { url } = hasCustomer ? await openPortalOrCheckout() : await startCheckout();
      // Full navigation, not router.push — the destination is stripe.com.
      window.location.assign(url);
    } catch (err) {
      setBusy(false);
      setError(
        err instanceof Error
          ? err.message
          : "Αποτυχία ανοίγματος διαχείρισης συνδρομής.",
      );
    }
  }

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={onClick}
        disabled={busy}
        className="inline-flex w-full items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:border-slate-600 dark:hover:bg-slate-700"
      >
        {busy ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Wallet className="h-4 w-4" />
        )}
        {busy ? "Άνοιγμα διαχείρισης…" : "Διαχείριση συνδρομής"}
        {busy ? null : <ExternalLink className="h-3.5 w-3.5 opacity-60" />}
      </button>
      {error ? (
        <p className="flex items-center justify-center gap-1.5 text-xs text-rose-600 dark:text-rose-400">
          <AlertCircle className="h-4 w-4 shrink-0" />
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** The portal, falling back to Checkout when there is no customer to manage. */
async function openPortalOrCheckout(): Promise<{ url: string }> {
  try {
    return await openBillingPortal();
  } catch (err) {
    // 409 is the backend's "no Stripe customer yet" — the one portal failure
    // that has a sensible next step rather than a message to apologise with.
    if (err instanceof ApiError && err.status === 409) return startCheckout();
    throw err;
  }
}
