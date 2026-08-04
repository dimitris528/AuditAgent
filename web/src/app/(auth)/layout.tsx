import { AuthBackground } from "@/components/auth/AuthBackground";

/**
 * The signed-out shell.
 *
 * No header, no theme toggle, no max-width main: the background is the design
 * here, and it has to reach all four edges. `overflow-hidden` is load-bearing —
 * the drifting mesh in globals.css is deliberately larger than the viewport so
 * its edge never shows, and without this it would scroll the page sideways.
 */
export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="auth-mesh relative flex min-h-screen items-center justify-center overflow-hidden px-4 py-12">
      <AuthBackground />
      <div className="relative z-10 flex w-full justify-center">{children}</div>
    </div>
  );
}
