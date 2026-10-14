"use client";

import { useState } from "react";
import { ArrowsClockwise } from "@phosphor-icons/react";
import { Button } from "../ui";

/** Sends all orders and enquiries to the TechDeva dashboard (first connection, or after an outage). */
export function PlatformSync({ configured }: { configured: boolean }) {
  const [state, setState] = useState<{ busy: boolean; msg?: string; error?: boolean }>({ busy: false });
  if (!configured) {
    return (
      <p className="text-sm text-ash-400">
        TechDeva dashboard not connected. Set PLATFORM_WEBHOOK_URL and PLATFORM_WEBHOOK_SECRET to send orders there live.
      </p>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button
        variant="ghost"
        disabled={state.busy}
        onClick={async () => {
          setState({ busy: true });
          const res = await fetch("/api/admin/platform-sync", { method: "POST" });
          const d = await res.json().catch(() => ({}));
          setState(
            res.ok
              ? { busy: false, msg: `Sent ${d.sent}${d.failed ? `, ${d.failed} failed (try again)` : ""}.`, error: d.failed > 0 }
              : { busy: false, msg: d.error ?? "Sync failed", error: true },
          );
        }}
      >
        <ArrowsClockwise size={16} className={state.busy ? "animate-spin" : ""} /> {state.busy ? "Syncing..." : "Sync to TechDeva"}
      </Button>
      {state.msg && <span className={`text-sm ${state.error ? "text-danger" : "text-ok"}`}>{state.msg}</span>}
      <span className="text-xs text-ash-400">New orders and status changes are sent automatically.</span>
    </div>
  );
}
