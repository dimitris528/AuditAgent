"use client";

// The last resort: an error thrown while rendering the ROOT layout, where
// Next's per-route error boundaries no longer exist and nothing of the app's
// chrome is available. It replaces the whole document, so it has to bring its
// own <html> and <body>.
//
// It is also the only place a root-layout crash can be reported from — the
// client SDK's global handlers never see it, because React caught it first.
import * as Sentry from "@sentry/nextjs";
import { useEffect } from "react";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <html lang="el">
      <body className="flex min-h-screen items-center justify-center bg-slate-950 p-6 text-slate-100 antialiased">
        <div className="w-full max-w-md rounded-2xl border border-white/10 bg-slate-900/60 p-6 text-center shadow-2xl">
          <h1 className="text-lg font-bold">Κάτι πήγε στραβά</h1>
          <p className="mt-2 text-sm text-slate-300">
            Παρουσιάστηκε απρόσμενο σφάλμα. Η ομάδα μας ειδοποιήθηκε αυτόματα.
          </p>
          {/* The digest is Next's own id for the error and the only handle a
              user can quote in a support ticket — the message itself is
              withheld in production and would be meaningless anyway. */}
          {error.digest ? (
            <p className="mt-3 font-mono text-[11px] text-slate-500">
              Κωδικός: {error.digest}
            </p>
          ) : null}
          <button
            type="button"
            onClick={reset}
            className="mt-5 inline-flex items-center justify-center rounded-lg bg-gradient-to-r from-indigo-500 to-sky-500 px-4 py-2 text-sm font-semibold text-white transition hover:from-indigo-400 hover:to-sky-400"
          >
            Δοκιμάστε ξανά
          </button>
        </div>
      </body>
    </html>
  );
}
