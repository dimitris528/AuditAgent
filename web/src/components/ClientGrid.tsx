"use client";

import { useState } from "react";
import { Users } from "lucide-react";
import type { ClientData, PeriodInfo } from "@/lib/types";
import { ClientCard } from "./ClientCard";
import { ClientDrawer } from "./ClientDrawer";

interface Props {
  clients: ClientData[];
  period: Pick<PeriodInfo, "year" | "quarter" | "month">;
}

export function ClientGrid({ clients, period }: Props) {
  const [openId, setOpenId] = useState<number | null>(null);

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
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {clients.map((client) => (
            <ClientCard
              key={client.id ?? client.key}
              client={client}
              // Client ids are numeric in PostgreSQL but serialised as strings
              // in the finance record shape; parse before opening the drawer.
              onOpen={client.id ? () => setOpenId(Number(client.id)) : undefined}
            />
          ))}
        </div>
      )}

      <ClientDrawer
        clientId={openId}
        period={period}
        onClose={() => setOpenId(null)}
      />
    </section>
  );
}
