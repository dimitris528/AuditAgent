"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { LogOut } from "lucide-react";
import { logout } from "@/lib/api";

export function LogoutButton({ username }: { username?: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  async function onClick() {
    setBusy(true);
    await logout();
    router.replace("/login");
    router.refresh();
  }

  return (
    <div className="flex items-center gap-2">
      {username ? (
        <span className="hidden text-xs font-medium text-slate-500 sm:inline dark:text-slate-400">
          {username}
        </span>
      ) : null}
      <button
        type="button"
        onClick={onClick}
        disabled={busy}
        title="Αποσύνδεση"
        aria-label="Αποσύνδεση"
        className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-slate-600 transition hover:border-slate-300 hover:text-slate-900 disabled:opacity-60 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-slate-600 dark:hover:text-white"
      >
        <LogOut className="h-4 w-4" />
        <span className="hidden sm:inline">Έξοδος</span>
      </button>
    </div>
  );
}
