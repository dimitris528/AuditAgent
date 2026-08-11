import Link from "next/link";
import { Calculator } from "lucide-react";
import { getSessionUsername } from "@/lib/session";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { MainNav } from "@/components/nav/MainNav";
import { UserMenu } from "@/components/nav/UserMenu";

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
  // Null only for an unauthenticated render, which the middleware already
  // redirects — so in practice this is the tenant's name.
  const username = await getSessionUsername();
  const authed = Boolean(username);
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
      {/* Two rows: identity and account controls on top, navigation beneath.
          The nav row is what makes Συνδρομή visible from every page instead of
          being one unlabelled icon among three — it is the only route back for
          an account whose trial has lapsed, so it should never be the hardest
          link in the header to find. */}
      {/* max-w-[1600px], not max-w-7xl (1280).
          The dashboard beneath this is a two-column grid of charts and a client
          rail, and at 1280 the two bar charts were ~380px wide each — narrow
          enough that a client name and its bar were competing for the same
          space. 1600 is roughly what a 1080p browser gives you, so on the
          screens this is actually used on the shell now ends where the display
          does; the cap only bites on ultrawides, where a full-bleed line of
          text is its own readability problem. */}
      <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/80 backdrop-blur print:hidden dark:border-slate-800 dark:bg-slate-950/80">
        <div className="mx-auto flex h-14 max-w-[1600px] items-center justify-between px-4 sm:px-6">
          <Link href="/" className="flex items-center gap-2.5">
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
          </Link>
          <div className="flex items-center gap-2">
            <ThemeToggle />
            {authed ? <UserMenu username={username} /> : null}
          </div>
        </div>

        {/* Hidden when signed out: the nav would only offer routes the
            middleware bounces straight back to /login. */}
        {authed ? (
          <div className="mx-auto max-w-[1600px] px-4 sm:px-6">
            {/* overflow-x-auto so three items and their icons never wrap or
                clip on a narrow phone. */}
            <div className="overflow-x-auto">
              <MainNav />
            </div>
          </div>
        ) : null}
      </header>
      <main className="mx-auto max-w-[1600px] px-4 py-4 sm:px-6 print:max-w-none print:p-0">
        {children}
      </main>
    </>
  );
}
