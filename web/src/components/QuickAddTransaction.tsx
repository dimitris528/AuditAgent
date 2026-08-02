"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Plus, X, Loader2, CheckCircle2, AlertCircle } from "lucide-react";
import { createTransaction } from "@/lib/api";
import { clsx } from "@/lib/clsx";

interface Props {
  vatRates: { value: number; label: string }[];
  defaultVatRate: number;
}

const TYPES = [
  { value: "Έσοδο", label: "Έσοδο" },
  { value: "Έξοδο", label: "Έξοδο" },
  { value: "Χρεωστούμενο", label: "Χρεωστούμενο" },
];

export function QuickAddTransaction({ vatRates, defaultVatRate }: Props) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [client, setClient] = useState("");
  const [amount, setAmount] = useState("");
  const [type, setType] = useState("Έσοδο");
  const [vatRate, setVatRate] = useState(defaultVatRate);
  const [description, setDescription] = useState("");
  const [status, setStatus] = useState<"idle" | "saving" | "ok" | "error">("idle");
  const [message, setMessage] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const parsed = Number(amount.replace(",", "."));
    if (!client.trim() || !parsed || parsed <= 0) {
      setStatus("error");
      setMessage("Συμπληρώστε πελάτη και ποσό > 0.");
      return;
    }
    setStatus("saving");
    setMessage("");
    try {
      await createTransaction({
        client: client.trim(),
        amount: parsed,
        type,
        vat_rate: vatRate,
        description: description.trim() || undefined,
      });
      setStatus("ok");
      setMessage("Η κίνηση αποθηκεύτηκε.");
      setClient("");
      setAmount("");
      setDescription("");
      router.refresh();
      setTimeout(() => setOpen(false), 900);
    } catch (err) {
      setStatus("error");
      setMessage(err instanceof Error ? err.message : "Αποτυχία αποθήκευσης.");
    }
  }

  const field =
    "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 dark:border-slate-700 dark:bg-slate-800 dark:text-white";

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-indigo-500"
      >
        {open ? <X className="h-4 w-4" /> : <Plus className="h-4 w-4" />}
        Νέα Κίνηση
      </button>

      {open ? (
        <div className="absolute right-0 z-20 mt-2 w-[min(92vw,22rem)] rounded-2xl border border-slate-200 bg-white p-4 shadow-xl dark:border-slate-700 dark:bg-slate-900">
          <form onSubmit={submit} className="space-y-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">
                Πελάτης
              </label>
              <input className={field} value={client} onChange={(e) => setClient(e.target.value)} placeholder="π.χ. Παπαδόπουλος Α.Ε." />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">
                  Ποσό (με Φ.Π.Α.)
                </label>
                <input className={field} value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="150,00" inputMode="decimal" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">
                  Συντ. Φ.Π.Α.
                </label>
                <select className={field} value={vatRate} onChange={(e) => setVatRate(Number(e.target.value))}>
                  {vatRates.map((r) => (
                    <option key={r.value} value={r.value}>{r.label}</option>
                  ))}
                </select>
              </div>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">
                Τύπος
              </label>
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
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">
                Περιγραφή (προαιρετικό)
              </label>
              <input className={field} value={description} onChange={(e) => setDescription(e.target.value)} />
            </div>

            {message ? (
              <div
                className={clsx(
                  "flex items-center gap-1.5 text-xs",
                  status === "ok"
                    ? "text-emerald-600 dark:text-emerald-400"
                    : "text-rose-600 dark:text-rose-400",
                )}
              >
                {status === "ok" ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
                {message}
              </div>
            ) : null}

            <button
              type="submit"
              disabled={status === "saving"}
              className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-indigo-500 disabled:opacity-60"
            >
              {status === "saving" ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Αποθήκευση
            </button>
          </form>
        </div>
      ) : null}
    </div>
  );
}
