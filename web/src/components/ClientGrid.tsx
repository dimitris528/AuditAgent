"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Search, Users, X } from "lucide-react";
import type { ClientData, DebtAlerts } from "@/lib/types";
import { bulkArchiveClients, bulkDeleteClients } from "@/lib/api";
import { searchHaystack, searchKey } from "@/lib/text";
import { useSelection } from "@/lib/useSelection";
import { clsx } from "@/lib/clsx";
import { ClientCard } from "./ClientCard";
import { BulkActionBar } from "./bulk/BulkActionBar";
import { SelectCheckbox } from "./bulk/SelectCheckbox";
import { ImportDataButton } from "./ImportDataModal";
import { useClientDrawer } from "./ClientDrawerProvider";

/** Which clients the panel is showing. Active is the default: the dashboard is
 *  a working view of the book, and a closed client is history. */
type Scope = "active" | "archived" | "all";

const SCOPES: { key: Scope; label: string }[] = [
  { key: "active", label: "Ενεργοί" },
  { key: "archived", label: "Αρχειοθετημένοι" },
  { key: "all", label: "Όλοι" },
];

interface Props {
  /** ACTIVE clients — the list every total on the page is the sum of. */
  clients: ClientData[];
  /** Closed clients. Optional: a caller that has none simply gets no filter,
   *  rather than a set of tabs where two of the three are always empty. */
  archivedClients?: ClientData[];
  /** Used only to mark cards whose client is overdue. */
  alerts: DebtAlerts;
  /** True when the grid sits in the dashboard's narrow right-hand rail: one
   *  card per row, and the cards themselves render compactly. Without it the
   *  viewport-based column classes below fire on a wide screen even though the
   *  container is ~370px, and the cards collapse. */
  compact?: boolean;
}

export function ClientGrid({
  clients,
  archivedClients,
  alerts,
  compact = false,
}: Props) {
  const { openClient } = useClientDrawer();
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<Scope>("active");

  // Both sides key on finance._client_key, so the card and the alert row
  // always agree about which client is which.
  const overdue = useMemo(() => {
    const map = new Map<string, number>();
    for (const c of alerts.clients) {
      if (c.status === "overdue") map.set(c.key, c.max_days_overdue);
    }
    return map;
  }, [alerts]);

  const archived = useMemo(() => archivedClients ?? [], [archivedClients]);
  // Nothing archived means nothing to filter BETWEEN — the tabs would be three
  // buttons, two of which lead to an empty panel.
  const showScopes = archived.length > 0;

  // The pool the search runs over. Archived clients sort after the active ones
  // under "Όλοι": the panel is still primarily about who is live.
  const pool = useMemo(() => {
    if (!showScopes || scope === "active") return clients;
    if (scope === "archived") return archived;
    return [...clients, ...archived];
  }, [clients, archived, scope, showScopes]);

  // Client-side, like the transactions table: the cards are already in the
  // dashboard payload, so this responds on the keystroke and costs no request.
  const needle = useMemo(() => searchKey(query), [query]);
  const visible = useMemo(() => {
    if (!needle) return pool;
    return pool.filter((c) => searchHaystack([c.name, c.afm]).includes(needle));
  }, [pool, needle]);

  // Selection runs over what is ON SCREEN. A client with no id (the legacy
  // name-only path) has nothing to send to a bulk endpoint, so it is not
  // selectable and "select all" cannot silently include it.
  const selectableIds = useMemo(
    () => visible.filter((c) => c.id).map((c) => String(c.id)),
    [visible],
  );
  const selection = useSelection(selectableIds);

  // The archive button flips direction with the filter: under Αρχειοθετημένοι
  // the only sensible bulk action is putting them back, and offering
  // "Αρχειοθέτηση" there would be a button that does nothing.
  const restoring = scope === "archived";

  async function refreshAfter<T extends { message: string }>(result: T): Promise<T> {
    // Refetch rather than splice the row out locally: every KPI, chart and tax
    // bar on this page is derived server-side from the client list, so an
    // optimistic removal would leave the card gone and all the totals around it
    // still counting it — a page that visibly disagrees with itself.
    router.refresh();
    return result;
  }

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
          {/* "Select all" for the grid, in its header — the counterpart of the
              table's header checkbox. Hidden when nothing on screen can be
              selected, rather than shown as a control that does nothing. */}
          {selectableIds.length > 0 ? (
            <SelectCheckbox
              checked={selection.allVisible}
              indeterminate={selection.someVisible}
              onChange={selection.toggleAll}
              label="Επιλογή όλων των εμφανιζόμενων πελατών"
            />
          ) : null}
          <Users className="h-4 w-4 text-slate-400 dark:text-slate-500" />
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
            {/* The tabs qualify the heading once they exist — a panel titled
                "Ενεργοί Πελάτες" listing archived ones contradicts itself. */}
            {showScopes ? "Πελάτες" : "Ενεργοί Πελάτες"}
          </h2>
          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
            {/* While filtering, the badge reads "3 / 27" — otherwise the count
                silently changing under a search looks like missing data. */}
            {visible.length}
            {visible.length !== pool.length ? ` / ${pool.length}` : ""}
          </span>
          {/* Pushed to the far end of the heading row: bulk import is a
              setup-time action, and it must not sit where the eye looks for
              the client count. Compact in the rail, where the whole panel is
              ~370px wide. */}
          <ImportDataButton kind="clients" compact={compact} className="ml-auto" />
        </div>

        {showScopes ? (
          <div
            role="tablist"
            aria-label="Κατάσταση πελατών"
            className="mb-2 flex rounded-lg border border-slate-200 p-0.5 dark:border-slate-700"
          >
            {SCOPES.map((s) => (
              <button
                key={s.key}
                type="button"
                role="tab"
                aria-selected={scope === s.key}
                onClick={() => setScope(s.key)}
                className={clsx(
                  "flex-1 truncate rounded-md px-2 py-1 text-[11px] font-medium transition",
                  scope === s.key
                    ? "bg-indigo-600 text-white"
                    : "text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200",
                )}
              >
                {s.label}
              </button>
            ))}
          </div>
        ) : null}

        {/* Hidden when the selected scope has nothing to search through. */}
        {pool.length > 0 ? (
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
          {pool.length > 0
            ? "Κανένας πελάτης δεν ταιριάζει με την αναζήτηση."
            : scope === "archived"
              ? "Δεν υπάρχουν αρχειοθετημένοι πελάτες."
              : "Δεν υπάρχουν ενεργοί πελάτες ακόμη."}
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
              // Scope-prefixed: under "Όλοι" the two lists are concatenated,
              // and an id-less client (the Airtable path) would otherwise
              // collide with an archived namesake on the name key alone.
              key={`${client.archived ? "a" : "c"}:${client.id ?? client.key}`}
              client={client}
              daysOverdue={overdue.get(client.key)}
              // Client ids are numeric in PostgreSQL but serialised as strings
              // in the finance record shape; parse before opening the drawer.
              compact={compact}
              onOpen={
                client.id ? () => openClient(Number(client.id)) : undefined
              }
              selected={client.id ? selection.isSelected(String(client.id)) : false}
              onToggleSelect={
                client.id ? () => selection.toggle(String(client.id)) : undefined
              }
            />
          ))}
        </div>
      )}

      <BulkActionBar
        scope="clients"
        count={selection.count}
        hiddenSelected={selection.hidden}
        onClear={selection.clear}
        onDelete={async () => {
          const result = await bulkDeleteClients(selection.ids);
          return refreshAfter({
            message: result.message,
            blocked: result.blocked,
          });
        }}
        archiveLabel={restoring ? "Επαναφορά" : "Αρχειοθέτηση"}
        onArchive={async () => {
          const result = await bulkArchiveClients(selection.ids, !restoring);
          return refreshAfter({ message: result.message });
        }}
      />
    </section>
  );
}
