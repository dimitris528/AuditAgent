"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  AlertTriangle,
  Archive,
  ArchiveRestore,
  Building2,
  CheckCircle2,
  HandCoins,
  History,
  Loader2,
  Receipt,
  Save,
  X,
} from "lucide-react";
import { getClientDetail, updateClient } from "@/lib/api";
import type {
  ClientDetailPayload,
  PeriodInfo,
  TransactionRow,
} from "@/lib/types";
import { money, moneyAbs } from "@/lib/format";
import { clsx } from "@/lib/clsx";
import { Badge } from "./ui/Badge";
import { DebtSettlementModal } from "./DebtSettlementModal";

interface Props {
  clientId: number | null;
  period: Pick<PeriodInfo, "year" | "quarter" | "month">;
  vatRates: { value: number; label: string }[];
  onClose: () => void;
}

type Tab = "info" | "transactions";

const field =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 dark:border-slate-700 dark:bg-slate-800 dark:text-white";
const labelCls = "mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400";

function SummaryTile({
  label,
  value,
  valueClass,
  sub,
}: {
  label: string;
  value: string;
  valueClass?: string;
  sub?: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-slate-100 bg-slate-50/60 p-3 dark:border-slate-800 dark:bg-slate-800/40">
      <div className="truncate text-[11px] font-medium text-slate-500 dark:text-slate-400">
        {label}
      </div>
      <div
        className={clsx(
          "mt-0.5 truncate text-[15px] font-bold tabular-nums text-slate-900 dark:text-white",
          valueClass,
        )}
      >
        {value}
      </div>
      {sub ? <div className="mt-0.5 truncate text-[11px]">{sub}</div> : null}
    </div>
  );
}

export function ClientDrawer({ clientId, period, vatRates, onClose }: Props) {
  const router = useRouter();
  const [data, setData] = useState<ClientDetailPayload | null>(null);
  const [tab, setTab] = useState<Tab>("info");
  const [loadError, setLoadError] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [messageTone, setMessageTone] = useState<"ok" | "error">("ok");
  // The debt row whose settlement modal is open.
  const [settling, setSettling] = useState<TransactionRow | null>(null);

  // Editable fields, seeded from the loaded client.
  const [name, setName] = useState("");
  const [afm, setAfm] = useState("");
  const [contact, setContact] = useState("");
  const [notes, setNotes] = useState("");

  const load = useCallback(async () => {
    if (clientId === null) return;
    setData(null);
    setLoadError("");
    try {
      const payload = await getClientDetail(clientId, period);
      setData(payload);
      setName(payload.client.name);
      setAfm(payload.client.afm ?? "");
      setContact(payload.client.contact ?? "");
      setNotes(payload.client.notes ?? "");
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Αποτυχία φόρτωσης.");
    }
  }, [clientId, period]);

  useEffect(() => {
    void load();
  }, [load]);

  // Escape closes, and body scroll is locked while the drawer is open.
  useEffect(() => {
    if (clientId === null) return;
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
  }, [clientId, onClose]);

  if (clientId === null) return null;

  async function save() {
    if (!data) return;
    if (!name.trim()) {
      setMessageTone("error");
      setMessage("Το όνομα δεν μπορεί να είναι κενό.");
      return;
    }
    setSaving(true);
    setMessage("");
    try {
      await updateClient(data.client.id, {
        name: name.trim(),
        afm: afm.trim(),
        contact: contact.trim(),
        notes: notes.trim(),
      });
      setMessageTone("ok");
      setMessage("Οι αλλαγές αποθηκεύτηκαν.");
      await load();
      // The dashboard cards are server-rendered, so refresh to pick up a
      // rename or any figure that moved with it.
      router.refresh();
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : "Αποτυχία αποθήκευσης.");
    } finally {
      setSaving(false);
    }
  }

  async function toggleArchive() {
    if (!data) return;
    setSaving(true);
    setMessage("");
    try {
      const next = !data.client.archived;
      await updateClient(data.client.id, { archived: next });
      setMessageTone("ok");
      setMessage(
        next ? "Ο πελάτης αρχειοθετήθηκε." : "Ο πελάτης επανενεργοποιήθηκε.",
      );
      await load();
      router.refresh();
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : "Αποτυχία αποθήκευσης.");
    } finally {
      setSaving(false);
    }
  }

  const s = data?.summary;
  const vatRefund = (s?.net_vat ?? 0) < 0;

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true">
      {/* Backdrop */}
      <button
        type="button"
        aria-label="Κλείσιμο"
        onClick={onClose}
        className="absolute inset-0 bg-slate-900/40 backdrop-blur-[2px]"
      />

      <aside className="relative flex h-full w-full max-w-lg flex-col overflow-hidden border-l border-slate-200 bg-white shadow-2xl dark:border-slate-800 dark:bg-slate-900">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 border-b border-slate-200 p-5 dark:border-slate-800">
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600 dark:bg-indigo-500/10 dark:text-indigo-400">
              <Building2 className="h-5 w-5" />
            </span>
            <div className="min-w-0">
              <h2 className="truncate text-base font-semibold text-slate-900 dark:text-white">
                {data?.client.name ?? "Φόρτωση…"}
              </h2>
              {data?.client.archived ? (
                <Badge tone="warning" className="mt-0.5">
                  Αρχειοθετημένος
                </Badge>
              ) : null}
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

        {/* Tabs */}
        <div className="flex gap-1 border-b border-slate-200 px-5 dark:border-slate-800">
          {([
            ["info", "Στοιχεία"],
            ["transactions", `Κινήσεις${s ? ` (${s.count})` : ""}`],
          ] as [Tab, string][]).map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => setTab(key)}
              className={clsx(
                "-mb-px border-b-2 px-3 py-2.5 text-sm font-medium transition",
                tab === key
                  ? "border-indigo-600 text-indigo-600 dark:border-indigo-400 dark:text-indigo-400"
                  : "border-transparent text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200",
              )}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-y-auto p-5">
          {loadError ? (
            <div className="flex items-center gap-1.5 text-sm text-rose-600 dark:text-rose-400">
              <AlertCircle className="h-4 w-4" />
              {loadError}
            </div>
          ) : !data ? (
            <div className="flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
              <Loader2 className="h-4 w-4 animate-spin" /> Φόρτωση…
            </div>
          ) : (
            <>
              {/* Financial summary — shown on both tabs, it is the point of the drawer */}
              <div className="mb-5 grid grid-cols-2 gap-2.5">
                <SummaryTile
                  label="Έσοδα"
                  value={money(s!.gross_rev)}
                  valueClass="text-emerald-600 dark:text-emerald-400"
                  sub={
                    <span className="text-slate-500 dark:text-slate-400">
                      Καθαρά {money(s!.net_rev)}
                    </span>
                  }
                />
                <SummaryTile
                  label="Έξοδα"
                  value={money(s!.gross_exp)}
                  valueClass="text-rose-600 dark:text-rose-400"
                  sub={
                    <span className="text-slate-500 dark:text-slate-400">
                      Καθαρά {money(s!.net_exp)}
                    </span>
                  }
                />
                <SummaryTile
                  label="Καθαρό Φ.Π.Α."
                  value={moneyAbs(s!.net_vat)}
                  valueClass="text-violet-600 dark:text-violet-400"
                  sub={
                    <Badge tone={vatRefund ? "success" : "vat"}>
                      {vatRefund
                        ? "Προς επιστροφή"
                        : s!.net_vat > 0
                          ? "Προς απόδοση"
                          : "Μηδενικό"}
                    </Badge>
                  }
                />
                <SummaryTile
                  label="Υπόλοιπο (Καθαρό Αποτέλεσμα)"
                  value={money(s!.net_profit)}
                  valueClass={
                    s!.net_profit >= 0
                      ? "text-emerald-600 dark:text-emerald-400"
                      : "text-rose-600 dark:text-rose-400"
                  }
                  sub={
                    s!.debt > 0 ? (
                      <span className="text-amber-600 dark:text-amber-400">
                        Χρεωστούμενα {money(s!.debt)}
                      </span>
                    ) : null
                  }
                />
              </div>

              {tab === "info" ? (
                <div className="space-y-3">
                  <div>
                    <label className={labelCls} htmlFor="cd-name">
                      Επωνυμία
                    </label>
                    <input
                      id="cd-name"
                      className={field}
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className={labelCls} htmlFor="cd-afm">
                        Α.Φ.Μ.
                      </label>
                      <input
                        id="cd-afm"
                        className={field}
                        value={afm}
                        onChange={(e) => setAfm(e.target.value)}
                        inputMode="numeric"
                        placeholder="π.χ. 123456789"
                      />
                    </div>
                    <div>
                      <label className={labelCls} htmlFor="cd-contact">
                        Επικοινωνία
                      </label>
                      <input
                        id="cd-contact"
                        className={field}
                        value={contact}
                        onChange={(e) => setContact(e.target.value)}
                        placeholder="Email ή τηλέφωνο"
                      />
                    </div>
                  </div>
                  <div>
                    <label className={labelCls} htmlFor="cd-notes">
                      Σημειώσεις
                    </label>
                    <textarea
                      id="cd-notes"
                      className={clsx(field, "min-h-[90px] resize-y")}
                      value={notes}
                      onChange={(e) => setNotes(e.target.value)}
                    />
                  </div>

                  {message ? (
                    <div
                      className={clsx(
                        "flex items-center gap-1.5 text-xs",
                        messageTone === "ok"
                          ? "text-emerald-600 dark:text-emerald-400"
                          : "text-rose-600 dark:text-rose-400",
                      )}
                    >
                      {messageTone === "ok" ? (
                        <CheckCircle2 className="h-4 w-4" />
                      ) : (
                        <AlertCircle className="h-4 w-4" />
                      )}
                      {message}
                    </div>
                  ) : null}
                </div>
              ) : (
                <div>
                  {data.transactions.length === 0 ? (
                    <div className="rounded-xl border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">
                      Καμία κίνηση για αυτόν τον πελάτη στην επιλεγμένη περίοδο.
                    </div>
                  ) : (
                    <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                      {data.transactions.map((t) => (
                        <li key={t.id} className="py-2.5">
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <div className="flex items-center gap-1.5">
                                <Receipt className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                                <span className="truncate text-sm text-slate-800 dark:text-slate-100">
                                  {t.description?.trim() || t.type || "Κίνηση"}
                                </span>
                              </div>
                              <div className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">
                                {t.date ?? "—"}
                                {t.doc_type ? ` · ${t.doc_type}` : ""}
                                {t.doc_number ? ` · ${t.doc_number}` : ""}
                                {t.vat_amount != null
                                  ? ` · Φ.Π.Α. ${money(t.vat_amount)}`
                                  : ""}
                              </div>
                              {/* Red marking follows the debt down to the row
                                  it belongs to, not just the dashboard. */}
                              {t.is_debt && t.status === "overdue" ? (
                                <div className="mt-1 inline-flex items-center gap-1 rounded-full bg-rose-100 px-2 py-0.5 text-[10px] font-semibold text-rose-700 dark:bg-rose-500/15 dark:text-rose-300">
                                  <AlertTriangle className="h-3 w-3" />
                                  Ληξιπρόθεσμο {t.days_overdue} ημ.
                                  {t.due_date ? ` (λήξη ${t.due_date})` : ""}
                                </div>
                              ) : t.is_debt && t.due_date ? (
                                <div className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">
                                  Λήξη {t.due_date}
                                </div>
                              ) : null}
                              {/* Only shown once something has been paid: on an
                                  untouched debt the remaining amount IS the
                                  original, and repeating it reads as noise. */}
                              {t.is_debt && (t.paid ?? 0) > 0 ? (
                                <div className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">
                                  Από {money(t.original ?? 0)} · πληρωμένα{" "}
                                  <span className="font-semibold text-emerald-600 dark:text-emerald-400">
                                    {money(t.paid ?? 0)}
                                  </span>
                                </div>
                              ) : null}
                            </div>
                            <div className="flex shrink-0 flex-col items-end gap-1.5">
                              <div
                                className={clsx(
                                  "text-sm font-semibold tabular-nums",
                                  t.is_debt
                                    ? "text-amber-600 dark:text-amber-400"
                                    : t.is_revenue
                                      ? "text-emerald-600 dark:text-emerald-400"
                                      : "text-rose-600 dark:text-rose-400",
                                )}
                              >
                                {/* Amounts are signed in storage; the UI
                                    convention is magnitude plus colour. */}
                                {moneyAbs(t.amount)}
                              </div>
                              {t.is_debt && t.id ? (
                                <button
                                  type="button"
                                  onClick={() => setSettling(t)}
                                  className="inline-flex items-center gap-1 rounded-lg border border-emerald-300 px-2 py-1 text-[11px] font-semibold text-emerald-700 transition hover:bg-emerald-50 dark:border-emerald-500/40 dark:text-emerald-400 dark:hover:bg-emerald-500/10"
                                >
                                  <HandCoins className="h-3 w-3" />
                                  Εξόφληση
                                </button>
                              ) : null}
                            </div>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}

                  {data.payments.length > 0 ? (
                    <div className="mt-5 border-t border-slate-200 pt-4 dark:border-slate-800">
                      <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-slate-500 dark:text-slate-400">
                        <History className="h-3.5 w-3.5" />
                        Ιστορικό Εξοφλήσεων ({data.payments.length})
                      </h3>
                      <ul className="space-y-1.5">
                        {data.payments.map((p) => (
                          <li
                            key={p.id}
                            className="flex items-center justify-between gap-3 text-xs"
                          >
                            <span className="min-w-0 truncate text-slate-600 dark:text-slate-300">
                              {p.paid_date ?? "—"}
                              {p.note ? ` · ${p.note}` : ""}
                              {p.kind === "full" ? " · πλήρης" : " · μερική"}
                            </span>
                            <span className="shrink-0 tabular-nums">
                              <span className="font-semibold text-emerald-600 dark:text-emerald-400">
                                {money(p.amount)}
                              </span>
                              <span className="ml-2 text-slate-400">
                                υπόλοιπο {money(p.remaining)}
                              </span>
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer actions */}
        {data ? (
          <div className="flex items-center justify-between gap-3 border-t border-slate-200 p-4 dark:border-slate-800">
            <button
              type="button"
              onClick={toggleArchive}
              disabled={saving}
              className={clsx(
                "inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-sm font-medium transition disabled:opacity-60",
                data.client.archived
                  ? "border-emerald-300 text-emerald-700 hover:bg-emerald-50 dark:border-emerald-500/40 dark:text-emerald-400 dark:hover:bg-emerald-500/10"
                  : "border-slate-300 text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800",
              )}
            >
              {data.client.archived ? (
                <>
                  <ArchiveRestore className="h-4 w-4" /> Επαναφορά
                </>
              ) : (
                <>
                  <Archive className="h-4 w-4" /> Αρχειοθέτηση
                </>
              )}
            </button>

            <button
              type="button"
              onClick={save}
              disabled={saving}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-indigo-500 disabled:opacity-60"
            >
              {saving ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Save className="h-4 w-4" />
              )}
              Αποθήκευση
            </button>
          </div>
        ) : null}
      </aside>

      {settling ? (
        <DebtSettlementModal
          debt={settling}
          // Transaction ids are numeric in PostgreSQL but serialised as strings
          // in the finance record shape.
          history={data?.payments.filter(
            (p) => String(p.debt_id) === settling.id,
          ) ?? []}
          vatRates={vatRates}
          onClose={() => setSettling(null)}
          onSettled={() => {
            // Reload the drawer for the new balance and history, and refresh
            // the server-rendered cards behind it — a settlement moves the
            // debt KPI and that client's revenue at the same time.
            void load();
            router.refresh();
          }}
        />
      ) : null}
    </div>
  );
}
