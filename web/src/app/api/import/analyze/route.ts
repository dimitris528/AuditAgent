import { proxyUpload } from "@/lib/bff";

// BFF for step one of a mapped import: read the file's shape, write nothing.
// A static segment, so it resolves ahead of the sibling [kind] route rather
// than being read as an import kind called "analyze".
//
// `kind` travels in the FormData rather than the path, which is what keeps
// this route static and the [kind] one dynamic without the two colliding.
export const maxDuration = 60;

export async function POST(req: Request) {
  return proxyUpload(req, `/api/import/analyze`, {
    fallback: "Αποτυχία ανάγνωσης του αρχείου.",
    timeoutMs: 45_000,
  });
}
