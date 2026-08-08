import { proxyJson } from "@/lib/bff";

// BFF: current 2FA state and the list of trusted devices.
export async function GET() {
  return proxyJson(`/api/v1/auth/mfa`, {
    fallback: "Αποτυχία φόρτωσης ρυθμίσεων ασφαλείας.",
  });
}
