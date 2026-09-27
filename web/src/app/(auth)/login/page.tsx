"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2, LogIn, AlertCircle, ShieldCheck, Sparkles } from "lucide-react";
import { login, verifyMfa } from "@/lib/api";
import { PasswordField } from "@/components/PasswordField";
import {
  AuthCard,
  authButton,
  authField,
  authLabel,
  authLink,
} from "@/components/auth/AuthCard";

// The public demo tenant provisioned by scripts/seed_demo.py. Not a secret —
// the button below hands it to anyone — but only shown where
// NEXT_PUBLIC_DEMO_LOGIN=1, so a deployment without a seeded demo account
// never offers a login that cannot work. Inlined at BUILD time, like every
// NEXT_PUBLIC_ variable.
const DEMO_ENABLED = process.env.NEXT_PUBLIC_DEMO_LOGIN === "1";
const DEMO_EMAIL = "demo@auditagent.io";
const DEMO_PASSWORD = "DemoPass2026!";

const demoButton =
  "inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-emerald-300/30 bg-emerald-400/10 px-3 py-2.5 text-sm font-semibold text-emerald-200 shadow-lg shadow-emerald-950/30 transition hover:border-emerald-300/50 hover:bg-emerald-400/20 hover:text-emerald-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-300 disabled:cursor-not-allowed disabled:opacity-60";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const from = params.get("from") || "/";

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "error">("idle");
  // Which button started the request, so only that one shows the spinner.
  const [pending, setPending] = useState<"form" | "demo" | null>(null);
  const [message, setMessage] = useState("");
  // Set when the password was right but a second factor is still owed. Holding
  // the challenge here rather than navigating keeps the whole login on one
  // screen — and keeps a token that opens nothing out of the URL.
  const [challenge, setChallenge] = useState("");
  const [trustDays, setTrustDays] = useState(30);
  const [code, setCode] = useState("");
  const [trustDevice, setTrustDevice] = useState(false);

  function done() {
    router.replace(from);
    router.refresh();
  }

  async function signIn(user: string, pass: string, source: "form" | "demo") {
    setStatus("loading");
    setPending(source);
    setMessage("");
    try {
      const result = await login(user.trim(), pass);
      if (result.mfa_required && result.challenge) {
        setChallenge(result.challenge);
        setTrustDays(result.trust_days ?? 30);
        setStatus("idle");
        setPending(null);
        return;
      }
      done();
    } catch (err) {
      setStatus("error");
      setPending(null);
      setMessage(err instanceof Error ? err.message : "Αποτυχία σύνδεσης.");
    }
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    void signIn(username, password, "form");
  }

  function demoLogin() {
    // Filled in visibly so the visitor sees which account they are entering,
    // but signed in with the constants: the state updates above have not
    // rendered yet when signIn runs.
    setUsername(DEMO_EMAIL);
    setPassword(DEMO_PASSWORD);
    void signIn(DEMO_EMAIL, DEMO_PASSWORD, "demo");
  }

  async function submitCode(e: React.FormEvent) {
    e.preventDefault();
    setStatus("loading");
    setMessage("");
    try {
      await verifyMfa(challenge, code.trim(), trustDevice);
      done();
    } catch (err) {
      setStatus("error");
      setMessage(err instanceof Error ? err.message : "Η επαλήθευση απέτυχε.");
    }
  }

  if (challenge) {
    return (
      <AuthCard
        title="Έλεγχος ταυτότητας"
        subtitle="Εισάγετε τον 6ψήφιο κωδικό από την εφαρμογή authenticator"
        footer={
          <p className="mt-4 text-center text-xs text-slate-300">
            <button
              type="button"
              onClick={() => {
                // Back to the password, and the challenge is DISCARDED rather
                // than kept for a retry: a half-finished login left lying
                // around is a credential nobody is watching.
                setChallenge("");
                setCode("");
                setTrustDevice(false);
                setMessage("");
                setStatus("idle");
              }}
              className={authLink}
            >
              Επιστροφή στη σύνδεση
            </button>
          </p>
        }
      >
        <form onSubmit={submitCode} className="space-y-4">
          <div>
            <label className={authLabel} htmlFor="mfa-code">
              Κωδικός επαλήθευσης
            </label>
            <input
              id="mfa-code"
              className={`${authField} text-center text-lg tracking-[0.4em]`}
              value={code}
              onChange={(e) =>
                // Digits only, six of them. Authenticators display "123 456"
                // and people paste exactly that.
                setCode(e.target.value.replace(/\D/g, "").slice(0, 6))
              }
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder="000000"
              autoFocus
            />
          </div>

          <label className="flex cursor-pointer items-start gap-2 text-xs text-slate-200">
            <input
              type="checkbox"
              checked={trustDevice}
              onChange={(e) => setTrustDevice(e.target.checked)}
              className="mt-0.5 h-4 w-4 shrink-0 cursor-pointer rounded accent-sky-400"
            />
            <span>
              Εμπιστοσύνη σε αυτή τη συσκευή για {trustDays} ημέρες
              <span className="mt-0.5 block text-[11px] text-slate-400">
                Δεν θα ζητείται κωδικός επαλήθευσης από αυτόν τον browser. Ο
                κωδικός πρόσβασης εξακολουθεί να απαιτείται.
              </span>
            </span>
          </label>

          {message ? (
            <div className="flex items-center gap-1.5 text-xs text-rose-300">
              <AlertCircle className="h-4 w-4 shrink-0" />
              {message}
            </div>
          ) : null}

          <button
            type="submit"
            disabled={status === "loading" || code.length < 6}
            className={authButton}
          >
            {status === "loading" ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <ShieldCheck className="h-4 w-4" />
            )}
            Επαλήθευση
          </button>
        </form>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title={
        <>
          Σύνδεση στο Λογιστήριο<span className="text-sky-300">Pro</span>
        </>
      }
      subtitle="Εισάγετε τα στοιχεία του λογαριασμού σας"
      footer={
        <p className="mt-4 text-center text-xs text-slate-300">
          Δεν έχετε λογαριασμό;{" "}
          <Link href="/register" className={authLink}>
            Εγγραφή
          </Link>
        </p>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label className={authLabel} htmlFor="login-username">
            Όνομα χρήστη
          </label>
          <input
            id="login-username"
            className={authField}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
          />
        </div>
        <div>
          <PasswordField
            id="login-password"
            label="Κωδικός"
            className={authField}
            labelClassName={authLabel}
            value={password}
            onChange={setPassword}
            autoComplete="current-password"
          />
          <div className="mt-1 text-right">
            <Link
              href="/reset-password"
              className="text-[11px] font-medium text-sky-300 transition hover:text-sky-200 hover:underline"
            >
              Ξεχάσατε τον κωδικό;
            </Link>
          </div>
        </div>

        {message ? (
          <div className="flex items-center gap-1.5 text-xs text-rose-300">
            <AlertCircle className="h-4 w-4 shrink-0" />
            {message}
          </div>
        ) : null}

        <button type="submit" disabled={status === "loading"} className={authButton}>
          {pending === "form" ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <LogIn className="h-4 w-4" />
          )}
          Σύνδεση
        </button>

        {DEMO_ENABLED ? (
          <>
            <div className="flex items-center gap-3 text-[11px] uppercase tracking-wider text-slate-400">
              <span className="h-px flex-1 bg-white/10" />
              ή
              <span className="h-px flex-1 bg-white/10" />
            </div>
            <button
              type="button"
              onClick={demoLogin}
              disabled={status === "loading"}
              className={demoButton}
            >
              {pending === "demo" ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Sparkles className="h-4 w-4" />
              )}
              1-Click Demo Login
            </button>
            <p className="text-center text-[11px] text-slate-400">
              Κοινόχρηστος λογαριασμός επίδειξης με δείγματα δεδομένων
            </p>
          </>
        ) : null}
      </form>
    </AuthCard>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
