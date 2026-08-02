// Tiny classnames helper (no dependency): joins truthy string args.
export function clsx(
  ...parts: Array<string | false | null | undefined>
): string {
  return parts.filter(Boolean).join(" ");
}
