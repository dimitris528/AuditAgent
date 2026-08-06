"use client";

import { useState } from "react";
import { AlertCircle, ExternalLink, Loader2, Wallet } from "lucide-react";
import { openBillingPortal } from "@/lib/api";

/**
 * Opens Stripe's hosted customer portal: card, invoices, plan change,
 * cancellation.
 *
 * Unconditional — whatever the subscription says. The backend opens a portal
 * for every signed-in tenant and creates the Stripe customer if there is not
 * one yet, so this no longer has to know whether the account has paid, lapsed
 * or is still on trial. It used to fall back to Checkout on a 409; that path is
 * gone because the 409 is gone.
 *
 * Secondary styling on purpose — on the billing page this sits next to
 * "Ενεργοποίηση συνδρομής", and two filled indigo buttons would give a visitor
 * no idea which one charges them.
 *
 * Stays busy after a successful call and never resets: the page is being
 * replaced by Stripe's, and returning the button to idle mid-hand-off invites a
 * second click and a second (wasted) portal session.
 */
export function ManageBillingButton() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function onClick() {
    setBusy(true);
    setError("");
    try {
      const { url } = await openBillingPortal();
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
