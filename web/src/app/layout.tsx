import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { cookies } from "next/headers";
import { Calculator } from "lucide-react";
import "./globals.css";
import { SESSION_COOKIE } from "@/lib/constants";
import { ThemeProvider, themeInitScript } from "@/components/theme/ThemeProvider";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { LogoutButton } from "@/components/LogoutButton";

const inter = Inter({ subsets: ["latin", "greek"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "Λογιστικός Πίνακας Ελέγχου",
  description: "Enterprise accounting dashboard — έσοδα, έξοδα, Φ.Π.Α. & φόρος ανά πελάτη",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const authed = Boolean(cookies().get(SESSION_COOKIE)?.value);
  return (
    <html lang="el" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body
        className={`${inter.variable} min-h-full bg-slate-50 font-sans text-slate-900 antialiased dark:bg-slate-950 dark:text-slate-100`}
      >
        <ThemeProvider>
          <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/80 backdrop-blur dark:border-slate-800 dark:bg-slate-950/80">
            <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4 sm:px-6">
              <div className="flex items-center gap-2.5">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600 text-white">
                  <Calculator className="h-5 w-5" />
                </span>
                <div className="leading-tight">
                  <div className="text-sm font-bold tracking-tight text-slate-900 dark:text-white">
                    Λογιστήριο<span className="text-indigo-600 dark:text-indigo-400">Pro</span>
                  </div>
                  <div className="text-[11px] text-slate-500 dark:text-slate-400">
                    Accounting Dashboard
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <ThemeToggle />
                {authed ? <LogoutButton /> : null}
              </div>
            </div>
          </header>
          <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">{children}</main>
        </ThemeProvider>
      </body>
    </html>
  );
}
