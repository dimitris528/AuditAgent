"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Cookie, X } from "lucide-react";

/**
 * A cookie NOTICE, not a consent gate — and the distinction is the whole design.
 *
 * This application sets three things in the browser and no more:
 *
 *   * `session`      — the httpOnly JWT that signs you in;
 *   * `device_trust` — an httpOnly, opt-in token that skips the 2FA prompt on a
 *                      browser you told us to trust, set only when you tick
 *                      that box;
 *   * a `theme` entry in localStorage, so the page does not flash white.
 *
 * All three are strictly necessary to provide a service the user explicitly
 * asked for, which ePrivacy (Art. 5(3), as implemented by Ν. 3471/2006) exempts
 * from prior consent. There is no analytics cookie, no advertising pixel and no
 * third-party tag anywhere in this app — so a banner demanding "Accept" before
 * anything works would be asking permission for something that needs none, and
 * training people to click through the ones that do.
 *
 * What is owed is INFORMATION, plainly given and dismissible. That is this.
 *
 * Dismissal is remembered in localStorage rather than a cookie, so the act of
 * acknowledging the notice does not itself set the thing being disclosed.
 */
const STORAGE_KEY = "cookie-notice-acknowledged";

export function CookieNotice() {
  // Starts hidden and is shown only after the effect runs. Rendering it during
  // SSR would put the banner in the HTML for someone who dismissed it months
  // ago and flash it on every navigation — localStorage does not exist on the
  // server, so the first paint cannot know.
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    try {
      if (!window.localStorage.getItem(STORAGE_KEY)) setVisible(true);
    } catch {
      // Private browsing, or storage disabled entirely. Showing the notice
      // every time is the correct failure: it is information, not a gate.
      setVisible(true);
    }
  }, []);

  function dismiss() {
    setVisible(false);
    try {
      window.localStorage.setItem(STORAGE_KEY, "1");
    } catch {
      /* Nothing to do — the notice simply reappears next visit. */
    }
  }

  if (!visible) return null;

  return (
    <div
      // `polite`, not `alert`: it is worth reading, not worth interrupting a
      // screen-reader user mid-sentence for.
      role="status"
      aria-live="polite"
      className="fixed inset-x-0 bottom-0 z-50 flex justify-center p-3 sm:p-4"
    >
      <div className="flex w-full max-w-2xl items-start gap-3 rounded-xl border border-white/10 bg-slate-900/90 p-3.5 text-xs text-slate-300 shadow-2xl shadow-slate-950/50 backdrop-blur-xl sm:items-center">
        <Cookie className="mt-0.5 h-4 w-4 shrink-0 text-sky-300 sm:mt-0" aria-hidden="true" />
        <p className="flex-1 leading-relaxed">
          Χρησιμοποιούμε <strong className="font-semibold text-slate-100">μόνο
          απολύτως απαραίτητα cookies</strong> για τη σύνδεση και την ασφάλεια
          του λογαριασμού σας. Δεν κάνουμε ανάλυση επισκεψιμότητας ούτε
          διαφημιστική παρακολούθηση.{" "}
          <Link
            href="/privacy#cookies"
            className="font-semibold text-sky-300 underline-offset-2 hover:underline"
          >
            Περισσότερα
          </Link>
        </p>
        <button
          type="button"
          onClick={dismiss}
          aria-label="Απόκρυψη ενημέρωσης για cookies"
          className="shrink-0 rounded-lg border border-white/10 px-2.5 py-1.5 font-semibold text-slate-200 transition hover:bg-white/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300"
        >
          <span className="hidden sm:inline">Το κατάλαβα</span>
          <X className="h-3.5 w-3.5 sm:hidden" aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}
