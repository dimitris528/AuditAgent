"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertCircle, AlertTriangle, Loader2, X } from "lucide-react";
import { cancelSubscription } from "@/lib/api";

/**
 * Cancels the subscription, behind a confirmation.
 *
 * The confirmation is a real dialog rather than window.confirm because the
 * thing worth saying does not fit in a browser prompt: cancelling does NOT end
 * access today. Stripe cancels at the close of the period already paid for, so
 * the account keeps working until then — and someone who thinks they are
 * losing their books immediately will not press the button at all, or will
 * press it and then panic.
 *
 * On success the server component is refreshed rather than the new state being
 * held here: /billing renders the whole subscription panel from the backend,
 * and a local "cancelled!" flag would be a second copy of that truth, free to
 * disagree with it.
 *
 * Destructive styling, but OUTLINE destructive — this sits under the two Stripe
 * buttons, and a filled red block would out-shout the action most people came
 * to the page for.
 */
export function CancelSubscriptionButton() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const confirmRef = useRef<HTMLButtonElement>(null);

  // Escape closes it. Deliberately NOT while the request is in flight: Stripe
  // has already been asked by then, and letting the dialog vanish mid-call
  // would leave the user with no idea whether it went through.
  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy) setOpen(false);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, busy]);

  // Focus the confirm button on open, so the dialog is reachable by keyboard
  // and a screen reader announces what it is asking.
  useEffect(() => {
    if (open) confirmRef.current?.focus();
  }, [open]);

  async function onConfirm() {
    setBusy(true);
    setError("");
    try {
      await cancelSubscription();
      setOpen(false);
      // Re-renders the server component, which re-reads the backend status and
      // paints the pending-cancellation state.
      router.refresh();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Αποτυχία ακύρωσης συνδρομής.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => {
          setError("");
          setOpen(true);
        }}
        className="inline-flex w-full items-center justify-center gap-2 rounded-lg border border-rose-200 bg-white px-4 py-2.5 text-sm font-semibold text-rose-600 transition hover:border-rose-300 hover:bg-rose-50 dark:border-rose-500/30 dark:bg-transparent dark:text-rose-400 dark:hover:border-rose-500/50 dark:hover:bg-rose-500/10"
      >
        Ακύρωση συνδρομής
      </button>

      {open ? (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Ακύρωση συνδρομής"
        >
          <button
            type="button"
            aria-label="Κλείσιμο"
            onClick={() => (busy ? null : setOpen(false))}
            className="absolute inset-0 bg-slate-900/50 backdrop-blur-[2px]"
          />

          <div className="relative w-full max-w-md overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900">
            <div className="flex items-start justify-between gap-3 border-b border-slate-200 p-5 dark:border-slate-800">
              <div className="flex items-center gap-2.5">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-rose-50 text-rose-600 dark:bg-rose-500/10 dark:text-rose-400">
                  <AlertTriangle className="h-5 w-5" />
                </span>
                <h2 className="text-base font-semibold text-slate-900 dark:text-white">
                  Ακύρωση συνδρομής
                </h2>
              </div>
              <button
                type="button"
                onClick={() => setOpen(false)}
                disabled={busy}
                aria-label="Κλείσιμο"
                className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-600 disabled:opacity-50 dark:hover:bg-slate-800 dark:hover:text-slate-200"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="space-y-3 p-5">
              <p className="text-sm font-medium text-slate-900 dark:text-white">
                Είστε σίγουροι ότι θέλετε να ακυρώσετε τη συνδρομή σας;
              </p>
              <p className="text-sm leading-relaxed text-slate-600 dark:text-slate-300">
                Η πρόσβασή σας παραμένει ενεργή έως το τέλος της περιόδου που
                έχετε ήδη πληρώσει, και δεν θα υπάρξει νέα χρέωση. Μετά τη λήξη,
                η καταχώριση νέων κινήσεων και πελατών απενεργοποιείται — τα
                στοιχεία σας παραμένουν ορατά.
              </p>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Μπορείτε να επαναφέρετε τη συνδρομή οποτεδήποτε πριν από τη λήξη
                μέσω της Διαχείρισης συνδρομής.
              </p>

              {error ? (
                <p className="flex items-start gap-1.5 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                  {error}
                </p>
              ) : null}
            </div>

            <div className="flex items-center justify-end gap-2 border-t border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/60">
              <button
                type="button"
                onClick={() => setOpen(false)}
                disabled={busy}
                className="inline-flex h-9 items-center rounded-lg border border-slate-200 bg-white px-3.5 text-sm font-medium text-slate-700 transition hover:bg-slate-50 disabled:opacity-60 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
              >
                Διατήρηση συνδρομής
              </button>
              <button
                ref={confirmRef}
                type="button"
                onClick={onConfirm}
                disabled={busy}
                className="inline-flex h-9 items-center gap-2 rounded-lg bg-rose-600 px-3.5 text-sm font-semibold text-white transition hover:bg-rose-500 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                {busy ? "Ακύρωση…" : "Ναι, ακύρωση"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
