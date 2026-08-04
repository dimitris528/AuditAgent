import { cookies } from "next/headers";
import Link from "next/link";
import { Calculator, CreditCard } from "lucide-react";
import { SESSION_COOKIE } from "@/lib/constants";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { LogoutButton } from "@/components/LogoutButton";

/**
 * The signed-in shell: header, centred main, and the ambient backdrop.
 *
 * The backdrop is deliberately STATIC. The auth pages animate because they are
 * looked at for fifteen seconds; this one is looked at all day, and anything
 * that moves in the periphery of a screen someone is reading numbers off is a
 * source of eye strain rather than polish. What is left is a pair of very low
 * -opacity radial washes that give the flat slate background some depth and
 * cost exactly one paint.
 */
export default async function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const authed = Boolean((await cookies()).get(SESSION_COOKIE)?.value);
  return (
    <>
      {/* fixed + -z-10: sits behind the content, never scrolls, never
          intercepts a click. print:hidden — it would only burn toner. */}
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 -z-10 app-ambient print:hidden"
      />

      {/* print:hidden — the app chrome has no business on a statement
          that gets saved as PDF and sent to a client. */}
      <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/80 backdrop-blur print:hidden dark:border-slate-800 dark:bg-slate-950/80">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4 sm:px-6">
          <div className="flex items-center gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600 text-white">
              <Calculator className="h-5 w-5" />
            </span>
            <div className="leading-tight">
              <div className="text-sm font-bold tracking-tight text-slate-900 dark:text-white">
                Λογιστήριο<span className="text-indigo-600 dark:text-indigo-400">Pro</span>
              </div>
              <div className="text-[11px] text-slate-500 dark:text-slate-400">
                Accounting Dashboard
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {/* Always reachable while signed in — including for a lapsed
                account, whose only way back is through this link. */}
            {authed ? (
              <Link
                href="/billing"
                title="Συνδρομή"
                className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-slate-600 transition hover:border-slate-300 hover:text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-slate-600 dark:hover:text-white"
              >
                <CreditCard className="h-4 w-4" />
                <span className="hidden sm:inline text-xs font-medium">
                  Συνδρομή
                </span>
              </Link>
            ) : null}
            <ThemeToggle />
            {authed ? <LogoutButton /> : null}
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 print:max-w-none print:p-0">
        {children}
      </main>
    </>
  );
}
