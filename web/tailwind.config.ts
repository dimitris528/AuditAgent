import type { Config } from "tailwindcss";

/**
 * Corporate accounting theme. Dark mode is class-based (`.dark` on <html>),
 * toggled by the ThemeProvider. Semantic brand colors keep revenue/expense/
 * VAT/debt consistent across cards and charts.
 */
const config: Config = {
  darkMode: "class",
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-inter)", "system-ui", "sans-serif"],
      },
      colors: {
        brand: {
          DEFAULT: "#4f46e5", // indigo-600 — primary
          fg: "#6366f1",
        },
        revenue: "#059669", // emerald-600
        expense: "#e11d48", // rose-600
        vat: "#7c3aed", // violet-600
        debt: "#d97706", // amber-600
      },
      boxShadow: {
        card: "0 1px 2px 0 rgb(0 0 0 / 0.04), 0 1px 3px 0 rgb(0 0 0 / 0.06)",
        "card-dark": "0 1px 2px 0 rgb(0 0 0 / 0.4)",
      },
      borderRadius: {
        xl: "0.875rem",
        "2xl": "1.125rem",
      },
    },
  },
  plugins: [],
};

export default config;
