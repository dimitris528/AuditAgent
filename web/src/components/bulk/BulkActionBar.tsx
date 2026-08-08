"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import {
  AlertTriangle,
  Archive,
  ArchiveRestore,
  Loader2,
  Trash2,
  X,
} from "lucide-react";
import { clsx } from "@/lib/clsx";

/**
 * The floating bar that appears once rows are ticked, plus its confirmation.
 *
 * Shared by the client grid and the transactions table. Which actions it offers
 * is the caller's choice: transactions have no archived state, so that button
 * simply is not passed there rather than being rendered disabled.
 *
 * Rendered through a portal for the same reason ImportDataModal is — one of its
 * two mount points is inside the dashboard's client rail, whose sticky header
 * carries `backdrop-blur`, and a backdrop-filter ancestor becomes the
 * containing block for `position: fixed` descendants. In place, this bar would
 * be pinned to the bottom of a ~370px column instead of the viewport.
 */

// ---------------------------------------------------------------------------
// Stacking
// ---------------------------------------------------------------------------
// Both lists live on the dashboard at once, so both bars can be active — ticking
// three clients and five transactions is an ordinary thing to do. Two elements
// pinned to `bottom` would then sit exactly on top of each other, so active bars
// register here and read their position from the order they appeared in.
//
// A module-level store rather than a context: it keeps the bar a drop-in
// component with no provider to remember, which is what lets a third list adopt
// it later without touching the page.
const active: string[] = [];
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function snapshot() {
  return active.join("|");
}

function useStackIndex(id: string, visible: boolean): number {
  useEffect(() => {
    if (!visible) return;
    active.push(id);
    emit();
    return () => {
      const at = active.indexOf(id);
      if (at !== -1) active.splice(at, 1);
      emit();
    };
  }, [id, visible]);

  const order = useSyncExternalStore(subscribe, snapshot, () => "");
  const index = order ? order.split("|").indexOf(id) : -1;
  return index < 0 ? 0 : index;
}

// ---------------------------------------------------------------------------
// Copy
// ---------------------------------------------------------------------------
export type BulkScope = "clients" | "transactions";

/** Greek agrees the noun AND the verb with the count, so both forms are needed
 *  — "Επιλέχθηκαν 1 πελάτες" is exactly what makes a product read as
 *  machine-translated. */
const NOUNS: Record<BulkScope, [string, string]> = {
  clients: ["πελάτης", "πελάτες"],
  transactions: ["κίνηση", "κινήσεις"],
};

function selectedLabel(scope: BulkScope, count: number): string {
  const [singular, plural] = NOUNS[scope];
  return count === 1
    ? `Επιλέχθηκε 1 ${singular}`
    : `Επιλέχθηκαν ${count} ${plural}`;
}

// ---------------------------------------------------------------------------
// Confirmation
// ---------------------------------------------------------------------------
function ConfirmDelete({
  count,
  scope,
  busy,
  error,
  onCancel,
  onConfirm,
}: {
  count: number;
  scope: BulkScope;
  busy: boolean;
  error: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !busy) onCancel();
    }
    document.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [onCancel, busy]);

  return createPortal(
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center p-4"
      role="alertdialog"
      aria-modal="true"
      aria-label="Επιβεβαίωση διαγραφής"
    >
      <button
        type="button"
        aria-label="Άκυρο"
        onClick={busy ? undefined : onCancel}
        className="absolute inset-0 bg-slate-900/60 backdrop-blur-[2px]"
      />
      <div className="relative w-full max-w-md overflow-hidden rounded-2xl border border-rose-200 bg-white shadow-2xl dark:border-rose-500/30 dark:bg-slate-900">
        <div className="flex items-start gap-3 p-5">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-400">
            <AlertTriangle className="h-5 w-5" />
          </span>
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-slate-900 dark:text-white">
              Μαζική Διαγραφή
            </h2>
            <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">
              {/* Article, numeral and noun all agree with the count — "τα 1
                  επιλεγμένα στοιχεία" is the kind of line that makes a product
                  read as machine-translated. */}
              {count === 1
                ? "Είστε σίγουροι ότι θέλετε να διαγράψετε το 1 επιλεγμένο στοιχείο;"
                : `Είστε σίγουροι ότι θέλετε να διαγράψετε τα ${count} επιλεγμένα στοιχεία;`}
            </p>
            <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">
              Η ενέργεια δεν αναιρείται.
              {scope === "clients"
                ? " Πελάτες με καταχωρημένες κινήσεις δεν διαγράφονται — αρχειοθετήστε τους."
                : ""}
            </p>
            {error ? (
              <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">
                {error}
              </p>
            ) : null}
          </div>
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-800/40">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium text-slate-600 transition hover:bg-white disabled:opacity-60 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            Άκυρο
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            // Autofocus is deliberately NOT on the destructive button: a
            // stray Enter must not delete anything.
            className="inline-flex items-center gap-1.5 rounded-lg bg-rose-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-rose-500 disabled:opacity-60"
          >
            {busy ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Trash2 className="h-4 w-4" />
            )}
            Διαγραφή {count}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

// ---------------------------------------------------------------------------
// The bar
// ---------------------------------------------------------------------------
export interface BulkResult {
  /** Shown in the bar after the action. Comes from the server, which knows
   *  what it actually did — including what it refused. */
  message: string;
  /** Rows the server declined to touch, e.g. a client that still has
   *  transactions. Surfaced verbatim; the reason is per-row. */
  blocked?: { message: string }[];
}

export function BulkActionBar({
  scope,
  count,
  onClear,
  onDelete,
  onArchive,
  archiveLabel = "Αρχειοθέτηση",
  hiddenSelected = false,
}: {
  scope: BulkScope;
  count: number;
  onClear: () => void;
  /** Resolves with what the server reports. Throwing surfaces in the modal. */
  onDelete: () => Promise<BulkResult>;
  /** Omitted where the concept does not exist — transactions have no archived
   *  state, so the button is absent rather than rendered disabled. */
  onArchive?: () => Promise<BulkResult>;
  archiveLabel?: string;
  /** True when rows are ticked but filtered out of view — worth saying, or the
   *  count looks wrong against what is on screen. */
  hiddenSelected?: boolean;
}) {
  const id = useRef(`bulk-${scope}-${Math.random().toString(36).slice(2)}`).current;
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<BulkResult | null>(null);

  const visible = count > 0 || result !== null;
  const stackIndex = useStackIndex(id, visible);

  // The report clears itself: it is an acknowledgement, not a state the user
  // has to dismiss. Long enough to read a line of Greek.
  useEffect(() => {
    if (!result) return;
    const timer = setTimeout(() => setResult(null), result.blocked?.length ? 9000 : 4500);
    return () => clearTimeout(timer);
  }, [result]);

  const run = useCallback(
    async (action: () => Promise<BulkResult>) => {
      setBusy(true);
      setError("");
      try {
        const outcome = await action();
        setResult(outcome);
        setConfirming(false);
        // Selection is cleared on success because the rows it named are gone
        // or moved; keeping the ticks would offer a second delete of nothing.
        onClear();
        return true;
      } catch (err) {
        setError(err instanceof Error ? err.message : "Η ενέργεια απέτυχε.");
        return false;
      } finally {
        setBusy(false);
      }
    },
    [onClear],
  );

  if (!visible) return null;

  return createPortal(
    <>
      <div
        className="fixed left-1/2 z-[60] w-[min(38rem,calc(100vw-2rem))] -translate-x-1/2"
        style={{ bottom: `${1.5 + stackIndex * 4.75}rem` }}
        role="region"
        aria-label={`Ενέργειες για επιλεγμένα (${scope})`}
      >
        <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-slate-700 bg-slate-900 px-3 py-2.5 shadow-2xl dark:border-slate-600">
          {count > 0 ? (
            <>
              <span className="ml-1 text-sm font-semibold text-white">
                {selectedLabel(scope, count)}
              </span>
              {hiddenSelected ? (
                <span
                  className="text-[11px] text-slate-400"
                  title="Κάποιες επιλογές δεν εμφανίζονται με το τρέχον φίλτρο και δεν συμπεριλαμβάνονται."
                >
                  (μόνο τα ορατά)
                </span>
              ) : null}

              <div className="ml-auto flex items-center gap-2">
                {onArchive ? (
                  <button
                    type="button"
                    onClick={() => void run(onArchive)}
                    disabled={busy}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-slate-600 px-3 py-1.5 text-xs font-medium text-slate-200 transition hover:border-slate-400 hover:text-white disabled:opacity-60"
                  >
                    {busy ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : archiveLabel === "Αρχειοθέτηση" ? (
                      <Archive className="h-3.5 w-3.5" />
                    ) : (
                      <ArchiveRestore className="h-3.5 w-3.5" />
                    )}
                    {archiveLabel}
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={() => {
                    setError("");
                    setConfirming(true);
                  }}
                  disabled={busy}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-rose-500 disabled:opacity-60"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Μαζική Διαγραφή
                </button>
                <button
                  type="button"
                  onClick={onClear}
                  disabled={busy}
                  aria-label="Καθαρισμός επιλογής"
                  title="Καθαρισμός επιλογής"
                  className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-800 hover:text-white disabled:opacity-60"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </>
          ) : (
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-white">{result?.message}</p>
              {result?.blocked?.length ? (
                <ul className="mt-1 space-y-0.5">
                  {result.blocked.slice(0, 4).map((row, i) => (
                    <li key={i} className="text-[11px] text-amber-300">
                      {row.message}
                    </li>
                  ))}
                  {result.blocked.length > 4 ? (
                    <li className="text-[11px] text-amber-300/70">
                      …και άλλοι {result.blocked.length - 4}.
                    </li>
                  ) : null}
                </ul>
              ) : null}
            </div>
          )}
          {count === 0 && result ? (
            <button
              type="button"
              onClick={() => setResult(null)}
              aria-label="Κλείσιμο"
              className="ml-auto rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-800 hover:text-white"
            >
              <X className="h-4 w-4" />
            </button>
          ) : null}
        </div>

        {error && !confirming ? (
          <p className="mt-1.5 rounded-lg bg-rose-600 px-3 py-1.5 text-center text-xs font-medium text-white">
            {error}
          </p>
        ) : null}
      </div>

      {confirming ? (
        <ConfirmDelete
          count={count}
          scope={scope}
          busy={busy}
          error={error}
          onCancel={() => setConfirming(false)}
          onConfirm={() => void run(onDelete)}
        />
      ) : null}
    </>,
    document.body,
  );
}
