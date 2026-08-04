"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2, UserPlus, AlertCircle } from "lucide-react";
import { register, MIN_PASSWORD_LENGTH, TRIAL_DAYS } from "@/lib/api";
import { PasswordField } from "@/components/PasswordField";
import {
  AuthCard,
  authButton,
  authField,
  authHint,
  authLabel,
  authLink,
} from "@/components/auth/AuthCard";

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

  return (
    <AuthCard
      title="Δημιουργία λογαριασμού"
      subtitle={`Ξεκινήστε με δωρεάν δοκιμή ${TRIAL_DAYS} ημερών · χωρίς κάρτα`}
      footer={
        <p className="mt-4 text-center text-xs text-slate-300">
          Έχετε ήδη λογαριασμό;{" "}
          <Link href="/login" className={authLink}>
            Σύνδεση
          </Link>
        </p>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label className={authLabel} htmlFor="reg-username">
            Όνομα χρήστη
          </label>
          <input
            id="reg-username"
            className={authField}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
          />
        </div>
        <div>
          <label className={authLabel} htmlFor="reg-email">
            Email
          </label>
          <input
            id="reg-email"
            type="email"
            className={authField}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
          />
        </div>
        <PasswordField
          id="reg-password"
          label="Κωδικός"
          className={authField}
          labelClassName={authLabel}
          hintClassName={authHint}
          value={password}
          onChange={setPassword}
          autoComplete="new-password"
          hint={`Τουλάχιστον ${MIN_PASSWORD_LENGTH} χαρακτήρες.`}
        />
        <PasswordField
          id="reg-confirm"
          label="Επιβεβαίωση κωδικού"
          className={authField}
          labelClassName={authLabel}
          value={confirm}
          onChange={setConfirm}
          autoComplete="new-password"
        />

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
            <UserPlus className="h-4 w-4" />
          )}
          Εγγραφή
        </button>
      </form>
    </AuthCard>
  );
}
