"use client";

import { Moon, Sun } from "lucide-react";
import { useTheme } from "./ThemeProvider";

export function ThemeToggle() {
  const { theme, toggle, mounted } = useTheme();
  const isDark = theme === "dark";
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={isDark ? "Εναλλαγή σε φωτεινό θέμα" : "Εναλλαγή σε σκοτεινό θέμα"}
      title={isDark ? "Φωτεινό θέμα" : "Σκοτεινό θέμα"}
      className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 transition hover:border-slate-300 hover:text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-slate-600 dark:hover:text-white"
    >
      {/* Avoid an icon flash before mount by rendering a neutral glyph. */}
      {!mounted ? (
        <Sun className="h-4 w-4 opacity-40" />
      ) : isDark ? (
        <Sun className="h-4 w-4" />
      ) : (
        <Moon className="h-4 w-4" />
      )}
    </button>
  );
}
