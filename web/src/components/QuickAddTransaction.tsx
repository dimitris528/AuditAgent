"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Plus,
  Sparkles,
  X,
} from "lucide-react";
import { checkTransactionDuplicate, createTransaction, DuplicateError } from "@/lib/api";
import type { DuplicateTransaction, ScanResult } from "@/lib/types";
import { money } from "@/lib/format";
import { clsx } from "@/lib/clsx";
import { ClientPicker, type PickedClient } from "./ClientPicker";
import { InvoiceScanner } from "./InvoiceScanner";

interface Props {
  vatRates: { value: number; label: string }[];
  defaultVatRate: number;
  /** False when the backend has no OCR key — the scanner is then hidden. */
  scanEnabled?: boolean;
}

const TYPES = [
  { value: "Έσοδο", label: "Έσοδο" },
  { value: "Έξοδο", label: "Έξοδο" },
  { value: "Χρεωστούμενο", label: "Χρεωστούμενο" },
];

const field =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 dark:border-slate-700 dark:bg-slate-800 dark:text-white";
const labelCls = "mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400";

/** The VAT the row will carry: the figure read off the document when there is
 *  one, else the standard derivation from the gross amount. */
function vatPreview(gross: number, rate: number, scanned: number | null): number {
  if (scanned !== null) return scanned;
  if (!rate || !gross) return 0;
  return Math.round(((gross * rate) / (1 + rate)) * 100) / 100;
}

function DuplicateWarning({ duplicate }: { duplicate: DuplicateTransaction }) {
  return (
    <div className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-xs dark:border-amber-500/40 dark:bg-amber-500/10">
      <div className="flex items-start gap-1.5 font-semibold text-amber-800 dark:text-amber-300">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        Το παραστατικό υπάρχει ήδη καταχωρημένο
      </div>
      <p className="mt-1 pl-5 text-amber-700 dark:text-amber-200/80">
        {duplicate.doc_number ?? "—"} · {duplicate.date ?? "—"} ·{" "}
        {duplicate.client ?? "—"} · {money(duplicate.amount)}
        {duplicate.description ? ` · ${duplicate.description}` : ""}
      </p>
      <p className="mt-1 pl-5 text-amber-700 dark:text-amber-200/80">
        Αποθηκεύστε ξανά μόνο αν πρόκειται για διαφορετικό παραστατικό.
      </p>
    </div>
  );
}

export function QuickAddTransaction({
  vatRates,
  defaultVatRate,
  scanEnabled = false,
}: Props) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [client, setClient] = useState<PickedClient | null>(null);
  const [amount, setAmount] = useState("");
  const [type, setType] = useState("Έξοδο");
  const [vatRate, setVatRate] = useState(defaultVatRate);
  // The ΦΠΑ printed on a scanned document. Kept separately from the derived
  // figure and dropped the moment the user edits the amount or the rate, so a
  // stale scan can never override what is now on screen.
  const [scannedVat, setScannedVat] = useState<number | null>(null);
  const [description, setDescription] = useState("");
  const [date, setDate] = useState("");
  const [docNumber, setDocNumber] = useState("");
  const [counterpartyAfm, setCounterpartyAfm] = useState("");
  const [scanNote, setScanNote] = useState("");
  const [duplicate, setDuplicate] = useState<DuplicateTransaction | null>(null);
  const [status, setStatus] = useState<"idle" | "saving" | "ok" | "error">("idle");
  const [message, setMessage] = useState("");

  const reset = useCallback(() => {
    setClient(null);
    setAmount("");
    setDescription("");
    setDate("");
    setDocNumber("");
    setCounterpartyAfm("");
    setScannedVat(null);
    setScanNote("");
    setDuplicate(null);
    setStatus("idle");
    setMessage("");
  }, []);

  // Escape closes, and body scroll is locked while the modal is open.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [open]);

  // Warn while the invoice is still being typed rather than only on submit.
  // Debounced, and only once there is something to key on.
  useEffect(() => {
    if (!open || !docNumber.trim()) {
      setDuplicate(null);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      checkTransactionDuplicate({
        doc_number: docNumber.trim(),
        counterparty_afm: counterpartyAfm.trim() || undefined,
        client: client?.name?.trim() || undefined,
        date: date || undefined,
      })
        .then((r) => {
          if (!cancelled) setDuplicate(r.duplicate);
        })
        // A failed pre-check must not block the form: the server repeats the
        // check on save, which is the one that actually guards the data.
        .catch(() => undefined);
    }, 400);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [open, docNumber, counterpartyAfm, date, client]);

  function applyScan(result: ScanResult) {
    const e = result.extracted;
    if (e.total_amount !== null) setAmount(String(e.total_amount).replace(".", ","));
    if (e.vat_rate !== null) setVatRate(e.vat_rate);
    setScannedVat(e.vat_amount);
    if (e.doc_date) setDate(e.doc_date);
    if (e.doc_number) setDocNumber(e.doc_number);
    if (e.counterparty_afm) setCounterpartyAfm(e.counterparty_afm);
    // An issuer already on file is selected by id, so the row cannot be
    // mis-filed onto a near-duplicate name; otherwise the name is offered and
    // the client is created on save.
    if (result.client_match) {
      setClient({ id: result.client_match.id, name: result.client_match.name });
    } else if (e.counterparty_name) {
      setClient({ name: e.counterparty_name });
    }
    if (!description.trim() && e.document_type) setDescription(e.document_type);
    setDuplicate(result.duplicate);
    setStatus("idle");
    setMessage("");
    setScanNote(
      [
        e.confidence === "high"
          ? "Τα στοιχεία διαβάστηκαν καθαρά."
          : e.confidence === "medium"
            ? "Ελέγξτε τα στοιχεία — το έγγραφο δεν ήταν απόλυτα ευανάγνωστο."
            : "Χαμηλή αναγνωσιμότητα — ελέγξτε προσεκτικά κάθε πεδίο.",
        e.notes ?? "",
      ]
        .filter(Boolean)
        .join(" "),
    );
  }

  async function save(force: boolean) {
    const parsed = Number(amount.replace(",", "."));
    if (!client?.name.trim() || !parsed || parsed <= 0) {
      setStatus("error");
      setMessage("Συμπληρώστε πελάτη και ποσό > 0.");
      return;
    }
    setStatus("saving");
    setMessage("");
    try {
      await createTransaction({
        client: client.name.trim(),
        // Present only when an existing client was picked; a new name is
        // created server-side from `client`.
        client_id: client.id,
        amount: parsed,
        type,
        vat_rate: vatRate,
        vat_amount: scannedVat ?? undefined,
        date: date || undefined,
        description: description.trim() || undefined,
        doc_number: docNumber.trim() || undefined,
        counterparty_afm: counterpartyAfm.trim() || undefined,
        force,
      });
      setStatus("ok");
      setMessage("Η κίνηση αποθηκεύτηκε.");
      router.refresh();
      setTimeout(() => {
        setOpen(false);
        reset();
      }, 900);
    } catch (err) {
      setStatus("error");
      if (err instanceof DuplicateError) {
        setDuplicate(err.duplicate);
        setMessage(err.message);
        return;
      }
      setMessage(err instanceof Error ? err.message : "Αποτυχία αποθήκευσης.");
    }
  }

  const saving = status === "saving";
  const gross = Number(amount.replace(",", ".")) || 0;
  const isDebt = type === "Χρεωστούμενο";
  // A duplicate is a warning, not a wall: the second save carries force.
  const confirming = duplicate !== null;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-indigo-500"
      >
        <Plus className="h-4 w-4" />
        Νέα Κίνηση
      </button>

      {open ? (
        <div
          className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-4 sm:items-center"
          role="dialog"
          aria-modal="true"
          aria-label="Νέα κίνηση"
        >
          <button
            type="button"
            aria-label="Κλείσιμο"
            onClick={() => setOpen(false)}
            className="fixed inset-0 bg-slate-900/50 backdrop-blur-[2px]"
          />

          <div className="relative my-auto w-full max-w-lg rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900">
            <div className="flex items-center justify-between border-b border-slate-200 p-5 dark:border-slate-800">
              <h2 className="text-base font-semibold text-slate-900 dark:text-white">
                Νέα Κίνηση
              </h2>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-200"
                aria-label="Κλείσιμο"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <form
              onSubmit={(e) => {
                e.preventDefault();
                void save(confirming);
              }}
              className="space-y-3 p-5"
            >
              {scanEnabled ? (
                <InvoiceScanner onScanned={applyScan} disabled={saving} />
              ) : null}

              {scanNote ? (
                <p className="flex items-start gap-1.5 rounded-lg bg-indigo-50 p-2 text-[11px] text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300">
                  <Sparkles className="mt-0.5 h-3 w-3 shrink-0" />
                  {scanNote}
                </p>
              ) : null}

              <div>
                <label className={labelCls}>Πελάτης / Συναλλασσόμενος</label>
                <ClientPicker
                  value={client}
                  onChange={setClient}
                  inputClassName={field}
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={labelCls} htmlFor="qa-amount">
                    Ποσό (με Φ.Π.Α.)
                  </label>
                  <input
                    id="qa-amount"
                    className={field}
                    value={amount}
                    onChange={(e) => {
                      setAmount(e.target.value);
                      setScannedVat(null);
                    }}
                    placeholder="150,00"
                    inputMode="decimal"
                  />
                </div>
                <div>
                  <label className={labelCls} htmlFor="qa-vat">
                    Συντ. Φ.Π.Α.
                  </label>
                  <select
                    id="qa-vat"
                    className={field}
                    value={vatRate}
                    onChange={(e) => {
                      setVatRate(Number(e.target.value));
                      setScannedVat(null);
                    }}
                  >
                    {vatRates.map((r) => (
                      <option key={r.value} value={r.value}>
                        {r.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {!isDebt && gross > 0 ? (
                <p className="text-[11px] text-slate-500 dark:text-slate-400">
                  Φ.Π.Α.{" "}
                  <span className="font-semibold tabular-nums text-violet-600 dark:text-violet-400">
                    {money(vatPreview(gross, vatRate, scannedVat))}
                  </span>
                  {scannedVat !== null ? " (από το παραστατικό)" : ""} · Καθαρή αξία{" "}
                  <span className="font-semibold tabular-nums">
                    {money(gross - vatPreview(gross, vatRate, scannedVat))}
                  </span>
                </p>
              ) : null}

              <div>
                <label className={labelCls}>Τύπος</label>
                <div className="grid grid-cols-3 gap-2">
                  {TYPES.map((t) => (
                    <button
                      key={t.value}
                      type="button"
                      onClick={() => setType(t.value)}
                      className={clsx(
                        "rounded-lg border px-2 py-1.5 text-xs font-medium transition",
                        type === t.value
                          ? "border-indigo-500 bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300"
                          : "border-slate-300 text-slate-600 hover:border-slate-400 dark:border-slate-700 dark:text-slate-300",
                      )}
                    >
                      {t.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={labelCls} htmlFor="qa-doc">
                    Αρ. Παραστατικού
                  </label>
                  <input
                    id="qa-doc"
                    className={field}
                    value={docNumber}
                    onChange={(e) => setDocNumber(e.target.value)}
                    placeholder="π.χ. ΤΠΥ-1042"
                  />
                </div>
                <div>
                  <label className={labelCls} htmlFor="qa-afm">
                    Α.Φ.Μ. εκδότη
                  </label>
                  <input
                    id="qa-afm"
                    className={field}
                    value={counterpartyAfm}
                    onChange={(e) => setCounterpartyAfm(e.target.value)}
                    placeholder="123456789"
                    inputMode="numeric"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={labelCls} htmlFor="qa-date">
                    Ημερομηνία
                  </label>
                  <input
                    id="qa-date"
                    type="date"
                    className={field}
                    value={date}
                    onChange={(e) => setDate(e.target.value)}
                  />
                </div>
                <div>
                  <label className={labelCls} htmlFor="qa-desc">
                    Περιγραφή
                  </label>
                  <input
                    id="qa-desc"
                    className={field}
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                  />
                </div>
              </div>

              {duplicate ? <DuplicateWarning duplicate={duplicate} /> : null}

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

              <div className="flex items-center justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={reset}
                  disabled={saving}
                  className="rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium text-slate-600 transition hover:bg-slate-50 disabled:opacity-60 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                >
                  Καθαρισμός
                </button>
                <button
                  type="submit"
                  disabled={saving || status === "ok"}
                  className={clsx(
                    "inline-flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-semibold text-white transition disabled:opacity-60",
                    confirming
                      ? "bg-amber-600 hover:bg-amber-500"
                      : "bg-indigo-600 hover:bg-indigo-500",
                  )}
                >
                  {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                  {confirming ? "Αποθήκευση παρόλα αυτά" : "Αποθήκευση"}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </>
  );
}
