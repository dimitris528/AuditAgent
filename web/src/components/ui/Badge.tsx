import { clsx } from "@/lib/clsx";

type Tone = "neutral" | "revenue" | "expense" | "vat" | "debt" | "warning" | "success";

const TONES: Record<Tone, string> = {
  neutral:
    "bg-slate-100 text-slate-600 ring-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-700",
  revenue:
    "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-400 dark:ring-emerald-500/20",
  expense:
    "bg-rose-50 text-rose-700 ring-rose-200 dark:bg-rose-500/10 dark:text-rose-400 dark:ring-rose-500/20",
  vat:
    "bg-violet-50 text-violet-700 ring-violet-200 dark:bg-violet-500/10 dark:text-violet-400 dark:ring-violet-500/20",
  debt:
    "bg-amber-50 text-amber-700 ring-amber-200 dark:bg-amber-500/10 dark:text-amber-400 dark:ring-amber-500/20",
  warning:
    "bg-rose-100 text-rose-800 ring-rose-300 dark:bg-rose-500/15 dark:text-rose-300 dark:ring-rose-500/30",
  success:
    "bg-emerald-100 text-emerald-800 ring-emerald-300 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-500/30",
};

export function Badge({
  tone = "neutral",
  children,
  className,
  icon,
}: {
  tone?: Tone;
  children: React.ReactNode;
  className?: string;
  icon?: React.ReactNode;
}) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset",
        TONES[tone],
        className,
      )}
    >
      {icon}
      {children}
    </span>
  );
}
