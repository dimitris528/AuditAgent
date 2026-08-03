"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, Loader2, Plus, Search } from "lucide-react";
import { listClients } from "@/lib/api";
import type { ClientDetail } from "@/lib/types";
import { clsx } from "@/lib/clsx";

export interface PickedClient {
  /** Undefined when the user is creating a new client inline. */
  id?: number;
  name: string;
}

interface Props {
  value: PickedClient | null;
  onChange: (value: PickedClient | null) => void;
  inputClassName: string;
  /** Rendered under the field, e.g. a validation message. */
  hint?: React.ReactNode;
}

/**
 * Type-to-filter picker over the tenant's existing clients, with inline
 * creation for a name that does not exist yet.
 *
 * Archived clients are listed but visually marked: you may still need to file a
 * late invoice against one, and silently hiding them would look like data loss.
 */
export function ClientPicker({ value, onChange, inputClassName, hint }: Props) {
  const [clients, setClients] = useState<ClientDetail[] | null>(null);
  const [query, setQuery] = useState(value?.name ?? "");
  const [open, setOpen] = useState(false);
  const [loadError, setLoadError] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);

  // Load once when the picker is first opened, not on mount: the form lives
  // inside a popover that is usually closed.
  useEffect(() => {
    if (!open || clients !== null) return;
    let cancelled = false;
    listClients(true)
      .then((r) => {
        if (!cancelled) setClients(r.clients);
      })
      .catch((err) => {
        if (!cancelled) {
          setClients([]);
          setLoadError(
            err instanceof Error ? err.message : "Αποτυχία φόρτωσης πελατών.",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open, clients]);

  // Close when clicking outside.
  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  const trimmed = query.trim();
  const filtered = useMemo(() => {
    const list = clients ?? [];
    if (!trimmed) return list;
    const needle = trimmed.toLocaleLowerCase("el");
    return list.filter((c) => c.name.toLocaleLowerCase("el").includes(needle));
  }, [clients, trimmed]);

  // Only offer "create" when nothing matches EXACTLY — otherwise picking an
  // existing client would sit next to an option that makes a duplicate.
  const exactExists = (clients ?? []).some(
    (c) => c.name.trim().toLocaleLowerCase("el") === trimmed.toLocaleLowerCase("el"),
  );
  const canCreate = trimmed.length > 0 && !exactExists;

  function pick(client: ClientDetail) {
    onChange({ id: client.id, name: client.name });
    setQuery(client.name);
    setOpen(false);
  }

  function createInline() {
    // No id: the transaction endpoint creates the client from the name, so a
    // single request covers both. Nothing is written until the form is saved.
    onChange({ name: trimmed });
    setOpen(false);
  }

  return (
    <div className="relative" ref={boxRef}>
      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
        <input
          className={clsx(inputClassName, "pl-8 pr-8")}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
            // Typing invalidates a previous pick until it is re-confirmed,
            // otherwise the stale id would win over the visible text.
            onChange(e.target.value.trim() ? { name: e.target.value.trim() } : null);
          }}
          onFocus={() => setOpen(true)}
          placeholder="Αναζήτηση ή νέος πελάτης…"
          role="combobox"
          aria-expanded={open}
          aria-controls="client-picker-list"
          autoComplete="off"
        />
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
          aria-label="Άνοιγμα λίστας πελατών"
        >
          <ChevronDown className="h-4 w-4" />
        </button>
      </div>

      {value?.id ? (
        <p className="mt-1 flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400">
          <Check className="h-3 w-3" /> Επιλέχθηκε υπάρχων πελάτης
        </p>
      ) : trimmed ? (
        <p className="mt-1 flex items-center gap-1 text-[11px] text-indigo-600 dark:text-indigo-400">
          <Plus className="h-3 w-3" /> Θα δημιουργηθεί νέος πελάτης
        </p>
      ) : null}
      {hint}

      {open ? (
        <div
          id="client-picker-list"
          role="listbox"
          className="absolute z-30 mt-1 max-h-60 w-full overflow-auto rounded-xl border border-slate-200 bg-white py-1 shadow-xl dark:border-slate-700 dark:bg-slate-900"
        >
          {clients === null ? (
            <div className="flex items-center gap-2 px-3 py-2 text-xs text-slate-500 dark:text-slate-400">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Φόρτωση πελατών…
            </div>
          ) : loadError ? (
            <div className="px-3 py-2 text-xs text-rose-600 dark:text-rose-400">
              {loadError}
            </div>
          ) : null}

          {filtered.map((c) => (
            <button
              key={c.id}
              type="button"
              role="option"
              aria-selected={value?.id === c.id}
              onClick={() => pick(c)}
              className={clsx(
                "flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm transition hover:bg-slate-50 dark:hover:bg-slate-800",
                value?.id === c.id && "bg-indigo-50 dark:bg-indigo-500/10",
              )}
            >
              <span className="truncate text-slate-800 dark:text-slate-100">
                {c.name}
              </span>
              {c.archived ? (
                <span className="shrink-0 rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                  Αρχειοθετημένος
                </span>
              ) : null}
            </button>
          ))}

          {clients !== null && filtered.length === 0 && !canCreate ? (
            <div className="px-3 py-2 text-xs text-slate-500 dark:text-slate-400">
              Δεν βρέθηκαν πελάτες.
            </div>
          ) : null}

          {canCreate ? (
            <button
              type="button"
              onClick={createInline}
              className="flex w-full items-center gap-2 border-t border-slate-100 px-3 py-2 text-left text-sm font-medium text-indigo-600 transition hover:bg-indigo-50 dark:border-slate-800 dark:text-indigo-400 dark:hover:bg-indigo-500/10"
            >
              <Plus className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">Νέος πελάτης: «{trimmed}»</span>
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
