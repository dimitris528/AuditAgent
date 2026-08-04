import { Calculator } from "lucide-react";

/**
 * The frosted card every auth form sits in, plus the field styling that goes
 * with it.
 *
 * The classes are exported rather than each page keeping its own copy: the
 * three forms are on the same blue background, and a field that is legible on
 * /login and washed out on /register is exactly what happens when the styling
 * is pasted three times. They are also NOT theme-dependent — the auth
 * background is deep blue in light and dark mode alike, so a `dark:` variant
 * here would only produce dark-on-dark for half the visitors.
 */
export const authField =
  "w-full rounded-lg border border-white/15 bg-white/[0.06] px-3 py-2.5 text-sm text-white placeholder:text-slate-400 outline-none transition focus:border-sky-400/60 focus:bg-white/10 focus:ring-1 focus:ring-sky-400/40";

export const authLabel = "mb-1 block text-xs font-medium text-slate-300";

export const authHint = "mt-1 text-[11px] text-slate-400";

export const authButton =
  "inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-gradient-to-r from-indigo-500 to-sky-500 px-3 py-2.5 text-sm font-semibold text-white shadow-lg shadow-sky-950/40 transition hover:from-indigo-400 hover:to-sky-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300 disabled:cursor-not-allowed disabled:opacity-60";

export const authLink =
  "font-semibold text-sky-300 underline-offset-2 transition hover:text-sky-200 hover:underline";

export function AuthCard({
  title,
  subtitle,
  children,
  footer,
}: {
  title: React.ReactNode;
  subtitle: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <div className="auth-scrim relative w-full max-w-sm">
      <div className="mb-6 flex flex-col items-center text-center">
        <span className="mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-sky-400 text-white shadow-lg shadow-sky-950/40">
          <Calculator className="h-6 w-6" />
        </span>
        <h1 className="text-lg font-bold text-white">{title}</h1>
        <p className="mt-1 text-sm text-slate-300">{subtitle}</p>
      </div>

      {/* backdrop-blur over the moving curves is what makes them read as
          *behind* the form rather than through it. */}
      <div className="rounded-2xl border border-white/10 bg-slate-950/40 p-6 text-slate-200 shadow-2xl shadow-slate-950/50 backdrop-blur-xl">
        {children}
      </div>

      {footer}
    </div>
  );
}
