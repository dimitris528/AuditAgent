"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

type Theme = "light" | "dark";

interface ThemeContextValue {
  theme: Theme;
  toggle: () => void;
  mounted: boolean;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);
const STORAGE_KEY = "acc-theme";

function readInitial(): Theme {
  if (typeof window === "undefined") return "light";
  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  // Start "light" for a deterministic SSR render; the inline head script has
  // already applied the real class, and this effect syncs React state on mount.
  const [theme, setTheme] = useState<Theme>("light");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setTheme(readInitial());
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!mounted) return;
    const root = document.documentElement;
    root.classList.toggle("dark", theme === "dark");
    window.localStorage.setItem(STORAGE_KEY, theme);
  }, [theme, mounted]);

  const toggle = useCallback(
    () => setTheme((t) => (t === "dark" ? "light" : "dark")),
    [],
  );

  return (
    <ThemeContext.Provider value={{ theme, toggle, mounted }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}

/**
 * Inline, render-blocking script that applies the stored/system theme class
 * BEFORE first paint, so there is no flash of the wrong theme.
 */
export const themeInitScript = `
(function(){try{
  var t = localStorage.getItem('${STORAGE_KEY}');
  if(t!=='light' && t!=='dark'){
    t = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark':'light';
  }
  document.documentElement.classList.toggle('dark', t==='dark');
}catch(e){}})();
`;
