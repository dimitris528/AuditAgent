"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2, UserPlus, AlertCircle } from "lucide-react";
import { register, MIN_PASSWORD_LENGTH, TRIAL_DAYS } from "@/lib/api";
import { CONSENT_VERSION } from "@/lib/legal";
import { PasswordField } from "@/components/PasswordField";
import {
  AuthCard,
  authButton,
  authField,
  authHint,
  authLabel,
  authLink,
} from "@/components/auth/AuthCard";

/**
 * Public tenant onboarding.
 *
 * The form asks for the OFFICE first and the person second, because that is
 * what is being created: a tenant, with the visitor as its first admin. It
 * deliberately does not ask for a username — the API derives one from the email
 * — since every field on a signup form is a reason to abandon it, and this one
 * would be asking a visitor to invent an identifier they will never type again.
 */
export default function RegisterPage() {
  const router = useRouter();

  const [companyName, setCompanyName] = useState("");
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [acceptTerms, setAcceptTerms] = useState(false);
  const [status, setStatus] = useState<"idle" | "loading" | "error">("idle");
  const [message, setMessage] = useState("");

  // Checked here purely for immediate feedback. The server enforces all of it
  // independently — length, strength, email validity, uniqueness and the
  // consent — and its message is what gets shown when the two disagree.
  function clientSideProblem(): string | null {
    if (companyName.trim().length < 2)
      return "Συμπληρώστε την επωνυμία του γραφείου ή της εταιρείας.";
    if (fullName.trim().length < 2) return "Συμπληρώστε το ονοματεπώνυμό σας.";
    if (!email.includes("@")) return "Δώστε μια έγκυρη διεύθυνση email.";
    if (password.length < MIN_PASSWORD_LENGTH)
      return `Ο κωδικός θέλει τουλάχιστον ${MIN_PASSWORD_LENGTH} χαρακτήρες.`;
    if (password !== confirm) return "Οι κωδικοί δεν ταιριάζουν.";
    if (!acceptTerms)
      return "Πρέπει να αποδεχθείτε τους Όρους Χρήσης και την Πολιτική Απορρήτου.";
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
      await register({
        company_name: companyName.trim(),
        full_name: fullName.trim(),
        email: email.trim(),
        password,
        accept_terms: acceptTerms,
        // The version THIS page is displaying, so the stored consent records
        // what was on screen rather than what the server assumed — the two
        // differ for exactly as long as it takes a cached page to be replaced.
        terms_version: CONSENT_VERSION,
      });
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
          <label className={authLabel} htmlFor="reg-company">
            Επωνυμία γραφείου / εταιρείας
          </label>
          <input
            id="reg-company"
            className={authField}
            value={companyName}
            onChange={(e) => setCompanyName(e.target.value)}
            placeholder="π.χ. Λογιστικό Γραφείο Παπαδοπούλου"
            autoComplete="organization"
            autoFocus
          />
          <p className={authHint}>
            Εμφανίζεται στις καρτέλες πελατών που εκτυπώνετε.
          </p>
        </div>
        <div>
          <label className={authLabel} htmlFor="reg-fullname">
            Ονοματεπώνυμο
          </label>
          <input
            id="reg-fullname"
            className={authField}
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            placeholder="π.χ. Μαρία Παπαδοπούλου"
            autoComplete="name"
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
          <p className={authHint}>Με αυτό θα συνδέεστε στον λογαριασμό σας.</p>
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
          hint={`Τουλάχιστον ${MIN_PASSWORD_LENGTH} χαρακτήρες. Μην χρησιμοποιήσετε το email ή την επωνυμία σας.`}
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

        {/* The consent, and the two links it is consent TO. They open in a new
            tab on purpose: sending someone away from a half-filled signup form
            to read a privacy policy is how a form gets abandoned. */}
        <div className="flex items-start gap-2 rounded-lg border border-white/10 bg-white/[0.03] p-3">
          <input
            id="reg-terms"
            type="checkbox"
            checked={acceptTerms}
            onChange={(e) => setAcceptTerms(e.target.checked)}
            className="mt-0.5 h-4 w-4 shrink-0 cursor-pointer rounded border-white/30 bg-white/10 accent-sky-500"
          />
          <label htmlFor="reg-terms" className="cursor-pointer text-xs text-slate-300">
            Αποδέχομαι τους{" "}
            <Link href="/terms" target="_blank" className={authLink}>
              Όρους Χρήσης
            </Link>{" "}
            και την{" "}
            <Link href="/privacy" target="_blank" className={authLink}>
              Πολιτική Απορρήτου
            </Link>
            , συμπεριλαμβανομένης της επεξεργασίας των δεδομένων των πελατών μου
            σύμφωνα με τον GDPR.
          </label>
        </div>

        {message ? (
          <div className="flex items-start gap-1.5 text-xs text-rose-300">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
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
