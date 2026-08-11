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
      // rounded-xl, not 2xl: at dashboard density the panels sit close enough
      // that a 16px corner radius eats visible plot area and softens the grid
      // into something that reads like a phone app rather than a ledger.
      className={clsx(
        "rounded-xl border border-slate-200 bg-white shadow-card transition-colors",
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
    // px-3.5/py-2.5, down from px-5/py-4: a header band that costs 60px is
    // affordable once per page and wasteful six times over. The border stays —
    // at this spacing the rule is what separates the header from the panel.
    <div className="flex items-start justify-between gap-2 border-b border-slate-100 px-3.5 py-2.5 dark:border-slate-800">
      <div className="flex items-center gap-2 min-w-0">
        {icon ? (
          <span className="text-slate-400 dark:text-slate-500">{icon}</span>
        ) : null}
        <div className="min-w-0">
          <h3 className="truncate text-[13px] font-semibold leading-tight text-slate-900 dark:text-slate-100">
            {title}
          </h3>
          {subtitle ? (
            <p className="truncate text-[11px] leading-tight text-slate-500 dark:text-slate-400">
              {subtitle}
            </p>
          ) : null}
        </div>
      </div>
      {right}
    </div>
  );
}
