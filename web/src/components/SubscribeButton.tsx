"use client";

import { useState } from "react";
import { AlertCircle, CreditCard, Loader2 } from "lucide-react";
import { startCheckout } from "@/lib/api";

/**
 * Opens Stripe Checkout.
 *
 * Stays in the "loading" state after a successful call and never resets: the
 * page is being replaced by Stripe's, and flipping the button back to idle
 * during that hand-off invites a second click and a second checkout session.
 */
export function SubscribeButton({
  label = "Ενεργοποίηση συνδρομής",
  disabled = false,
  disabledReason,
}: {
  label?: string;
  /** True when the backend reports billing is not configured. */
  disabled?: boolean;
  disabledReason?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function onClick() {
    setBusy(true);
    setError("");
    try {
      const { url } = await startCheckout();
      // Full navigation, not router.push — the destination is stripe.com.
      window.location.assign(url);
    } catch (err) {
      setBusy(false);
      setError(err instanceof Error ? err.message : "Αποτυχία έναρξης πληρωμής.");
    }
  }

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={onClick}
        disabled={busy || disabled}
        title={disabled ? disabledReason : undefined}
        className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {busy ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <CreditCard className="h-4 w-4" />
        )}
        {busy ? "Άνοιγμα πληρωμής…" : label}
      </button>
      {disabled && disabledReason ? (
        <p className="text-center text-[11px] text-slate-500 dark:text-slate-400">
          {disabledReason}
        </p>
      ) : null}
      {error ? (
        <p className="flex items-center justify-center gap-1.5 text-xs text-rose-600 dark:text-rose-400">
          <AlertCircle className="h-4 w-4 shrink-0" />
          {error}
        </p>
      ) : null}
    </div>
  );
}
