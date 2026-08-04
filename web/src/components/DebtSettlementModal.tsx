"use client";

import { useEffect, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Coins,
  HandCoins,
  History,
  Loader2,
  X,
} from "lucide-react";
import { settleDebt } from "@/lib/api";
import type { DebtPaymentRow, TransactionRow } from "@/lib/types";
import { money } from "@/lib/format";
import { clsx } from "@/lib/clsx";

interface Props {
  debt: TransactionRow;
  /** This debt's payments so far, newest first. */
  history: DebtPaymentRow[];
  vatRates: { value: number; label: string }[];
  onClose: () => void;
  /** Fired after a successful settlement so the caller can reload. */
  onSettled: () => void;
}

type Mode = "full" | "partial";

const field =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 dark:border-slate-700 dark:bg-slate-800 dark:text-white";
const labelCls = "mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/**
 * Εξόφληση Χρέους — settle a Χρεωστούμενο row in full or in part.
 *
 * Full settlement deliberately sends NO amount: the server holds the
 * authoritative balance, and a figure computed in the browser from a payload
 * that may be seconds stale is how you leave a cent outstanding forever.
 */
export function DebtSettlementModal({
  debt,
  history,
  vatRates,
  onClose,
  onSettled,
}: Props) {
  const remaining = debt.remaining ?? debt.amount;
  const paidSoFar = debt.paid ?? 0;
  const original = debt.original ?? remaining;

  const [mode, setMode] = useState<Mode>("full");
  const [amount, setAmount] = useState("");
  const [date, setDate] = useState(today());
  const [vatRate, setVatRate] = useState<number>(
    debt.vat_rate ?? vatRates[0]?.value ?? 0.24,
  );
  const [note, setNote] = useState("");
  const [status, setStatus] = useState<"idle" | "saving" | "ok" | "error">("idle");
  const [message, setMessage] = useState("");

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [onClose]);

  const parsed = Number(amount.replace(",", "."));
  const partialValid =
    Number.isFinite(parsed) && parsed > 0 && parsed <= remaining + 0.005;
  const preview = mode === "full" ? 0 : Math.max(0, remaining - (parsed || 0));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (mode === "partial" && !partialValid) {
      setStatus("error");
      setMessage(`Δώστε ποσό μεταξύ 0 και ${money(remaining)}.`);
      return;
    }
    setStatus("saving");
    setMessage("");
    try {
      const result = await settleDebt(debt.id!, {
        // Omitted on a full settlement — see the component docstring.
        amount: mode === "full" ? undefined : parsed,
        vat_rate: vatRate,
        date,
        note: note.trim() || undefined,
      });
      setStatus("ok");
      setMessage(
        result.settled
          ? "Το χρέος εξοφλήθηκε πλήρως."
          : `Καταχωρήθηκε πληρωμή ${money(result.paid)}. Υπόλοιπο ${money(result.remaining)}.`,
      );
      onSettled();
      setTimeout(onClose, 1100);
    } catch (err) {
      setStatus("error");
      setMessage(err instanceof Error ? err.message : "Αποτυχία εξόφλησης.");
    }
  }

  const saving = status === "saving";

  return (
    <div
      className="fixed inset-0 z-[55] flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Εξόφληση χρέους"
    >
      <button
        type="button"
        aria-label="Κλείσιμο"
        onClick={onClose}
        className="absolute inset-0 bg-slate-900/50 backdrop-blur-[2px]"
      />

      <div className="relative flex max-h-[90vh] w-full max-w-md flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900">
        <div className="flex items-start justify-between gap-3 border-b border-slate-200 p-5 dark:border-slate-800">
          <div className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-amber-50 text-amber-600 dark:bg-amber-500/10 dark:text-amber-400">
              <HandCoins className="h-5 w-5" />
            </span>
            <div>
              <h2 className="text-base font-semibold text-slate-900 dark:text-white">
                Εξόφληση Χρέους
              </h2>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {debt.client ?? "—"}
                {debt.doc_number ? ` · ${debt.doc_number}` : ""}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-200"
            aria-label="Κλείσιμο"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5">
          {/* Where the debt stands right now. */}
          <div className="mb-4 grid grid-cols-3 gap-2 rounded-xl border border-slate-100 bg-slate-50/60 p-3 dark:border-slate-800 dark:bg-slate-800/40">
            <div>
              <div className="text-[11px] text-slate-500 dark:text-slate-400">
                Αρχικό
              </div>
              <div className="text-sm font-semibold tabular-nums text-slate-700 dark:text-slate-200">
                {money(original)}
              </div>
            </div>
            <div>
              <div className="text-[11px] text-slate-500 dark:text-slate-400">
                Πληρωμένο
              </div>
              <div className="text-sm font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">
                {money(paidSoFar)}
              </div>
            </div>
            <div>
              <div className="text-[11px] text-slate-500 dark:text-slate-400">
                Υπόλοιπο
              </div>
              <div className="text-sm font-bold tabular-nums text-amber-600 dark:text-amber-400">
                {money(remaining)}
              </div>
            </div>
          </div>

          <form onSubmit={submit} className="space-y-3" id="settle-form">
            <div className="grid grid-cols-2 gap-2">
              {(
                [
                  ["full", "Πλήρης Εξόφληση", money(remaining)],
                  ["partial", "Μερική Εξόφληση", "Custom ποσό"],
                ] as [Mode, string, string][]
              ).map(([key, label, hint]) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setMode(key)}
                  className={clsx(
                    "rounded-xl border p-3 text-left transition",
                    mode === key
                      ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-500/10"
                      : "border-slate-300 hover:border-slate-400 dark:border-slate-700",
                  )}
                >
                  <span
                    className={clsx(
                      "block text-sm font-semibold",
                      mode === key
                        ? "text-indigo-700 dark:text-indigo-300"
                        : "text-slate-700 dark:text-slate-200",
                    )}
                  >
                    {label}
                  </span>
                  <span className="mt-0.5 block text-[11px] text-slate-500 dark:text-slate-400">
                    {hint}
                  </span>
                </button>
              ))}
            </div>

            {mode === "partial" ? (
              <div>
                <label className={labelCls} htmlFor="settle-amount">
                  Ποσό πληρωμής
                </label>
                <input
                  id="settle-amount"
                  className={field}
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  placeholder="200,00"
                  inputMode="decimal"
                  autoFocus
                />
                <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
                  Νέο υπόλοιπο:{" "}
                  <span className="font-semibold tabular-nums text-amber-600 dark:text-amber-400">
                    {money(preview)}
                  </span>
                </p>
              </div>
            ) : null}

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className={labelCls} htmlFor="settle-date">
                  Ημερομηνία πληρωμής
                </label>
                <input
                  id="settle-date"
                  type="date"
                  className={field}
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                />
              </div>
              <div>
                <label className={labelCls} htmlFor="settle-vat">
                  Συντ. Φ.Π.Α.
                </label>
                <select
                  id="settle-vat"
                  className={field}
                  value={vatRate}
                  onChange={(e) => setVatRate(Number(e.target.value))}
                >
                  {vatRates.map((r) => (
                    <option key={r.value} value={r.value}>
                      {r.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div>
              <label className={labelCls} htmlFor="settle-note">
                Σημείωση (προαιρετικό)
              </label>
              <input
                id="settle-note"
                className={field}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="π.χ. Έναντι, μετρητά"
              />
            </div>

            {message ? (
              <div
                className={clsx(
                  "flex items-start gap-1.5 text-xs",
                  status === "ok"
                    ? "text-emerald-600 dark:text-emerald-400"
                    : "text-rose-600 dark:text-rose-400",
                )}
              >
                {status === "ok" ? (
                  <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                ) : (
                  <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                )}
                {message}
              </div>
            ) : null}
          </form>

          {history.length > 0 ? (
            <div className="mt-5 border-t border-slate-200 pt-4 dark:border-slate-800">
              <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-slate-500 dark:text-slate-400">
                <History className="h-3.5 w-3.5" />
                Ιστορικό πληρωμών ({history.length})
              </h3>
              <ul className="space-y-1.5">
                {history.map((p) => (
                  <li
                    key={p.id}
                    className="flex items-center justify-between gap-3 text-xs"
                  >
                    <span className="min-w-0 truncate text-slate-600 dark:text-slate-300">
                      {p.paid_date ?? "—"}
                      {p.note ? ` · ${p.note}` : ""}
                      {p.kind === "full" ? " · εξόφληση" : ""}
                    </span>
                    <span className="shrink-0 tabular-nums">
                      <span className="font-semibold text-emerald-600 dark:text-emerald-400">
                        {money(p.amount)}
                      </span>
                      <span className="ml-2 text-slate-400">
                        → {money(p.remaining)}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-slate-200 p-4 dark:border-slate-800">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium text-slate-600 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            Άκυρο
          </button>
          <button
            type="submit"
            form="settle-form"
            disabled={saving || status === "ok"}
            className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-emerald-500 disabled:opacity-60"
          >
            {saving ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Coins className="h-4 w-4" />
            )}
            {mode === "full"
              ? `Εξόφληση ${money(remaining)}`
              : "Καταχώρηση Πληρωμής"}
          </button>
        </div>
      </div>
    </div>
  );
}
