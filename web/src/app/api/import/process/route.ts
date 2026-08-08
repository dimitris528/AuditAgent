import { proxyUpload } from "@/lib/bff";

// BFF for step two: import the file using the mapping the user confirmed. The
// mapping rides in the FormData as JSON alongside the file, because the file
// makes this a multipart request either way.
//
// A few thousand rows is a few thousand duplicate-checked inserts, well past
// the default serverless timeout on some hosts.
export const maxDuration = 120;

export async function POST(req: Request) {
  return proxyUpload(req, `/api/import/process`, {
    fallback: "Αποτυχία εισαγωγής δεδομένων.",
  });
}
