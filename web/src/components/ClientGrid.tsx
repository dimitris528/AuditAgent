"use client";

import { useMemo } from "react";
import { Users } from "lucide-react";
import type { ClientData, DebtAlerts } from "@/lib/types";
import { ClientCard } from "./ClientCard";
import { useClientDrawer } from "./ClientDrawerProvider";

interface Props {
  clients: ClientData[];
  /** Used only to mark cards whose client is overdue. */
  alerts: DebtAlerts;
  /** True when the grid sits in the dashboard's narrow right-hand rail: one
   *  card per row, and the cards themselves render compactly. Without it the
   *  viewport-based column classes below fire on a wide screen even though the
   *  container is ~370px, and the cards collapse. */
  compact?: boolean;
}

export function ClientGrid({ clients, alerts, compact = false }: Props) {
  const { openClient } = useClientDrawer();

  // Both sides key on finance._client_key, so the card and the alert row
  // always agree about which client is which.
  const overdue = useMemo(() => {
    const map = new Map<string, number>();
    for (const c of alerts.clients) {
      if (c.status === "overdue") map.set(c.key, c.max_days_overdue);
    }
    return map;
  }, [alerts]);

  return (
    <section>
      <div className="mb-4 flex items-center gap-2">
        <Users className="h-4 w-4 text-slate-400 dark:text-slate-500" />
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          Ενεργοί Πελάτες
        </h2>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
          {clients.length}
        </span>
      </div>

      {clients.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-white/50 p-10 text-center text-sm text-slate-500 dark:border-slate-700 dark:bg-slate-900/50 dark:text-slate-400">
          Δεν υπάρχουν ενεργοί πελάτες ακόμη.
        </div>
      ) : (
        <div
          className={
            compact
              ? "grid grid-cols-1 gap-3"
              : "grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3"
          }
        >
          {clients.map((client) => (
            <ClientCard
              key={client.id ?? client.key}
              client={client}
              daysOverdue={overdue.get(client.key)}
              // Client ids are numeric in PostgreSQL but serialised as strings
              // in the finance record shape; parse before opening the drawer.
              compact={compact}
              onOpen={
                client.id ? () => openClient(Number(client.id)) : undefined
              }
            />
          ))}
        </div>
      )}
    </section>
  );
}
