"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  FileSpreadsheet,
  Info,
  Loader2,
  Upload,
  X,
  XCircle,
} from "lucide-react";
import {
  IMPORT_ACCEPT,
  IMPORT_MAX_BYTES,
  importData,
  importTemplateUrl,
} from "@/lib/api";
import type { ImportIssue, ImportKind, ImportSummary } from "@/lib/types";
import { clsx } from "@/lib/clsx";

/**
 * "Εισαγωγή Δεδομένων (CSV / Excel)" — the migration front door.
 *
 * One component for both importers, because they differ only in their copy:
 * the flow (pick a file, upload it, read what happened) and every failure mode
 * are identical, and two near-copies would drift the moment one of them grew a
 * feature.
 *
 * A partial import is the NORMAL outcome, not an error. Rows are validated
 * independently server-side, so a real migration file routinely lands as
 * "42 imported, 3 already on file, 1 unreadable" — the summary below is
 * therefore laid out as an answer to "what happened to my 46 rows?", with the
 * counts adding up in plain sight, rather than as a success banner with an
 * error hidden behind it.
 */

interface CopyBlock {
  title: string;
  /** The columns the template ships with, shown before a file is picked so the
   *  expected shape is visible without downloading anything. */
  columns: string;
  hint: string;
  /** Rendered when the server skipped rows — says WHY skipping is the right
   *  outcome, since "3 skipped" otherwise reads as data loss. */
  skippedLabel: string;
}

const COPY: Record<ImportKind, CopyBlock> = {
  clients: {
    title: "Εισαγωγή Πελατών",
    columns: "Επωνυμία · Α.Φ.Μ. · Τηλέφωνο · Email · Σημειώσεις",
    hint:
      "Απαιτείται μόνο η Επωνυμία. Το τηλέφωνο και το email αποθηκεύονται στο " +
      "πεδίο επικοινωνίας του πελάτη.",
    skippedLabel: "Υπάρχουν ήδη",
  },
  transactions: {
    title: "Εισαγωγή Κινήσεων",
    columns:
      "Ημερομηνία · Πελάτης · Α.Φ.Μ. · Είδος Κίνησης · Τύπος Παραστατικού · " +
      "Αρ. Παραστατικού · Συνολικό Ποσό · Συντ. Φ.Π.Α. · Φ.Π.Α. · Περιγραφή",
    hint:
      "Οι πελάτες αντιστοιχίζονται με Α.Φ.Μ. ή επωνυμία και δημιουργούνται " +
      "αυτόματα αν δεν υπάρχουν. Χωρίς στήλη Φ.Π.Α. εφαρμόζεται ο συντελεστής 24 %.",
    skippedLabel: "Ήδη καταχωρημένα",
  },
};

function bytes(size: number): string {
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

/** One of the three issue lists. Collapsed to a count until asked for: a
 *  hundred rows of detail would bury the headline figure. */
function IssueList({
  title,
  issues,
  total,
  tone,
}: {
  title: string;
  issues: ImportIssue[];
  /** The exact count. Can exceed `issues.length` — the server caps the list. */
  total: number;
  tone: "error" | "warning" | "muted";
}) {
  const [open, setOpen] = useState(false);
  if (total === 0) return null;

  const palette = {
    error: "text-rose-600 dark:text-rose-400",
    warning: "text-amber-600 dark:text-amber-400",
    muted: "text-slate-500 dark:text-slate-400",
  }[tone];

  return (
    <div className="rounded-xl border border-slate-200 dark:border-slate-800">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left"
      >
        <span className={clsx("text-xs font-semibold", palette)}>
          {title} ({total})
        </span>
        <span className="text-[11px] text-slate-400">
          {open ? "Απόκρυψη" : "Εμφάνιση"}
        </span>
      </button>
      {open ? (
        <ul className="max-h-48 space-y-1 overflow-y-auto border-t border-slate-100 px-3 py-2 dark:border-slate-800">
          {issues.map((issue, i) => (
            <li
              key={`${issue.row}-${i}`}
              className="flex gap-2 text-[11px] text-slate-600 dark:text-slate-300"
            >
              <span className="shrink-0 font-semibold tabular-nums text-slate-400">
                Γρ. {issue.row}
              </span>
              <span className="min-w-0">{issue.message}</span>
            </li>
          ))}
          {total > issues.length ? (
            <li className="pt-1 text-[11px] italic text-slate-400">
              …και άλλες {total - issues.length} γραμμές.
            </li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}

function Summary({ kind, summary }: { kind: ImportKind; summary: ImportSummary }) {
  const copy = COPY[kind];
  const nothing = summary.imported === 0;

  return (
    <div className="space-y-4">
      <div
        className={clsx(
          "flex items-start gap-3 rounded-xl p-4",
          nothing
            ? "bg-slate-100 dark:bg-slate-800/60"
            : "bg-emerald-50 dark:bg-emerald-500/10",
        )}
      >
        {nothing ? (
          <Info className="mt-0.5 h-5 w-5 shrink-0 text-slate-500" />
        ) : (
          <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600 dark:text-emerald-400" />
        )}
        <div className="min-w-0">
          <p
            className={clsx(
              "text-sm font-semibold",
              nothing
                ? "text-slate-700 dark:text-slate-200"
                : "text-emerald-800 dark:text-emerald-300",
            )}
          >
            {summary.message}
          </p>
          {summary.clients_created > 0 ? (
            <p className="mt-0.5 text-xs text-emerald-700 dark:text-emerald-400/80">
              Δημιουργήθηκαν {summary.clients_created} νέοι πελάτες για τις
              κινήσεις αυτές.
            </p>
          ) : null}
        </div>
      </div>

      {/* The arithmetic in plain sight: the three buckets sum to the rows read,
          so nothing can have quietly gone missing between file and book. */}
      <div className="grid grid-cols-3 gap-2 text-center">
        {(
          [
            ["Εισήχθησαν", summary.imported, "text-emerald-600 dark:text-emerald-400"],
            [copy.skippedLabel, summary.skipped, "text-slate-500 dark:text-slate-400"],
            ["Απέτυχαν", summary.failed, "text-rose-600 dark:text-rose-400"],
          ] as [string, number, string][]
        ).map(([label, value, tone]) => (
          <div
            key={label}
            className="rounded-xl border border-slate-200 p-3 dark:border-slate-800"
          >
            <div className={clsx("text-xl font-bold tabular-nums", tone)}>
              {value}
            </div>
            <div className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">
              {label}
            </div>
          </div>
        ))}
      </div>
      <p className="text-center text-[11px] text-slate-400">
        Διαβάστηκαν {summary.total_rows} γραμμές
        {summary.filename ? ` από «${summary.filename}»` : ""}.
      </p>

      <IssueList
        title="Γραμμές που δεν εισήχθησαν"
        issues={summary.errors}
        total={summary.failed}
        tone="error"
      />
      <IssueList
        title="Προειδοποιήσεις"
        issues={summary.warnings}
        total={summary.warnings.length}
        tone="warning"
      />
      <IssueList
        title={copy.skippedLabel}
        issues={summary.skipped_rows}
        total={summary.skipped}
        tone="muted"
      />

      {summary.failed > 0 ? (
        <p className="rounded-xl bg-amber-50 px-3 py-2 text-[11px] text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
          Διορθώστε τις παραπάνω γραμμές και ανεβάστε ξανά το αρχείο. Όσα
          εισήχθησαν δεν θα διπλοκαταχωρηθούν.
        </p>
      ) : null}
    </div>
  );
}

export function ImportDataModal({
  kind,
  onClose,
}: {
  kind: ImportKind;
  onClose: () => void;
}) {
  const copy = COPY[kind];
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [summary, setSummary] = useState<ImportSummary | null>(null);
  // Gates the portal below. document.body does not exist during SSR, and the
  // effect only runs on the client — so the first paint renders nothing and the
  // modal appears immediately after hydration.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  useEffect(() => {
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
  }, [onClose]);

  async function send(file: File) {
    setError("");
    if (file.size > IMPORT_MAX_BYTES) {
      setError(
        `Το αρχείο ξεπερνά τα ${bytes(IMPORT_MAX_BYTES)} (${bytes(file.size)}).`,
      );
      return;
    }
    setBusy(true);
    try {
      const result = await importData(kind, file);
      setSummary(result);
      // Refreshed immediately rather than on close, so the grid behind the
      // modal already shows the imported rows when it is dismissed.
      if (result.imported > 0) router.refresh();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Αποτυχία εισαγωγής δεδομένων.",
      );
    } finally {
      setBusy(false);
    }
  }

  function pick(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    // Reset so re-picking the SAME file (after fixing it) fires change again.
    e.target.value = "";
    if (file) void send(file);
  }

  function drop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file && !busy) void send(file);
  }

  if (!mounted) return null;

  // Rendered into document.body rather than in place, and that is REQUIRED
  // rather than tidy. One of the two buttons that opens this modal lives in the
  // dashboard's client rail, whose sticky header carries `backdrop-blur` — and
  // an ancestor with a backdrop-filter (or a transform, or a filter) becomes
  // the containing block for its `position: fixed` descendants. Rendered in
  // place, the modal was laid out inside that ~370px column and its full-screen
  // backdrop covered only the rail. A portal escapes any such ancestor, so the
  // component stays safe to drop into a toolbar without auditing its parents.
  return createPortal(
    <div
      className="fixed inset-0 z-[55] flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label={copy.title}
    >
      <button
        type="button"
        aria-label="Κλείσιμο"
        onClick={onClose}
        className="absolute inset-0 bg-slate-900/50 backdrop-blur-[2px]"
      />

      <div className="relative flex max-h-[90vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900">
        <div className="flex items-start justify-between gap-3 border-b border-slate-200 p-5 dark:border-slate-800">
          <div className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600 dark:bg-indigo-500/10 dark:text-indigo-400">
              <FileSpreadsheet className="h-5 w-5" />
            </span>
            <div>
              <h2 className="text-base font-semibold text-slate-900 dark:text-white">
                {copy.title}
              </h2>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Εισαγωγή Δεδομένων (CSV / Excel)
              </p>
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

        <div className="flex-1 overflow-y-auto p-5">
          {summary ? (
            <Summary kind={kind} summary={summary} />
          ) : (
            <div className="space-y-4">
              {/* The template comes FIRST: someone who has never seen the
                  expected columns needs it before the drop zone, not after. */}
              <a
                href={importTemplateUrl(kind)}
                download
                className="flex items-center gap-2.5 rounded-xl border border-indigo-200 bg-indigo-50/60 px-3 py-2.5 text-sm font-medium text-indigo-700 transition hover:border-indigo-300 hover:bg-indigo-50 dark:border-indigo-500/30 dark:bg-indigo-500/10 dark:text-indigo-300 dark:hover:bg-indigo-500/15"
              >
                <Download className="h-4 w-4 shrink-0" />
                Κατεβάστε το πρότυπο αρχείο CSV
              </a>

              <div className="rounded-xl border border-slate-200 p-3 dark:border-slate-800">
                <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                  Αναμενόμενες στήλες
                </div>
                <p className="mt-1 text-xs text-slate-600 dark:text-slate-300">
                  {copy.columns}
                </p>
                <p className="mt-2 text-[11px] text-slate-500 dark:text-slate-400">
                  {copy.hint}
                </p>
              </div>

              <div
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={drop}
                className={clsx(
                  "rounded-xl border-2 border-dashed p-6 text-center transition",
                  dragging
                    ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-500/10"
                    : "border-slate-300 dark:border-slate-700",
                )}
              >
                {busy ? (
                  <div className="flex flex-col items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
                    <Loader2 className="h-6 w-6 animate-spin text-indigo-500" />
                    Ανάγνωση και έλεγχος αρχείου…
                  </div>
                ) : (
                  <>
                    <Upload className="mx-auto h-6 w-6 text-slate-400" />
                    <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">
                      Σύρετε το αρχείο εδώ ή
                    </p>
                    <button
                      type="button"
                      onClick={() => fileRef.current?.click()}
                      className="mt-2 inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-indigo-500"
                    >
                      Επιλογή αρχείου
                    </button>
                    <p className="mt-2 text-[11px] text-slate-400">
                      .csv ή .xlsx — έως {bytes(IMPORT_MAX_BYTES)}
                    </p>
                  </>
                )}
              </div>

              <input
                ref={fileRef}
                type="file"
                accept={IMPORT_ACCEPT}
                onChange={pick}
                className="hidden"
              />

              {error ? (
                <p className="flex items-start gap-1.5 text-xs text-rose-600 dark:text-rose-400">
                  <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  {error}
                </p>
              ) : (
                <p className="flex items-start gap-1.5 text-[11px] text-slate-500 dark:text-slate-400">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  Οι γραμμές ελέγχονται ξεχωριστά: ένα λάθος σε μία γραμμή δεν
                  εμποδίζει τις υπόλοιπες. Διπλοεγγραφές παραλείπονται, οπότε
                  μπορείτε να ανεβάσετε ξανά το ίδιο αρχείο με ασφάλεια.
                </p>
              )}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-slate-200 p-4 dark:border-slate-800">
          {summary ? (
            <button
              type="button"
              onClick={() => {
                setSummary(null);
                setError("");
              }}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium text-slate-600 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
            >
              Νέα εισαγωγή
            </button>
          ) : null}
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white transition hover:bg-slate-700 disabled:opacity-60 dark:bg-white dark:text-slate-900 dark:hover:bg-slate-200"
          >
            {summary ? "Κλείσιμο" : "Άκυρο"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/**
 * The trigger that opens the modal. Lives with it so the two toolbars that
 * offer this feature — the client grid and the transactions table — mount one
 * component and share every behaviour.
 */
export function ImportDataButton({
  kind,
  compact = false,
  className,
}: {
  kind: ImportKind;
  /** Icon-and-short-label, for the dashboard's narrow right-hand rail. */
  compact?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title="Εισαγωγή Δεδομένων (CSV / Excel)"
        className={clsx(
          "inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white font-medium text-slate-600 transition hover:border-slate-300 hover:text-slate-900",
          "dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-slate-600 dark:hover:text-white",
          compact ? "h-7 px-2 text-[11px]" : "h-9 px-3 text-xs",
          className,
        )}
      >
        <Upload className={compact ? "h-3.5 w-3.5" : "h-4 w-4"} />
        {compact ? "Εισαγωγή" : "Εισαγωγή Δεδομένων (CSV / Excel)"}
      </button>
      {open ? (
        <ImportDataModal kind={kind} onClose={() => setOpen(false)} />
      ) : null}
    </>
  );
}
