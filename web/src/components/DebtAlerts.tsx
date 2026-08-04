"use client";

import { useState } from "react";
import { AlertTriangle, ChevronDown, Clock, HandCoins } from "lucide-react";
import type { DebtAlertClient, DebtAlerts as DebtAlertsData, DebtStatus } from "@/lib/types";
import { money } from "@/lib/format";
import { clsx } from "@/lib/clsx";
import { useClientDrawer } from "./ClientDrawerProvider";

const STATUS_LABEL: Record<DebtStatus, string> = {
  overdue: "Ληξιπρόθεσμο",
  due_soon: "Λήγει σύντομα",
  current: "Σε ισχύ",
  unknown: "Χωρίς ημερομηνία",
};

/** Red is reserved for genuinely overdue money — if everything is amber,
 *  nothing reads as urgent. */
const STATUS_STYLE: Record<DebtStatus, string> = {
  overdue:
    "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300",
  due_soon:
    "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
  current:
    "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
  unknown:
    "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400",
};

const INITIAL_ROWS = 5;

function StatusBadge({ client }: { client: DebtAlertClient }) {
  const label =
    client.status === "overdue"
      ? `${STATUS_LABEL.overdue} · ${client.max_days_overdue} ημ.`
      : STATUS_LABEL[client.status];
  return (
    <span
      className={clsx(
        "shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold",
        STATUS_STYLE[client.status],
      )}
    >
      {label}
    </span>
  );
}

function ClientRow({ client }: { client: DebtAlertClient }) {
  const { openClient } = useClientDrawer();
  const overdue = client.status === "overdue";

  return (
    <li
      className={clsx(
        "flex items-center justify-between gap-3 rounded-xl border px-3 py-2.5",
        "transition duration-200 hover:-translate-y-px hover:shadow-sm motion-reduce:transform-none",
        overdue
          ? "border-rose-200 bg-rose-50/60 hover:border-rose-300 dark:border-rose-500/30 dark:bg-rose-500/5 dark:hover:border-rose-500/50"
          : "border-slate-200 bg-white hover:border-indigo-200 dark:border-slate-800 dark:bg-slate-900 dark:hover:border-indigo-500/30",
      )}
    >
      <div className="flex min-w-0 items-center gap-2">
        {overdue ? (
          <span
            aria-hidden
            className="h-2 w-2 shrink-0 rounded-full bg-rose-500"
          />
        ) : null}
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">
              {client.name}
            </span>
            <StatusBadge client={client} />
          </div>
          <div className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">
            {client.count} {client.count === 1 ? "χρεωστούμενο" : "χρεωστούμενα"}
            {client.overdue > 0 && client.overdue < client.total
              ? ` · ${money(client.overdue)} ληξιπρόθεσμα`
              : ""}
          </div>
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-3">
        <span
          className={clsx(
            "text-sm font-bold tabular-nums",
            overdue
              ? "text-rose-600 dark:text-rose-400"
              : "text-amber-600 dark:text-amber-400",
          )}
        >
          {money(client.total)}
        </span>
        {/* No id means the debt names a client with no row — nothing to open. */}
        {client.id ? (
          <button
            type="button"
            onClick={() => openClient(Number(client.id))}
            className="inline-flex items-center gap-1 rounded-lg border border-emerald-300 px-2 py-1 text-[11px] font-semibold text-emerald-700 transition hover:bg-emerald-50 dark:border-emerald-500/40 dark:text-emerald-400 dark:hover:bg-emerald-500/10"
          >
            <HandCoins className="h-3 w-3" />
            Εξόφληση
          </button>
        ) : null}
      </div>
    </li>
  );
}

/**
 * Ληξιπρόθεσμα / Ανεξόφλητα — who owes money and how late they are.
 *
 * Renders nothing when there is nothing outstanding: an alert section with no
 * alert in it is noise, and its absence is itself the signal.
 */
export function DebtAlerts({ alerts }: { alerts: DebtAlertsData }) {
  const [expanded, setExpanded] = useState(false);

  if (alerts.total <= 0) return null;

  const hasOverdue = alerts.overdue_total > 0;
  const shown = expanded
    ? alerts.clients
    : alerts.clients.slice(0, INITIAL_ROWS);
  const hidden = alerts.clients.length - shown.length;
  const agingWithMoney = alerts.aging.filter((b) => b.amount > 0);

  return (
    <section
      className={clsx(
        "rounded-2xl border p-4",
        hasOverdue
          ? "border-rose-300 bg-rose-50/40 dark:border-rose-500/40 dark:bg-rose-500/5"
          : "border-amber-300 bg-amber-50/40 dark:border-amber-500/40 dark:bg-amber-500/5",
      )}
      aria-label="Ανεξόφλητα χρεωστούμενα"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-2.5">
          <span
            className={clsx(
              "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg",
              hasOverdue
                ? "bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-400"
                : "bg-amber-100 text-amber-600 dark:bg-amber-500/15 dark:text-amber-400",
            )}
          >
            {hasOverdue ? (
              <AlertTriangle className="h-5 w-5" />
            ) : (
              <Clock className="h-5 w-5" />
            )}
          </span>
          <div>
            <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
              {hasOverdue
                ? "Ληξιπρόθεσμα Χρεωστούμενα"
                : "Ανεξόφλητα Χρεωστούμενα"}
            </h2>
            <p className="text-xs text-slate-600 dark:text-slate-400">
              {alerts.count} χρεωστούμενα από {alerts.clients_affected}{" "}
              {alerts.clients_affected === 1 ? "πελάτη" : "πελάτες"}
              {hasOverdue
                ? ` · ${alerts.overdue_count} ληξιπρόθεσμα`
                : ""}{" "}
              · όλες οι περίοδοι
            </p>
          </div>
        </div>

        {/* Summary card */}
        <div className="flex gap-4 rounded-xl border border-slate-200 bg-white px-4 py-2.5 dark:border-slate-800 dark:bg-slate-900">
          <div>
            <div className="text-[11px] text-slate-500 dark:text-slate-400">
              Σύνολο ανεξόφλητων
            </div>
            <div className="text-lg font-bold tabular-nums text-amber-600 dark:text-amber-400">
              {money(alerts.total)}
            </div>
          </div>
          {hasOverdue ? (
            <div className="border-l border-slate-200 pl-4 dark:border-slate-800">
              <div className="text-[11px] text-slate-500 dark:text-slate-400">
                Ληξιπρόθεσμα
              </div>
              <div className="text-lg font-bold tabular-nums text-rose-600 dark:text-rose-400">
                {money(alerts.overdue_total)}
              </div>
            </div>
          ) : null}
        </div>
      </div>

      {agingWithMoney.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-600 dark:text-slate-400">
          <span className="font-medium">Ενηλικίωση:</span>
          {agingWithMoney.map((bucket) => (
            <span key={bucket.label}>
              {bucket.label} ημ.{" "}
              <span className="font-semibold tabular-nums text-rose-600 dark:text-rose-400">
                {money(bucket.amount)}
              </span>
            </span>
          ))}
        </div>
      ) : null}

      <ul className="mt-3 space-y-2">
        {shown.map((client) => (
          <ClientRow key={client.key} client={client} />
        ))}
      </ul>

      {hidden > 0 ? (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-slate-600 transition hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"
        >
          <ChevronDown className="h-3.5 w-3.5" />
          Εμφάνιση {hidden} ακόμη
        </button>
      ) : null}
    </section>
  );
}
