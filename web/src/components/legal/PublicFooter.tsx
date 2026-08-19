import Link from "next/link";

import { CONTROLLER } from "@/lib/legal";

/**
 * The footer every signed-out page carries.
 *
 * It exists for one legal reason and one practical one. Legally, the Terms and
 * the Privacy Policy have to be reachable from the pages where someone decides
 * to sign up — a link that only appears inside the product is a link nobody
 * sees before they agree to it. Practically, it is where people look for
 * "who actually runs this".
 *
 * Deliberately low-contrast: the auth pages are a single, deliberate visual and
 * a loud footer would compete with the form. It is legible, not prominent.
 */
export function PublicFooter({ className = "" }: { className?: string }) {
  return (
    <footer
      className={`relative z-10 mt-8 flex flex-wrap items-center justify-center gap-x-4 gap-y-1.5 text-[11px] text-slate-400 ${className}`}
    >
      <span>
        © {new Date().getFullYear()} {CONTROLLER.productName}
      </span>
      <Link href="/terms" className="transition hover:text-slate-200 hover:underline">
        Όροι Χρήσης
      </Link>
      <Link href="/privacy" className="transition hover:text-slate-200 hover:underline">
        Πολιτική Απορρήτου
      </Link>
      <Link
        href="/privacy#cookies"
        className="transition hover:text-slate-200 hover:underline"
      >
        Cookies
      </Link>
      <a
        href={`mailto:${CONTROLLER.supportEmail}`}
        className="transition hover:text-slate-200 hover:underline"
      >
        Επικοινωνία
      </a>
    </footer>
  );
}
