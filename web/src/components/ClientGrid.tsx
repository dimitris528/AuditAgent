"use client";

import { useMemo, useState } from "react";
import { Search, Users, X } from "lucide-react";
import type { ClientData, DebtAlerts } from "@/lib/types";
import { searchHaystack, searchKey } from "@/lib/text";
import { clsx } from "@/lib/clsx";
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
  const [query, setQuery] = useState("");

  // Both sides key on finance._client_key, so the card and the alert row
  // always agree about which client is which.
  const overdue = useMemo(() => {
    const map = new Map<string, number>();
    for (const c of alerts.clients) {
      if (c.status === "overdue") map.set(c.key, c.max_days_overdue);
    }
    return map;
  }, [alerts]);

  // Client-side, like the transactions table: the cards are already in the
  // dashboard payload, so this responds on the keystroke and costs no request.
  const needle = useMemo(() => searchKey(query), [query]);
  const visible = useMemo(() => {
    if (!needle) return clients;
    return clients.filter((c) =>
      searchHaystack([c.name, c.afm]).includes(needle),
    );
  }, [clients, needle]);

  return (
    <section>
      {/* Title + search travel together, and in the rail they STICK to the top
          of its scroll area: the rail is a bounded scroller (see the dashboard
          page), so an ordinary header scrolls out of reach exactly when a long
          client list is the reason you wanted the search box. The blurred
          background is what keeps the cards from showing through it. */}
      <div
        className={clsx(
          compact &&
            "sticky -top-1 z-10 -mx-1 -mt-1 bg-slate-50/85 px-1 pb-2 pt-1 backdrop-blur dark:bg-slate-950/85",
        )}
      >
        <div className="mb-3 flex items-center gap-2">
          <Users className="h-4 w-4 text-slate-400 dark:text-slate-500" />
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
            Ενεργοί Πελάτες
          </h2>
          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
            {/* While filtering, the badge reads "3 / 27" — otherwise the count
                silently changing under a search looks like missing data. */}
            {visible.length}
            {visible.length !== clients.length ? ` / ${clients.length}` : ""}
          </span>
        </div>

        {/* Hidden when there is nothing to search through. */}
        {clients.length > 0 ? (
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Αναζήτηση πελάτη ή ΑΦΜ…"
              aria-label="Αναζήτηση πελάτη ή ΑΦΜ"
              className="w-full rounded-lg border border-slate-200 bg-white py-1.5 pl-8 pr-7 text-xs text-slate-900 outline-none transition focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 dark:border-slate-700 dark:bg-slate-800 dark:text-white"
            />
            {query ? (
              <button
                type="button"
                onClick={() => setQuery("")}
                aria-label="Καθαρισμός αναζήτησης"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 transition hover:text-slate-600 dark:hover:text-slate-200"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
      {visible.length === 0 ? (
        <div className="mt-3 rounded-2xl border border-dashed border-slate-300 bg-white/50 p-10 text-center text-sm text-slate-500 dark:border-slate-700 dark:bg-slate-900/50 dark:text-slate-400">
          {clients.length === 0
            ? "Δεν υπάρχουν ενεργοί πελάτες ακόμη."
            : "Κανένας πελάτης δεν ταιριάζει με την αναζήτηση."}
        </div>
      ) : (
        // No scroll container of its own: in the rail the PAGE already gives
        // this list a bounded, self-scrolling parent, and a second scrollbar
        // nested inside the first is worse than either alone.
        <div
          className={clsx(
            "mt-3 grid grid-cols-1",
            compact ? "gap-3" : "gap-4 md:grid-cols-2 xl:grid-cols-3",
          )}
        >
          {visible.map((client) => (
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
