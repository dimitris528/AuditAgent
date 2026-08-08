"use client";

import { useEffect, useRef } from "react";
import { clsx } from "@/lib/clsx";

/**
 * A checkbox that can show the indeterminate dash.
 *
 * Its own component only because `indeterminate` is a DOM PROPERTY with no HTML
 * attribute behind it — React cannot set it declaratively, so it has to be
 * written to the node through a ref after every render. A header checkbox
 * without it has to lie: with three of seven rows ticked it would read either
 * "all" or "none", and clicking it would then do the opposite of what it showed.
 */
export function SelectCheckbox({
  checked,
  indeterminate = false,
  onChange,
  label,
  className,
}: {
  checked: boolean;
  indeterminate?: boolean;
  onChange: () => void;
  /** Required: a bare checkbox announces nothing to a screen reader. */
  label: string;
  className?: string;
}) {
  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate && !checked;
  }, [indeterminate, checked]);

  return (
    <input
      ref={ref}
      type="checkbox"
      checked={checked}
      onChange={onChange}
      aria-label={label}
      title={label}
      // Stopped here rather than at each call site: this control sits inside a
      // clickable client card and a clickable table row, and without it every
      // tick would also open the drawer behind it.
      onClick={(e) => e.stopPropagation()}
      className={clsx(
        "h-4 w-4 shrink-0 cursor-pointer rounded border-slate-300 text-indigo-600 accent-indigo-600",
        "focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-1",
        "dark:border-slate-600 dark:bg-slate-800",
        className,
      )}
    />
  );
}
