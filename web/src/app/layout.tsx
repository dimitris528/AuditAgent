import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { ThemeProvider, themeInitScript } from "@/components/theme/ThemeProvider";

const inter = Inter({ subsets: ["latin", "greek"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "Λογιστικός Πίνακας Ελέγχου",
  description: "Enterprise accounting dashboard — έσοδα, έξοδα, Φ.Π.Α. & φόρος ανά πελάτη",
};

/**
 * The document itself, and nothing else.
 *
 * The app chrome (header, centred main) used to live here, which is why the
 * signed-out pages were stuck rendering inside it — a full-bleed background
 * cannot exist inside a `max-w-7xl` main. It now lives in `(app)/layout.tsx`,
 * and `(auth)/layout.tsx` provides its own. Both are ROUTE GROUPS: the
 * parentheses are not part of any URL, so /login, /billing and / are exactly
 * where they were.
 */
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="el" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body
        className={`${inter.variable} min-h-full bg-slate-50 font-sans text-slate-900 antialiased dark:bg-slate-950 dark:text-slate-100`}
      >
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
