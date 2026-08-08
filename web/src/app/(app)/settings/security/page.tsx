import { SecuritySettings } from "@/components/settings/SecuritySettings";

// Always fresh: 2FA state and the trusted-device list are exactly the things
// nobody should ever be shown a cached copy of.
export const dynamic = "force-dynamic";

export const metadata = { title: "Ασφάλεια" };

export default function SecurityPage() {
  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-4">
        <h1 className="text-xl font-bold tracking-tight text-slate-900 dark:text-white">
          Ασφάλεια
        </h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Ταυτοποίηση δύο παραγόντων και έμπιστες συσκευές
        </p>
      </div>
      {/* The panel loads its own state client-side: everything on it changes
          in response to a button on it, so a server-rendered snapshot would be
          stale by the first click. */}
      <SecuritySettings />
    </div>
  );
}
