import { AuthBackground } from "@/components/auth/AuthBackground";
import { CookieNotice } from "@/components/legal/CookieNotice";
import { PublicFooter } from "@/components/legal/PublicFooter";

/**
 * The signed-out shell.
 *
 * No header, no theme toggle, no max-width main: the background is the design
 * here, and it has to reach all four edges. `overflow-hidden` is load-bearing —
 * the drifting mesh in globals.css is deliberately larger than the viewport so
 * its edge never shows, and without this it would scroll the page sideways.
 *
 * The footer and the cookie notice live HERE rather than on each page: the
 * links to the Terms and the Privacy Policy have to be present on the pages
 * where somebody decides to sign up, and putting them in the layout is what
 * guarantees a future auth page cannot ship without them.
 *
 * `py-12` became `pb-28` at the bottom so the notice, which is fixed to the
 * viewport, cannot cover the submit button of a form on a short screen.
 */
export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="auth-mesh relative flex min-h-screen flex-col items-center justify-center overflow-hidden px-4 pb-28 pt-12">
      <AuthBackground />
      <div className="relative z-10 flex w-full flex-col items-center justify-center">
        {children}
        <PublicFooter />
      </div>
      <CookieNotice />
    </div>
  );
}
