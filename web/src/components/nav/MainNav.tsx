"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { CreditCard, LayoutDashboard, PieChart } from "lucide-react";
import { clsx } from "@/lib/clsx";

/**
 * The app's primary navigation.
 *
 * Συνδρομή is a first-class destination here rather than a link buried in a
 * menu, because it is the one page a lapsed account can still reach and the
 * only route back from a paywall. It keeps its credit-card icon in every
 * surface it appears in (here and in the user menu) so it is recognisable
 * before it is read.
 *
 * The active item is decided from the pathname rather than passed down: the
 * layout that renders this is a Server Component and would have to become
 * dynamic per route to know it.
 */
const ITEMS = [
  { href: "/", label: "Πίνακας Ελέγχου", icon: LayoutDashboard },
  { href: "/summary", label: "Σύνοψη", icon: PieChart },
  { href: "/billing", label: "Συνδρομή", icon: CreditCard },
] as const;

export function MainNav() {
  const pathname = usePathname();

  return (
    <nav aria-label="Κύρια πλοήγηση" className="-mb-px flex items-center gap-1">
      {ITEMS.map(({ href, label, icon: Icon }) => {
        // "/" would otherwise prefix-match every route in the app.
        const active =
          href === "/" ? pathname === "/" : pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={clsx(
              "inline-flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-sm font-medium transition",
              active
                ? "border-indigo-600 text-indigo-600 dark:border-indigo-400 dark:text-indigo-400"
                : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-900 dark:text-slate-400 dark:hover:border-slate-700 dark:hover:text-white",
            )}
          >
            <Icon className="h-4 w-4" />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
