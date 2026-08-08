"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  KeyRound,
  Laptop,
  Loader2,
  ShieldCheck,
  ShieldOff,
  Trash2,
} from "lucide-react";
import {
  disableMfa,
  enableMfa,
  getMfaStatus,
  revokeTrustedDevices,
  startMfaSetup,
} from "@/lib/api";
import type { MfaSetup, MfaStatus, TrustedDevice } from "@/lib/types";
import { PasswordField } from "@/components/PasswordField";
import { clsx } from "@/lib/clsx";

/**
 * Ασφάλεια — the second factor, and the devices excused from it.
 *
 * Enrolment is deliberately two screens rather than one switch. The server
 * mints a secret and turns NOTHING on; the factor takes effect only once a
 * code minted from that secret comes back. Anyone who closes this tab halfway
 * through is left exactly as they were, which is the difference between a
 * setup flow and a lockout.
 */

const field =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 dark:border-slate-700 dark:bg-slate-800 dark:text-white";
const labelCls = "mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400";

type Feedback = { tone: "ok" | "error"; message: string } | null;

function Alert({ feedback }: { feedback: Feedback }) {
  if (!feedback) return null;
  const ok = feedback.tone === "ok";
  return (
    <div
      role="alert"
      className={clsx(
        "flex items-start gap-2.5 rounded-xl border p-3 text-xs font-medium",
        ok
          ? "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-500/40 dark:bg-emerald-500/10 dark:text-emerald-200"
          : "border-rose-300 bg-rose-50 text-rose-800 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-200",
      )}
    >
      {ok ? (
        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
      ) : (
        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
      )}
      {feedback.message}
    </div>
  );
}

function Section({
  title,
  description,
  icon,
  children,
}: {
  title: string;
  description: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-card dark:border-slate-800 dark:bg-slate-900 dark:shadow-card-dark">
      <div className="mb-4 flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600 dark:bg-indigo-500/10 dark:text-indigo-400">
          {icon}
        </span>
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-slate-900 dark:text-white">
            {title}
          </h2>
          <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
            {description}
          </p>
        </div>
      </div>
      {children}
    </section>
  );
}

function DeviceRow({
  device,
  busy,
  onRevoke,
}: {
  device: TrustedDevice;
  busy: boolean;
  onRevoke: () => void;
}) {
  return (
    <li className="flex items-start justify-between gap-3 py-2.5">
      <div className="min-w-0">
        <div className="truncate text-xs font-medium text-slate-800 dark:text-slate-100">
          {device.label || "Άγνωστη συσκευή"}
        </div>
        <div className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">
          Λήγει {device.expires_at?.slice(0, 10) ?? "—"}
          {device.last_used_at
            ? ` · Τελευταία χρήση ${device.last_used_at.slice(0, 10)}`
            : ""}
        </div>
      </div>
      <button
        type="button"
        onClick={onRevoke}
        disabled={busy}
        className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-300 px-2.5 py-1.5 text-[11px] font-medium text-slate-600 transition hover:border-rose-300 hover:text-rose-600 disabled:opacity-60 dark:border-slate-700 dark:text-slate-300 dark:hover:border-rose-500/40 dark:hover:text-rose-400"
      >
        <Trash2 className="h-3 w-3" />
        Ανάκληση
      </button>
    </li>
  );
}

export function SecuritySettings() {
  const [status, setStatus] = useState<MfaStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<Feedback>(null);

  // Enrolment state, live only while the flow is open.
  const [setup, setSetup] = useState<MfaSetup | null>(null);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirmingDisable, setConfirmingDisable] = useState(false);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      setStatus(await getMfaStatus());
    } catch (err) {
      setFeedback({
        tone: "error",
        message:
          err instanceof Error
            ? err.message
            : "Αποτυχία φόρτωσης ρυθμίσεων ασφαλείας.",
      });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /** Every action goes through here so one place owns busy, feedback and the
   *  reload — a screen where half the buttons forget to refresh the list is
   *  how a revoked device appears to still be trusted. */
  async function run(action: () => Promise<string>) {
    setBusy(true);
    setFeedback(null);
    try {
      const message = await action();
      await load(true);
      setFeedback({ tone: "ok", message });
    } catch (err) {
      setFeedback({
        tone: "error",
        message: err instanceof Error ? err.message : "Η ενέργεια απέτυχε.",
      });
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 p-10 text-sm text-slate-500 dark:text-slate-400">
        <Loader2 className="h-4 w-4 animate-spin" />
        Φόρτωση ρυθμίσεων…
      </div>
    );
  }

  const enabled = status?.enabled === true;
  const devices = status?.devices ?? [];

  return (
    <div className="space-y-4">
      <Alert feedback={feedback} />

      <Section
        title="Ταυτοποίηση δύο παραγόντων (2FA)"
        description="Ένας εξαψήφιος κωδικός από την εφαρμογή authenticator, επιπλέον του κωδικού πρόσβασης."
        icon={enabled ? <ShieldCheck className="h-5 w-5" /> : <ShieldOff className="h-5 w-5" />}
      >
        <div className="mb-4 flex items-center gap-2">
          <span
            className={clsx(
              "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold",
              enabled
                ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300"
                : "bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-300",
            )}
          >
            {enabled ? "Ενεργή" : "Ανενεργή"}
          </span>
          {status?.pending && !enabled ? (
            <span className="text-[11px] text-amber-600 dark:text-amber-400">
              Η ρύθμιση ξεκίνησε αλλά δεν ολοκληρώθηκε.
            </span>
          ) : null}
        </div>

        {status && !status.available ? (
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Η ταυτοποίηση δύο παραγόντων δεν είναι διαθέσιμη σε αυτόν τον
            διακομιστή.
          </p>
        ) : enabled ? (
          // --- Enabled: offer to turn it off, with the password ------------
          confirmingDisable ? (
            <form
              className="space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                void run(async () => {
                  await disableMfa(password);
                  setPassword("");
                  setConfirmingDisable(false);
                  return "Η ταυτοποίηση δύο παραγόντων απενεργοποιήθηκε.";
                });
              }}
            >
              <p className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200">
                Η απενεργοποίηση αφαιρεί και όλες τις έμπιστες συσκευές.
                Επιβεβαιώστε με τον κωδικό πρόσβασής σας.
              </p>
              <PasswordField
                id="mfa-disable-password"
                label="Κωδικός πρόσβασης"
                className={field}
                labelClassName={labelCls}
                value={password}
                onChange={setPassword}
                autoComplete="current-password"
              />
              <div className="flex items-center gap-2">
                <button
                  type="submit"
                  disabled={busy || password.length === 0}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-rose-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-rose-500 disabled:opacity-60"
                >
                  {busy ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <ShieldOff className="h-4 w-4" />
                  )}
                  Απενεργοποίηση 2FA
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setConfirmingDisable(false);
                    setPassword("");
                  }}
                  className="rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium text-slate-600 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                >
                  Άκυρο
                </button>
              </div>
            </form>
          ) : (
            <button
              type="button"
              onClick={() => {
                setFeedback(null);
                setConfirmingDisable(true);
              }}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium text-slate-600 transition hover:border-rose-300 hover:text-rose-600 dark:border-slate-700 dark:text-slate-300 dark:hover:border-rose-500/40 dark:hover:text-rose-400"
            >
              Απενεργοποίηση 2FA
            </button>
          )
        ) : setup ? (
          // --- Enrolment step two: scan, then prove it ---------------------
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                await enableMfa(code);
                setSetup(null);
                setCode("");
                return "Η ταυτοποίηση δύο παραγόντων ενεργοποιήθηκε.";
              });
            }}
          >
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
              {setup.qr_svg ? (
                <div
                  className="shrink-0 overflow-hidden rounded-xl border border-slate-200 bg-white p-2 dark:border-slate-700"
                  // Server-rendered SVG from our own backend, built from the
                  // otpauth URI — no user input reaches it.
                  dangerouslySetInnerHTML={{ __html: setup.qr_svg }}
                />
              ) : null}
              <div className="min-w-0 flex-1 space-y-2">
                <p className="text-xs text-slate-600 dark:text-slate-300">
                  Σαρώστε τον κωδικό με το Google Authenticator, το Authy ή
                  παρόμοια εφαρμογή.
                </p>
                <div>
                  <div className={labelCls}>
                    Ή καταχωρίστε τον κωδικό χειροκίνητα
                  </div>
                  <code className="block break-all rounded-lg bg-slate-100 px-2.5 py-2 font-mono text-[11px] text-slate-700 dark:bg-slate-800 dark:text-slate-200">
                    {setup.secret}
                  </code>
                </div>
              </div>
            </div>

            <div>
              <label className={labelCls} htmlFor="mfa-enable-code">
                Κωδικός επαλήθευσης από την εφαρμογή
              </label>
              <input
                id="mfa-enable-code"
                className={`${field} max-w-[12rem] text-center tracking-[0.4em]`}
                value={code}
                onChange={(e) =>
                  setCode(e.target.value.replace(/\D/g, "").slice(0, 6))
                }
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="000000"
                autoFocus
              />
            </div>

            <div className="flex items-center gap-2">
              <button
                type="submit"
                disabled={busy || code.length < 6}
                className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-indigo-500 disabled:opacity-60"
              >
                {busy ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <ShieldCheck className="h-4 w-4" />
                )}
                Ενεργοποίηση
              </button>
              <button
                type="button"
                onClick={() => {
                  setSetup(null);
                  setCode("");
                }}
                className="rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium text-slate-600 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
              >
                Άκυρο
              </button>
            </div>
          </form>
        ) : (
          // --- Enrolment step one ------------------------------------------
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                setSetup(await startMfaSetup());
                return "Σαρώστε τον κωδικό QR και καταχωρίστε τον 6ψήφιο κωδικό.";
              })
            }
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-indigo-500 disabled:opacity-60"
          >
            {busy ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <KeyRound className="h-4 w-4" />
            )}
            Ρύθμιση 2FA
          </button>
        )}
      </Section>

      <Section
        title="Έμπιστες συσκευές"
        description={`Browsers που δεν ζητούν κωδικό επαλήθευσης για ${status?.trust_days ?? 30} ημέρες.`}
        icon={<Laptop className="h-5 w-5" />}
      >
        {devices.length === 0 ? (
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Καμία έμπιστη συσκευή. Η επιλογή εμφανίζεται κατά τη σύνδεση με
            κωδικό επαλήθευσης.
          </p>
        ) : (
          <>
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {devices.map((device) => (
                <DeviceRow
                  key={device.id}
                  device={device}
                  busy={busy}
                  onRevoke={() =>
                    void run(async () => {
                      await revokeTrustedDevices(device.id);
                      return "Η συσκευή δεν είναι πλέον έμπιστη.";
                    })
                  }
                />
              ))}
            </ul>
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const result = await revokeTrustedDevices();
                  return `Ανακλήθηκαν ${result.revoked} συσκευές.`;
                })
              }
              className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-2 text-xs font-medium text-slate-600 transition hover:border-rose-300 hover:text-rose-600 disabled:opacity-60 dark:border-slate-700 dark:text-slate-300 dark:hover:border-rose-500/40 dark:hover:text-rose-400"
            >
              <Trash2 className="h-3.5 w-3.5" />
              Ανάκληση όλων των συσκευών
            </button>
          </>
        )}
      </Section>
    </div>
  );
}
