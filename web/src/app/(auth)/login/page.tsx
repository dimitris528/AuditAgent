"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2, LogIn, AlertCircle, ShieldCheck } from "lucide-react";
import { login, verifyMfa } from "@/lib/api";
import { PasswordField } from "@/components/PasswordField";
import {
  AuthCard,
  authButton,
  authField,
  authLabel,
  authLink,
} from "@/components/auth/AuthCard";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const from = params.get("from") || "/";

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "error">("idle");
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

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setStatus("loading");
    setMessage("");
    try {
      const result = await login(username.trim(), password);
      if (result.mfa_required && result.challenge) {
        setChallenge(result.challenge);
        setTrustDays(result.trust_days ?? 30);
        setStatus("idle");
        return;
      }
      done();
    } catch (err) {
      setStatus("error");
      setMessage(err instanceof Error ? err.message : "Αποτυχία σύνδεσης.");
    }
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
          {status === "loading" ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <LogIn className="h-4 w-4" />
          )}
          Σύνδεση
        </button>
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
