"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { PeriodInfo } from "@/lib/types";
import { ClientDrawer } from "./ClientDrawer";

interface DrawerApi {
  openClient: (id: number) => void;
}

const Ctx = createContext<DrawerApi | null>(null);

/**
 * Opens the client drawer from anywhere on the dashboard.
 *
 * The drawer used to be owned by ClientGrid, which meant only a card could
 * open it. The debt alerts need to open it too, and two independent drawer
 * instances would let both be open at once over each other — so the state
 * lives here and there is exactly one drawer on the page.
 */
export function useClientDrawer(): DrawerApi {
  const api = useContext(Ctx);
  if (!api) {
    throw new Error("useClientDrawer must be used inside <ClientDrawerProvider>");
  }
  return api;
}

export function ClientDrawerProvider({
  period,
  vatRates,
  children,
}: {
  period: Pick<PeriodInfo, "year" | "quarter" | "month">;
  vatRates: { value: number; label: string }[];
  children: React.ReactNode;
}) {
  const [openId, setOpenId] = useState<number | null>(null);

  const close = useCallback(() => setOpenId(null), []);
  // Memoised: a fresh object here would re-render every consumer on each
  // render of the dashboard.
  const api = useMemo<DrawerApi>(() => ({ openClient: setOpenId }), []);

  return (
    <Ctx.Provider value={api}>
      {children}
      <ClientDrawer
        clientId={openId}
        period={period}
        vatRates={vatRates}
        onClose={close}
      />
    </Ctx.Provider>
  );
}
