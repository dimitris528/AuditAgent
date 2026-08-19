import Link from "next/link";
import { ArrowLeft } from "lucide-react";

import { formatVersionDate } from "@/lib/legal";
import { PublicFooter } from "./PublicFooter";

/**
 * The shared chrome for /terms and /privacy: a readable column, a dated header,
 * and a way back to the signup form.
 *
 * Theme-aware (unlike the auth card, which sits on a fixed blue background),
 * because these pages are also opened from inside the product by someone who
 * has already chosen dark mode — and a document that ignores that choice is
 * exactly the one nobody finishes reading.
 */
export function LegalDocument({
  title,
  subtitle,
  version,
  children,
}: {
  title: string;
  subtitle: string;
  version: string;
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-slate-50 px-4 py-10 text-slate-800 dark:bg-slate-950 dark:text-slate-200">
      <div className="mx-auto w-full max-w-3xl">
        <Link
          href="/register"
          className="mb-6 inline-flex items-center gap-1.5 text-xs font-semibold text-slate-500 transition hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
          Πίσω στην εγγραφή
        </Link>

        <header className="border-b border-slate-200 pb-5 dark:border-slate-800">
          <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-50">
            {title}
          </h1>
          <p className="mt-1.5 text-sm text-slate-600 dark:text-slate-400">
            {subtitle}
          </p>
          <p className="mt-3 text-xs text-slate-500 dark:text-slate-500">
            Έκδοση {version} · Τελευταία ενημέρωση: {formatVersionDate(version)}
          </p>
        </header>

        <article className="legal-body mt-7 space-y-7 text-sm leading-relaxed">
          {children}
        </article>

        <PublicFooter className="mt-12 border-t border-slate-200 pt-6 text-slate-500 dark:border-slate-800 dark:text-slate-500" />
      </div>
    </div>
  );
}

/** One numbered clause. Headings carry an id so the document can be linked
 *  into — "/privacy#cookies" from the cookie notice, and from a support reply
 *  pointing somebody at the retention section. */
export function Clause({
  id,
  number,
  title,
  children,
}: {
  id?: string;
  number: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-6">
      <h2 className="text-base font-bold text-slate-900 dark:text-slate-100">
        <span className="mr-2 text-slate-400 dark:text-slate-600">{number}</span>
        {title}
      </h2>
      <div className="mt-2 space-y-3 text-slate-700 dark:text-slate-300">
        {children}
      </div>
    </section>
  );
}

/** A definition-style row, for the tables of data categories and retention
 *  periods that carry most of the actual information in a privacy policy. */
export function Term({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid gap-1 border-l-2 border-slate-200 py-1 pl-3 sm:grid-cols-[minmax(9rem,auto)_1fr] sm:gap-4 dark:border-slate-800">
      <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {label}
      </dt>
      <dd className="text-slate-700 dark:text-slate-300">{children}</dd>
    </div>
  );
}
