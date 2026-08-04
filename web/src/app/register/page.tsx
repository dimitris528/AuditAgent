"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Calculator, Loader2, UserPlus, AlertCircle } from "lucide-react";
import { register, MIN_PASSWORD_LENGTH, TRIAL_DAYS } from "@/lib/api";
import { PasswordField } from "@/components/PasswordField";

export default function RegisterPage() {
  const router = useRouter();

  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "error">("idle");
  const [message, setMessage] = useState("");

  // Checked here purely for immediate feedback; the server enforces length,
  // uniqueness and email validity independently.
  function clientSideProblem(): string | null {
    if (username.trim().length < 3) return "Το όνομα χρήστη θέλει τουλάχιστον 3 χαρακτήρες.";
    if (!email.includes("@")) return "Δώστε μια έγκυρη διεύθυνση email.";
    if (password.length < MIN_PASSWORD_LENGTH)
      return `Ο κωδικός θέλει τουλάχιστον ${MIN_PASSWORD_LENGTH} χαρακτήρες.`;
    if (password !== confirm) return "Οι κωδικοί δεν ταιριάζουν.";
    return null;
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const problem = clientSideProblem();
    if (problem) {
      setStatus("error");
      setMessage(problem);
      return;
    }
    setStatus("loading");
    setMessage("");
    try {
      await register(username.trim(), email.trim(), password);
      // Registration returns a session cookie, so go straight to the dashboard.
      router.replace("/");
      router.refresh();
    } catch (err) {
      setStatus("error");
      setMessage(err instanceof Error ? err.message : "Αποτυχία εγγραφής.");
    }
  }

  const field =
    "w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm text-slate-900 outline-none transition focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 dark:border-slate-700 dark:bg-slate-800 dark:text-white";
  const labelCls =
    "mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400";

  return (
    <div className="flex min-h-[calc(100vh-8rem)] items-center justify-center">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center text-center">
          <span className="mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-indigo-600 text-white">
            <Calculator className="h-6 w-6" />
          </span>
          <h1 className="text-lg font-bold text-slate-900 dark:text-white">
            Δημιουργία λογαριασμού
          </h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Ξεκινήστε με δωρεάν δοκιμή {TRIAL_DAYS} ημερών · χωρίς κάρτα
          </p>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-card dark:border-slate-800 dark:bg-slate-900 dark:shadow-card-dark">
          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className={labelCls} htmlFor="reg-username">
                Όνομα χρήστη
              </label>
              <input
                id="reg-username"
                className={field}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                autoFocus
              />
            </div>
            <div>
              <label className={labelCls} htmlFor="reg-email">
                Email
              </label>
              <input
                id="reg-email"
                type="email"
                className={field}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
              />
            </div>
            <PasswordField
              id="reg-password"
              label="Κωδικός"
              className={field}
              value={password}
              onChange={setPassword}
              autoComplete="new-password"
              hint={`Τουλάχιστον ${MIN_PASSWORD_LENGTH} χαρακτήρες.`}
            />
            <PasswordField
              id="reg-confirm"
              label="Επιβεβαίωση κωδικού"
              className={field}
              value={confirm}
              onChange={setConfirm}
              autoComplete="new-password"
            />

            {message ? (
              <div className="flex items-center gap-1.5 text-xs text-rose-600 dark:text-rose-400">
                <AlertCircle className="h-4 w-4 shrink-0" />
                {message}
              </div>
            ) : null}

            <button
              type="submit"
              disabled={status === "loading"}
              className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2.5 text-sm font-semibold text-white transition hover:bg-indigo-500 disabled:opacity-60"
            >
              {status === "loading" ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <UserPlus className="h-4 w-4" />
              )}
              Εγγραφή
            </button>
          </form>
        </div>

        <p className="mt-4 text-center text-xs text-slate-500 dark:text-slate-400">
          Έχετε ήδη λογαριασμό;{" "}
          <Link
            href="/login"
            className="font-semibold text-indigo-600 hover:underline dark:text-indigo-400"
          >
            Σύνδεση
          </Link>
        </p>
      </div>
    </div>
  );
}
