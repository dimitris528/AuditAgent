import { proxyJson } from "@/lib/bff";

// BFF: stop trusting one device, or all of them.
//
// Only `device_id` is forwarded, and only when it is a number. This URL is
// user-visible and the backend scopes every revocation to the caller's own
// devices regardless — but passing a query string through wholesale means
// anything appended to it reaches the backend, and there is no reason to
// allow that.
export async function DELETE(req: Request) {
  const raw = new URL(req.url).searchParams.get("device_id");
  const id = raw && /^\d+$/.test(raw) ? `?device_id=${raw}` : "";
  return proxyJson(`/api/v1/auth/mfa/devices${id}`, {
    method: "DELETE",
    fallback: "Αποτυχία ανάκλησης συσκευής.",
  });
}
