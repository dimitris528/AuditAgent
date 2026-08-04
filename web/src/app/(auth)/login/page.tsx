"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2, LogIn, AlertCircle } from "lucide-react";
import { login } from "@/lib/api";
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

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setStatus("loading");
    setMessage("");
    try {
      await login(username.trim(), password);
      router.replace(from);
      router.refresh();
    } catch (err) {
      setStatus("error");
      setMessage(err instanceof Error ? err.message : "Αποτυχία σύνδεσης.");
    }
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
