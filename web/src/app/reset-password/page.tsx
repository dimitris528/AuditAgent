"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertCircle,
  Calculator,
  CheckCircle2,
  KeyRound,
  Loader2,
  Mail,
} from "lucide-react";
import {
  forgotPassword,
  resetPassword,
  MIN_PASSWORD_LENGTH,
} from "@/lib/api";
import { PasswordField } from "@/components/PasswordField";

// One route, two jobs, chosen by whether the URL carries a token:
//
//   /reset-password            → "email me a link"
//   /reset-password?token=...  → "set a new password"
//
// Together rather than split across two routes because they are two steps of
// one errand, and the second is only ever reached from a link in the first's
// email — a separate /forgot-password URL would be a page nobody navigates to
// twice.

const field =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm text-slate-900 outline-none transition focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 dark:border-slate-700 dark:bg-slate-800 dark:text-white";
const labelCls = "mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400";

function Shell({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <div className="flex min-h-[calc(100vh-8rem)] items-center justify-center">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center text-center">
          <span className="mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-indigo-600 text-white">
            <Calculator className="h-6 w-6" />
          </span>
          <h1 className="text-lg font-bold text-slate-900 dark:text-white">
            {title}
          </h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            {subtitle}
          </p>
        </div>
        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-card dark:border-slate-800 dark:bg-slate-900 dark:shadow-card-dark">
          {children}
        </div>
        {footer}
      </div>
    </div>
  );
}

function Problem({ message }: { message: string }) {
  if (!message) return null;
  return (
    <div className="flex items-start gap-1.5 text-xs text-rose-600 dark:text-rose-400">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
      {message}
    </div>
  );
}

const backToLogin = (
  <p className="mt-4 text-center text-xs text-slate-500 dark:text-slate-400">
    Θυμηθήκατε τον κωδικό;{" "}
    <Link
      href="/login"
      className="font-semibold text-indigo-600 hover:underline dark:text-indigo-400"
    >
      Σύνδεση
    </Link>
  </p>
);

/** Step 1 — ask for the link. */
function RequestLink() {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "sent" | "error">("idle");
  const [message, setMessage] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setStatus("loading");
    setMessage("");
    try {
      await forgotPassword(email.trim());
      setStatus("sent");
    } catch (err) {
      setStatus("error");
      setMessage(err instanceof Error ? err.message : "Κάτι πήγε στραβά.");
    }
  }

  if (status === "sent") {
    return (
      <Shell
        title="Ελέγξτε το email σας"
        subtitle="Αν υπάρχει λογαριασμός, ο σύνδεσμος είναι καθ' οδόν"
        footer={backToLogin}
      >
        <div className="flex flex-col items-center gap-3 text-center">
          <span className="flex h-11 w-11 items-center justify-center rounded-full bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-400">
            <CheckCircle2 className="h-6 w-6" />
          </span>
          {/* Worded to match what the server actually promises. The backend
              answers identically for a known and an unknown address so it
              cannot be used to discover who has an account — saying "we sent
              it" here would leak exactly what that protects. */}
          <p className="text-sm text-slate-600 dark:text-slate-300">
            Αν υπάρχει λογαριασμός με το <strong>{email.trim()}</strong>, θα
            λάβετε σύνδεσμο επαναφοράς. Ο σύνδεσμος λήγει σε 30 λεπτά.
          </p>
          <p className="text-xs text-slate-400 dark:text-slate-500">
            Δεν ήρθε τίποτα; Ελέγξτε τα ανεπιθύμητα ή δοκιμάστε ξανά.
          </p>
        </div>
      </Shell>
    );
  }

  return (
    <Shell
      title="Επαναφορά κωδικού"
      subtitle="Θα σας στείλουμε σύνδεσμο για νέο κωδικό"
      footer={backToLogin}
    >
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label className={labelCls} htmlFor="fp-email">
            Email λογαριασμού
          </label>
          <input
            id="fp-email"
            type="email"
            required
            className={field}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            autoFocus
          />
        </div>
        <Problem message={message} />
        <button
          type="submit"
          disabled={status === "loading"}
          className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2.5 text-sm font-semibold text-white transition hover:bg-indigo-500 disabled:opacity-60"
        >
          {status === "loading" ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Mail className="h-4 w-4" />
          )}
          Αποστολή συνδέσμου
        </button>
      </form>
    </Shell>
  );
}

/** Step 2 — the link was followed; choose the new password. */
function ChooseNewPassword({ token }: { token: string }) {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [message, setMessage] = useState("");

  // Immediate feedback only; the server enforces both independently.
  function clientSideProblem(): string | null {
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
      await resetPassword(token, password);
      setStatus("done");
      // Straight to login rather than signing them in: the reset issues no
      // session, and typing the new password once is what proves it is the one
      // they meant to set.
      setTimeout(() => router.replace("/login"), 1600);
    } catch (err) {
      setStatus("error");
      setMessage(err instanceof Error ? err.message : "Αποτυχία επαναφοράς.");
    }
  }

  if (status === "done") {
    return (
      <Shell title="Ο κωδικός άλλαξε" subtitle="Μπορείτε να συνδεθείτε">
        <div className="flex flex-col items-center gap-3 text-center">
          <span className="flex h-11 w-11 items-center justify-center rounded-full bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-400">
            <CheckCircle2 className="h-6 w-6" />
          </span>
          <p className="text-sm text-slate-600 dark:text-slate-300">
            Ο κωδικός σας ενημερώθηκε. Μεταφέρεστε στη σύνδεση…
          </p>
          <Link
            href="/login"
            className="text-xs font-semibold text-indigo-600 hover:underline dark:text-indigo-400"
          >
            Μετάβαση τώρα
          </Link>
        </div>
      </Shell>
    );
  }

  return (
    <Shell
      title="Νέος κωδικός"
      subtitle="Επιλέξτε έναν κωδικό που δεν έχετε ξαναχρησιμοποιήσει"
      footer={backToLogin}
    >
      <form onSubmit={submit} className="space-y-4">
        <PasswordField
          id="rp-password"
          label="Νέος κωδικός"
          className={field}
          value={password}
          onChange={setPassword}
          autoComplete="new-password"
          autoFocus
          hint={`Τουλάχιστον ${MIN_PASSWORD_LENGTH} χαρακτήρες.`}
        />
        <PasswordField
          id="rp-confirm"
          label="Επιβεβαίωση κωδικού"
          className={field}
          value={confirm}
          onChange={setConfirm}
          autoComplete="new-password"
        />
        <Problem message={message} />
        <button
          type="submit"
          disabled={status === "loading"}
          className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2.5 text-sm font-semibold text-white transition hover:bg-indigo-500 disabled:opacity-60"
        >
          {status === "loading" ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <KeyRound className="h-4 w-4" />
          )}
          Αποθήκευση κωδικού
        </button>
      </form>
    </Shell>
  );
}

function ResetPasswordRoute() {
  const token = useSearchParams().get("token");
  return token ? <ChooseNewPassword token={token} /> : <RequestLink />;
}

export default function ResetPasswordPage() {
  // useSearchParams needs a Suspense boundary in the app router.
  return (
    <Suspense fallback={null}>
      <ResetPasswordRoute />
    </Suspense>
  );
}
