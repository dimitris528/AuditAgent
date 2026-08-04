"use client";

import { useEffect, useRef, useState } from "react";
import { AlertCircle, Camera, Loader2, ScanLine, X } from "lucide-react";
import { scanDocument } from "@/lib/api";
import type { ScanResult } from "@/lib/types";
import { clsx } from "@/lib/clsx";

/** Mirrors server/ocr.SUPPORTED_TYPES. */
const ACCEPT = "application/pdf,image/jpeg,image/png,image/webp";
const MAX_BYTES = 12 * 1024 * 1024;

interface Props {
  onScanned: (result: ScanResult) => void;
  disabled?: boolean;
}

/** getUserMedia needs a secure context, so it is absent over plain HTTP and in
 *  older in-app browsers. Checked at click time rather than on render, because
 *  it is not knowable during SSR. */
function hasCamera(): boolean {
  return (
    typeof navigator !== "undefined" &&
    typeof navigator.mediaDevices?.getUserMedia === "function"
  );
}

/**
 * Live camera capture. Falls back to the OS camera app (an `<input capture>`)
 * when getUserMedia is unavailable — which is the normal case on a phone
 * browsing over plain HTTP.
 */
function CameraCapture({
  onCapture,
  onClose,
}: {
  onCapture: (file: File) => void;
  onClose: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    navigator.mediaDevices
      .getUserMedia({
        // The rear camera on a phone; ideal rather than exact so a laptop with
        // only a front camera still works.
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 } },
      })
      .then((stream) => {
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          void videoRef.current.play().catch(() => undefined);
        }
        setReady(true);
      })
      .catch(() => {
        if (!cancelled) {
          setError(
            "Δεν ήταν δυνατή η πρόσβαση στην κάμερα. Ελέγξτε τα δικαιώματα του browser.",
          );
        }
      });

    return () => {
      // Releasing every track is what turns the camera light off; leaving the
      // stream open holds the device until the tab is closed.
      cancelled = true;
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    };
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  function shoot() {
    const video = videoRef.current;
    if (!video || !video.videoWidth) return;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob(
      (blob) => {
        if (!blob) return;
        onCapture(
          new File([blob], `parastatiko-${Date.now()}.jpg`, {
            type: "image/jpeg",
          }),
        );
      },
      "image/jpeg",
      // High enough that small print stays legible, low enough that a 1920px
      // frame lands well under the upload cap.
      0.92,
    );
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex flex-col bg-slate-950/95"
      role="dialog"
      aria-modal="true"
      aria-label="Λήψη φωτογραφίας παραστατικού"
    >
      <div className="flex items-center justify-between p-4 text-white">
        <span className="text-sm font-medium">Φωτογραφία παραστατικού</span>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg p-2 text-slate-300 transition hover:bg-white/10 hover:text-white"
          aria-label="Κλείσιμο"
        >
          <X className="h-5 w-5" />
        </button>
      </div>

      <div className="flex flex-1 items-center justify-center overflow-hidden px-4">
        {error ? (
          <p className="flex max-w-sm items-start gap-2 text-center text-sm text-rose-300">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            {error}
          </p>
        ) : (
          <video
            ref={videoRef}
            playsInline
            muted
            className="max-h-full max-w-full rounded-xl object-contain"
          />
        )}
      </div>

      <div className="flex items-center justify-center gap-3 p-6">
        <button
          type="button"
          onClick={shoot}
          disabled={!ready || !!error}
          className="inline-flex items-center gap-2 rounded-full bg-white px-6 py-3 text-sm font-semibold text-slate-900 transition hover:bg-slate-100 disabled:opacity-50"
        >
          <Camera className="h-4 w-4" />
          Λήψη
        </button>
      </div>
    </div>
  );
}

/**
 * "Σκανάρισμα / Ανέβασμα Τιμολογίου" — upload a PDF/JPG/PNG or photograph the
 * document, and hand the extraction back for the form to pre-fill.
 *
 * Nothing is saved here: the result goes into the form for the user to check
 * before it becomes a transaction.
 */
export function InvoiceScanner({ onScanned, disabled }: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const captureRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [camera, setCamera] = useState(false);

  async function send(file: File) {
    setError("");
    if (file.size > MAX_BYTES) {
      setError(
        `Το αρχείο ξεπερνά τα ${Math.round(MAX_BYTES / 1024 / 1024)} MB.`,
      );
      return;
    }
    setBusy(true);
    try {
      onScanned(await scanDocument(file));
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Αποτυχία σάρωσης παραστατικού.",
      );
    } finally {
      setBusy(false);
    }
  }

  function pick(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    // Reset so re-picking the SAME file fires change again.
    e.target.value = "";
    if (file) void send(file);
  }

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={disabled || busy}
          className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg border border-dashed border-indigo-300 bg-indigo-50/50 px-3 py-2.5 text-sm font-medium text-indigo-700 transition hover:border-indigo-400 hover:bg-indigo-50 disabled:opacity-60 dark:border-indigo-500/40 dark:bg-indigo-500/5 dark:text-indigo-300 dark:hover:bg-indigo-500/10"
        >
          {busy ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Ανάγνωση παραστατικού…
            </>
          ) : (
            <>
              <ScanLine className="h-4 w-4" />
              Σκανάρισμα / Ανέβασμα Τιμολογίου
            </>
          )}
        </button>
        <button
          type="button"
          onClick={() => (hasCamera() ? setCamera(true) : captureRef.current?.click())}
          disabled={disabled || busy}
          title="Φωτογραφία με κάμερα"
          aria-label="Φωτογραφία με κάμερα"
          className="inline-flex items-center justify-center rounded-lg border border-dashed border-indigo-300 bg-indigo-50/50 px-3 py-2.5 text-indigo-700 transition hover:border-indigo-400 hover:bg-indigo-50 disabled:opacity-60 dark:border-indigo-500/40 dark:bg-indigo-500/5 dark:text-indigo-300 dark:hover:bg-indigo-500/10"
        >
          <Camera className="h-4 w-4" />
        </button>
      </div>

      <input
        ref={fileRef}
        type="file"
        accept={ACCEPT}
        onChange={pick}
        className="hidden"
      />
      {/* Fallback path: hands off to the OS camera app instead of getUserMedia. */}
      <input
        ref={captureRef}
        type="file"
        accept="image/*"
        capture="environment"
        onChange={pick}
        className="hidden"
      />

      {error ? (
        <p className="flex items-start gap-1.5 text-xs text-rose-600 dark:text-rose-400">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {error}
        </p>
      ) : (
        <p className={clsx("text-[11px] text-slate-500 dark:text-slate-400")}>
          PDF, JPG ή PNG. Τα στοιχεία συμπληρώνονται αυτόματα για έλεγχο πριν την
          αποθήκευση.
        </p>
      )}

      {camera ? (
        <CameraCapture
          onClose={() => setCamera(false)}
          onCapture={(file) => {
            setCamera(false);
            void send(file);
          }}
        />
      ) : null}
    </div>
  );
}
