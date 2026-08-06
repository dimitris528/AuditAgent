import { redirect } from "next/navigation";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowLeft,
  CalendarClock,
  CheckCircle2,
  CreditCard,
  Lock,
  ShieldCheck,
} from "lucide-react";
import { getBillingStatus } from "@/lib/server-api";
import { ApiError } from "@/lib/errors";
import type { BillingStatus } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { SubscribeButton } from "@/components/SubscribeButton";
import { ManageBillingButton } from "@/components/ManageBillingButton";
import { CancelSubscriptionButton } from "@/components/CancelSubscriptionButton";

// The one page a lapsed tenant can always reach, so it must never be cached
// with someone else's status.
export const dynamic = "force-dynamic";

/** "2026-08-18T09:30:00Z" → "18 Αυγ 2026". */
function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("el-GR", {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(d);
}

/** Greek needs the singular for exactly one. */
function days(n: number): string {
  return n === 1 ? "1 ημέρα" : `${n} ημέρες`;
}

const PRESENTATION = {
  trialing: {
    tone: "debt" as const,
    label: "Δοκιμαστική περίοδος",
    icon: <CalendarClock className="h-3 w-3" />,
  },
  active: {
    tone: "success" as const,
    label: "Ενεργή συνδρομή",
    icon: <CheckCircle2 className="h-3 w-3" />,
  },
  inactive: {
    tone: "warning" as const,
    label: "Ανενεργή",
    icon: <Lock className="h-3 w-3" />,
  },
};

/** Pending cancellation is not one of the three stored statuses — the account
 *  is still `active` and still paid up — so it gets its own badge rather than
 *  being shown as an ordinary active subscription. */
const CANCELLING = {
  tone: "warning" as const,
  label: "Ακύρωση σε εκκρεμότητα",
  icon: <CalendarClock className="h-3 w-3" />,
};

/** The headline + explanation for each state. */
function summary(s: BillingStatus): { title: string; body: string } {
  // Checked before the status, which still reads "active": what the user needs
  // to know is the date it stops, not that it currently works.
  if (s.pending_cancellation) {
    return {
      title: s.cancel_at
        ? `Η συνδρομή λήγει στις ${formatDate(s.cancel_at)}`
        : "Η συνδρομή σας έχει ακυρωθεί",
      body: "Η ακύρωση καταχωρήθηκε και δεν θα υπάρξει νέα χρέωση. Έχετε "
        + "πλήρη πρόσβαση μέχρι τη λήξη της περιόδου που έχετε ήδη πληρώσει. "
        + "Μπορείτε να επαναφέρετε τη συνδρομή οποτεδήποτε πριν από τότε, "
        + "μέσω της Διαχείρισης συνδρομής.",
    };
  }
  switch (s.status) {
    case "trialing":
      return {
        title: `Απομένουν ${days(s.days_left)} δωρεάν δοκιμής`,
        body: `Η δοκιμαστική περίοδος λήγει στις ${formatDate(s.trial_ends_at)}. `
          + "Μέχρι τότε έχετε πλήρη πρόσβαση σε όλες τις λειτουργίες. "
          + "Ενεργοποιήστε συνδρομή για να συνεχίσετε χωρίς διακοπή.",
      };
    case "active":
      return {
        title: "Η συνδρομή σας είναι ενεργή",
        body: "Έχετε πλήρη πρόσβαση σε όλες τις λειτουργίες. "
          + "Ευχαριστούμε που μας εμπιστεύεστε.",
      };
    default:
      return {
        title: s.trial_ends_at
          ? `Η δοκιμαστική περίοδος έληξε στις ${formatDate(s.trial_ends_at)}`
          : "Ο λογαριασμός σας είναι ανενεργός",
        body: "Μπορείτε ακόμη να δείτε τα στοιχεία σας, αλλά η καταχώριση νέων "
          + "κινήσεων και πελατών είναι απενεργοποιημένη. Ενεργοποιήστε "
          + "συνδρομή για να ξεκλειδώσετε ξανά τον λογαριασμό.",
      };
  }
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2.5">
      <dt className="text-xs text-slate-500 dark:text-slate-400">{label}</dt>
      <dd className="text-sm font-medium text-slate-900 dark:text-slate-100">
        {value}
      </dd>
    </div>
  );
}

/** The banner Stripe's redirect lands on. */
function CheckoutOutcome({ outcome, status }: { outcome: string; status: string }) {
  if (outcome === "cancelled") {
    return (
      <div className="flex items-start gap-2 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
        Η πληρωμή ακυρώθηκε. Δεν χρεωθήκατε.
      </div>
    );
  }
  if (outcome !== "success") return null;
  // Returning from a successful checkout does NOT mean the account is active
  // yet — Stripe's webhook is what flips it, and it can land a second or two
  // later. Saying so is better than showing a status that contradicts the
  // receipt the user just saw.
  const settled = status === "active";
  return (
    <div className="flex items-start gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300">
      <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
      {settled
        ? "Η πληρωμή ολοκληρώθηκε και η συνδρομή σας είναι ενεργή."
        : "Η πληρωμή ολοκληρώθηκε. Η ενεργοποίηση γίνεται σε λίγα δευτερόλεπτα — "
          + "ανανεώστε τη σελίδα αν η κατάσταση δεν έχει ενημερωθεί."}
    </div>
  );
}

export default async function BillingPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const outcomeParam = Array.isArray(sp.checkout) ? sp.checkout[0] : sp.checkout;

  let status: BillingStatus;
  try {
    status = await getBillingStatus();
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) redirect("/login");
    return (
      <div className="mx-auto mt-10 max-w-xl rounded-2xl border border-rose-200 bg-rose-50 p-6 text-center dark:border-rose-500/30 dark:bg-rose-500/10">
        <AlertTriangle className="mx-auto h-8 w-8 text-rose-500" />
        <h2 className="mt-3 text-lg font-semibold text-rose-800 dark:text-rose-300">
          Δεν ήταν δυνατή η φόρτωση της συνδρομής
        </h2>
        <p className="mt-1 text-sm text-rose-700 dark:text-rose-300/80">
          {err instanceof Error ? err.message : "Άγνωστο σφάλμα."}
        </p>
      </div>
    );
  }

  const pending = Boolean(status.pending_cancellation);
  const look = pending
    ? CANCELLING
    : PRESENTATION[status.status] ?? PRESENTATION.inactive;
  const { title, body } = summary(status);
  const canSubscribe = status.status !== "active";

  // The portal opens for every status — the backend creates the Stripe customer
  // if there is not one yet — so the only thing that can hide this button is a
  // server with no Stripe key at all.
  const showManage = Boolean(status.portal_enabled);
  // A live Stripe subscription that has not already been cancelled. False on a
  // free trial: that is ours, not Stripe's, and there is nothing to cancel.
  const showCancel = Boolean(status.can_cancel);

  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-slate-900 dark:text-white">
            Συνδρομή
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Κατάσταση λογαριασμού και χρέωση
          </p>
        </div>
        <Link
          href="/"
          className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-xs font-medium text-slate-600 transition hover:border-slate-300 hover:text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:text-white"
        >
          <ArrowLeft className="h-4 w-4" />
          Πίνακας ελέγχου
        </Link>
      </div>

      {outcomeParam ? (
        <CheckoutOutcome outcome={outcomeParam} status={status.status} />
      ) : null}

      <Card className="p-6">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <Badge tone={look.tone} icon={look.icon}>
              {look.label}
            </Badge>
            <h2 className="mt-3 text-lg font-semibold text-slate-900 dark:text-white">
              {title}
            </h2>
            <p className="mt-1.5 text-sm leading-relaxed text-slate-600 dark:text-slate-300">
              {body}
            </p>
          </div>
          <span
            className={
              pending
                ? "hidden h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-amber-50 text-amber-600 sm:flex dark:bg-amber-500/10 dark:text-amber-400"
                : "hidden h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-indigo-50 text-indigo-600 sm:flex dark:bg-indigo-500/10 dark:text-indigo-400"
            }
          >
            {pending ? (
              <CalendarClock className="h-6 w-6" />
            ) : status.status === "active" ? (
              <ShieldCheck className="h-6 w-6" />
            ) : (
              <CreditCard className="h-6 w-6" />
            )}
          </span>
        </div>

        {status.is_trialing ? (
          <div className="mt-5">
            <div className="mb-1.5 flex items-center justify-between text-[11px] text-slate-500 dark:text-slate-400">
              <span>Δοκιμαστική περίοδος</span>
              <span>
                {days(status.days_left)} από {status.trial_days}
              </span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
              <div
                className={
                  status.ending_soon
                    ? "h-full rounded-full bg-rose-500"
                    : "h-full rounded-full bg-indigo-500"
                }
                style={{
                  width: `${Math.max(
                    2,
                    Math.min(100, (status.days_left / status.trial_days) * 100),
                  )}%`,
                }}
              />
            </div>
          </div>
        ) : null}

        <dl className="mt-5 divide-y divide-slate-100 border-t border-slate-100 dark:divide-slate-800 dark:border-slate-800">
          <Row label="Λογαριασμός" value={status.username} />
          {status.email ? <Row label="Email" value={status.email} /> : null}
          <Row
            label="Καταχώριση εγγραφών"
            value={
              status.allows_writes ? (
                <span className="text-emerald-600 dark:text-emerald-400">
                  Ενεργή
                </span>
              ) : (
                <span className="text-rose-600 dark:text-rose-400">
                  Απενεργοποιημένη
                </span>
              )
            }
          />
          {status.trial_ends_at ? (
            <Row
              label={status.is_trialing ? "Λήξη δοκιμής" : "Έληξε στις"}
              value={formatDate(status.trial_ends_at)}
            />
          ) : null}
          {pending ? (
            <Row
              label="Λήξη πρόσβασης"
              value={
                <span className="text-amber-600 dark:text-amber-400">
                  {formatDate(status.cancel_at ?? null)}
                </span>
              }
            />
          ) : null}
        </dl>

        {canSubscribe ? (
          <div className="mt-6">
            <SubscribeButton
              label={
                status.is_trialing
                  ? "Αναβάθμιση σε συνδρομή"
                  : "Ενεργοποίηση συνδρομής"
              }
              disabled={!status.checkout_enabled}
              disabledReason={
                status.checkout_enabled
                  ? undefined
                  : "Οι πληρωμές δεν έχουν ρυθμιστεί σε αυτόν τον διακομιστή "
                    + "(STRIPE_SECRET_KEY / STRIPE_PRICE_ID)."
              }
            />
            <p className="mt-3 text-center text-[11px] text-slate-400 dark:text-slate-500">
              Ασφαλής πληρωμή μέσω Stripe. Μπορείτε να ακυρώσετε οποτεδήποτε.
            </p>
          </div>
        ) : null}

        {showManage ? (
          <div className={canSubscribe ? "mt-3" : "mt-6"}>
            <ManageBillingButton />
            <p className="mt-3 text-center text-[11px] text-slate-400 dark:text-slate-500">
              Κάρτα πληρωμής, τιμολόγια και αλλαγή πλάνου — μέσω του ασφαλούς
              περιβάλλοντος της Stripe.
            </p>
          </div>
        ) : null}

        {showCancel ? (
          <div className="mt-4 border-t border-slate-100 pt-4 dark:border-slate-800">
            <CancelSubscriptionButton />
            <p className="mt-3 text-center text-[11px] text-slate-400 dark:text-slate-500">
              Η πρόσβαση παραμένει ενεργή έως το τέλος της περιόδου που έχετε
              ήδη πληρώσει.
            </p>
          </div>
        ) : null}
      </Card>
    </div>
  );
}
