import { clsx } from "@/lib/clsx";

export function Card({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={clsx(
        "rounded-2xl border border-slate-200 bg-white shadow-card transition-colors",
        "dark:border-slate-800 dark:bg-slate-900 dark:shadow-card-dark",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function CardHeader({
  title,
  subtitle,
  icon,
  right,
}: {
  title: string;
  subtitle?: string;
  icon?: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4 dark:border-slate-800">
      <div className="flex items-center gap-2.5 min-w-0">
        {icon ? (
          <span className="text-slate-400 dark:text-slate-500">{icon}</span>
        ) : null}
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">
            {title}
          </h3>
          {subtitle ? (
            <p className="truncate text-xs text-slate-500 dark:text-slate-400">
              {subtitle}
            </p>
          ) : null}
        </div>
      </div>
      {right}
    </div>
  );
}
