"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ChevronDown, CreditCard, LogOut } from "lucide-react";
import { logout } from "@/lib/api";

/**
 * The account dropdown in the top-right: who is signed in, their subscription,
 * and the way out.
 *
 * Written by hand rather than pulled from a headless-UI dependency, because a
 * menu with two items needs four behaviours and all four are short:
 *
 *   * click outside closes it — `pointerdown` rather than `click`, so a click
 *     that lands on a button elsewhere closes this first instead of firing
 *     with the menu still open on top;
 *   * Escape closes it AND returns focus to the trigger, so a keyboard user is
 *     not dropped back at the top of the document;
 *   * the trigger carries aria-haspopup/aria-expanded, so it is announced as a
 *     menu rather than as an unexplained button;
 *   * navigating closes it — Next keeps this component mounted across a client
 *     -side route change, so an open menu would otherwise stay open on the page
 *     it just navigated to.
 *
 * Logout lives here rather than as its own header button: it is the one
 * destructive control in the chrome, and one accidental click away from the
 * theme toggle was too close.
 */
export function UserMenu({ username }: { username: string | null }) {
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  // Close on any route change — including the browser's back button, which no
  // click handler in here would ever see.
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!open) return;

    function onPointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }

    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  async function onLogout() {
    setBusy(true);
    await logout();
    setOpen(false);
    router.replace("/login");
    router.refresh();
  }

  const label = username || "Λογαριασμός";
  // The first letter as an avatar. Greek usernames uppercase correctly through
  // toLocaleUpperCase with the document's locale; toUpperCase does not always.
  const initial = label.slice(0, 1).toLocaleUpperCase("el-GR");

  const item =
    "flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-800";

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-200 bg-white pl-1.5 pr-2 text-slate-600 transition hover:border-slate-300 hover:text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-slate-600 dark:hover:text-white"
      >
        <span
          aria-hidden
          className="flex h-6 w-6 items-center justify-center rounded-md bg-indigo-600 text-[11px] font-bold text-white"
        >
          {initial}
        </span>
        <span className="hidden max-w-[10rem] truncate text-xs font-medium sm:inline">
          {label}
        </span>
        <ChevronDown className="h-3.5 w-3.5 opacity-60" aria-hidden />
      </button>

      {open ? (
        <div
          role="menu"
          aria-label="Μενού λογαριασμού"
          className="absolute right-0 z-40 mt-1.5 w-56 overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-lg dark:border-slate-700 dark:bg-slate-900"
        >
          <div className="border-b border-slate-100 px-3 py-2 dark:border-slate-800">
            <div className="text-[11px] text-slate-400 dark:text-slate-500">
              Συνδεδεμένος ως
            </div>
            <div className="truncate text-sm font-semibold text-slate-900 dark:text-white">
              {label}
            </div>
          </div>

          <Link
            href="/billing"
            role="menuitem"
            onClick={() => setOpen(false)}
            className={item}
          >
            <CreditCard className="h-4 w-4 text-slate-400" />
            Συνδρομή & χρέωση
          </Link>

          <button
            type="button"
            role="menuitem"
            onClick={onLogout}
            disabled={busy}
            className={`${item} text-rose-600 hover:bg-rose-50 disabled:opacity-60 dark:text-rose-400 dark:hover:bg-rose-500/10`}
          >
            <LogOut className="h-4 w-4" />
            {busy ? "Αποσύνδεση…" : "Έξοδος"}
          </button>
        </div>
      ) : null}
    </div>
  );
}
