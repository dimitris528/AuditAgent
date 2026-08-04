import Link from "next/link";
import { CalendarClock, Sparkles } from "lucide-react";
import type { SubscriptionInfo } from "@/lib/types";

/**
 * The trial countdown on the dashboard.
 *
 * Renders NOTHING outside a trial: a paid account has nothing to be reminded
 * of, and an expired one never gets here (the page redirects it to /billing).
 * Turns urgent in the last few days — `ending_soon` is decided server-side so
 * the threshold lives in one place.
 */
export function TrialBanner({ subscription }: { subscription: SubscriptionInfo }) {
  if (!subscription.is_trialing) return null;

  const urgent = subscription.ending_soon;
  const left =
    subscription.days_left === 1 ? "1 ημέρα" : `${subscription.days_left} ημέρες`;

  return (
    <div
      className={
        urgent
          ? "flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 dark:border-rose-500/30 dark:bg-rose-500/10"
          : "flex flex-wrap items-center justify-between gap-3 rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-3 dark:border-indigo-500/30 dark:bg-indigo-500/10"
      }
    >
      <div className="flex items-center gap-2.5">
        <span
          className={
            urgent
              ? "text-rose-500 dark:text-rose-400"
              : "text-indigo-500 dark:text-indigo-400"
          }
        >
          {urgent ? (
            <CalendarClock className="h-5 w-5" />
          ) : (
            <Sparkles className="h-5 w-5" />
          )}
        </span>
        <div className="leading-tight">
          <div
            className={
              urgent
                ? "text-sm font-semibold text-rose-800 dark:text-rose-300"
                : "text-sm font-semibold text-indigo-800 dark:text-indigo-300"
            }
          >
            {urgent
              ? `Η δοκιμή λήγει σε ${left}`
              : `Δωρεάν δοκιμή — απομένουν ${left}`}
          </div>
          <div
            className={
              urgent
                ? "text-xs text-rose-700 dark:text-rose-300/80"
                : "text-xs text-indigo-700 dark:text-indigo-300/80"
            }
          >
            Μετά τη λήξη η καταχώριση νέων κινήσεων απενεργοποιείται.
          </div>
        </div>
      </div>
      <Link
        href="/billing"
        className={
          urgent
            ? "inline-flex h-9 items-center rounded-lg bg-rose-600 px-3.5 text-xs font-semibold text-white transition hover:bg-rose-500"
            : "inline-flex h-9 items-center rounded-lg bg-indigo-600 px-3.5 text-xs font-semibold text-white transition hover:bg-indigo-500"
        }
      >
        Ενεργοποίηση συνδρομής
      </Link>
    </div>
  );
}
