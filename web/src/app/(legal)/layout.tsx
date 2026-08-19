/**
 * The shell for the public legal documents.
 *
 * A route group of its own rather than a page under (auth): those pages sit on
 * a full-bleed blue mesh that exists to make a login form feel like a product,
 * and it is precisely the wrong background for four thousand words somebody
 * has to actually read. This layout adds nothing but the page — LegalDocument
 * brings its own column, header and footer.
 *
 * "(legal)" is a ROUTE GROUP: the parentheses are not part of any URL, so the
 * pages inside are /terms and /privacy.
 */
export default function LegalLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
